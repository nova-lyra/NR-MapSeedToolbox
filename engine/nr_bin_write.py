# -*- coding: utf-8 -*-
r"""
nr_bin_write.py —— 把 CSV 的改动**直接写回 regulation.bin**（覆盖原文件，像 Smithbox 那样）
================================================================================
原理（与 nr_bin_unpack.py 完全对称）：
  ① 读：AES-256-CBC 解密 → DCX 解容器(ZSTD) → BND4 拿到每个 .param 的原始字节
  ② 改：按 CSV 的表名/行 ID/列名，把值**原地写进**该 .param 的字节里
       ★ 我们只改"已有行的值"、绝不加行 ⇒ .param 长度不变 ⇒ BND4 内部所有偏移/哈希表**天然保持有效**
  ③ 写：新建 DCX（只换压缩数据 + 更新 DCS 两个 size）→ AES 加密（NR 密钥，IV = 16 个 0，无填充）
       → 覆盖原 bin（先自动备份 .bak_时间戳）

用法：
  python nr_bin_write.py info  <regulation.bin>                          # 看这个 bin 有哪些表
  python nr_bin_write.py apply <regulation.bin> <表名> <CSV> [...] [--dry-run]
      # 多份 CSV 一起写：apply <bin> LotResultSmallBaseAndSpot a.csv LotResultMapPatternFlag b.csv
      # --dry-run 只报"会改哪些值"，不写文件
"""
import os
import re
import sys
import csv
import zlib
import shutil
import struct
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from nr_bin_unpack import (NR_KEY, _aes_cbc_decrypt, dcx_unpack, bnd4_parse,
                           read_name, _be32, _zstd_decompress)
import param_lookup as pl

FMT = {"s8": "b", "u8": "B", "dummy8": "B", "s16": "h", "u16": "H",
       "s32": "i", "u32": "I", "f32": "f", "dummy32": "I", "u32": "I"}


# ---------------------------------------------------------------- 读 bin（复用解包器）
def read_bin(path):
    raw = open(path, "rb").read()
    iv, ct = raw[:16], raw[16:]
    pad = (-len(ct)) % 16
    dec = _aes_cbc_decrypt(NR_KEY, iv, ct + b"\x00" * pad)
    inner = dcx_unpack(dec)
    info, entries = bnd4_parse(inner)
    return raw, dec, inner, info, entries


def _zstd_compress(inner_new):
    """按 **FromSoft 原版规格**压缩：
       ★ window_log = 16（64KB 窗口）+ 不写 Frame_Content_Size
         ⇒ 帧头必须是 `28 b5 2f fd 00 30`（与原版 regulation.bin 逐字节一致）。
       原版用大窗口/写长度（python-zstandard 默认 `28b52ffd80…`）时，游戏按 64KB 缓冲解压会失败 ⇒ 启动崩。
    """
    try:
        import zstandard as zs
        p = zs.ZstdCompressionParameters.from_level(15, window_log=16, write_content_size=0)
        comp = zs.ZstdCompressor(compression_params=p).compress(inner_new)
        if comp[:6] != b"\x28\xb5\x2f\xfd\x00\x30":
            # 兜底：标准帧头要求（至少保证不写 content size）
            p = zs.ZstdCompressionParameters.from_level(15, write_content_size=0)
            comp = zs.ZstdCompressor(compression_params=p).compress(inner_new)
        return comp
    except Exception:
        pass
    try:
        from compression import zstd as cz          # Python 3.14+ 内置
        return cz.compress(inner_new, level=15)
    except Exception:
        raise RuntimeError("需要 zstandard（打包时已 --collect-all zstandard）")


def dcx_wrap(dec_template, inner_new):
    """按原文件的 DCX/DCP/DCA 结构，重新打包 inner_new（只更新尺寸），返回新 DCX 明文"""
    dcs = _be32(dec_template, 8)
    dcp = _be32(dec_template, 0xC)
    # DCA 块（实测在 dcp+0x20，8 字节）后就是压缩流
    pay_start = dcp + 0x20
    if dec_template[pay_start:pay_start + 4] != b"DCA\x00":
        raise RuntimeError("DCX 结构和预期不符（找不到 DCA 块）")
    pay_start += 8
    comp = _zstd_compress(inner_new)
    head = bytearray(dec_template[:pay_start])                 # 复用原头（含 DCS/DCP/DCA 各字段）
    struct.pack_into(">i", head, dcs + 4, len(inner_new))      # DCS.uncompressedSize
    struct.pack_into(">i", head, dcs + 8, len(comp))           # DCS.compressedSize
    return bytes(head) + comp


def aes_wrap(plain):
    """AES-256-CBC 加密（NR 密钥，文件头 16 字节 = IV = 全 0）。
    ★ 填充方式与原版一致：① 先补 0 到 16 字节对齐 ② 再用 PKCS#7 补满一整块
      （原版密文末尾那 16 个 `0x10` 就是这一步 —— 少了它，游戏解填充会不认）
    """
    from Crypto.Cipher import AES
    body = plain + b"\x00" * ((-len(plain)) % 16)
    pad = 16 - (len(body) % 16)
    if pad == 0:
        pad = 16
    body += bytes([pad]) * pad
    return b"\x00" * 16 + AES.new(NR_KEY, AES.MODE_CBC, b"\x00" * 16).encrypt(body)


# ---------------------------------------------------------------- 改 .param 字节
def _bitpos_map(table):
    """bit 字段 → (所在字节偏移, 字节内起始位, 位数)"""
    flds = pl.def_fields(table)
    out, o, bitbuf = {}, 0, 0
    for name, kind, nbits, typ, arr in flds:
        if kind == "bit":
            if bitbuf + nbits > 8:
                o += 1
                bitbuf = 0
            out[name] = (o, bitbuf, nbits)
            bitbuf += nbits
        else:
            if bitbuf:
                o += 1
                bitbuf = 0
            sz = pl.SIZE.get(typ, 4) * (arr if kind == "arr" else 1)
            o += sz
    return out


def _parse_val(s):
    s = (s or "").strip()
    if s.startswith("[") and s.endswith("]"):
        return [x for x in s[1:-1].split("|")]
    return s


def apply_csv(buf, table, csv_path, dry=False):
    """把 CSV 的值原地写进 param 字节 buf（bytearray）。返回 [(行id, 列名, 旧值, 新值), ...]"""
    lay, rowsize = pl.layout(pl.def_fields(table))
    laymap = {n: (off, sz, kind, typ) for (n, off, sz, kind, typ) in lay}
    bits = _bitpos_map(table)
    rc = struct.unpack_from("<H", buf, 0x0A)[0]
    data_off = struct.unpack_from("<I", buf, 0x30)[0]
    total = struct.unpack_from("<I", buf, 0x00)[0]
    if rc <= 0 or (total - data_off) % rc or 0x40 + rc * 24 != data_off:
        raise RuntimeError("param 结构校验失败")
    rs = (total - data_off) // rc
    idx = {}
    for i in range(rc):
        base = 0x40 + i * 24
        rid = struct.unpack_from("<I", buf, base)[0]
        idx[rid] = struct.unpack_from("<I", buf, base + 8)[0]      # 绝对偏移
    rep = []
    lines = open(csv_path, encoding="utf-8-sig").read().splitlines()
    head = [h.strip() for h in next(csv.reader([lines[0]]))]
    if head[:2] != ["ID", "Name"]:
        raise RuntimeError("CSV 表头不对（应为 ID,Name,<字段…>）：%s" % head[:3])
    nrow = 0
    for ln in lines[1:]:
        if not ln.strip():
            continue
        vals = next(csv.reader([ln]))
        rid = int(vals[0])
        if rid not in idx:
            rep.append((rid, "(整行)", "", "⚠ 这个行号不在 %s 里，跳过" % table))
            continue
        nrow += 1
        base = idx[rid]
        for k, col in enumerate(head):
            if k < 2 or k >= len(vals):
                continue
            col = col.strip()
            new = vals[k]
            if col in bits:                                        # 位字段
                off, start, nbits = bits[col]
                p = base + off
                old = (buf[p] >> start) & ((1 << nbits) - 1)
                try:
                    nv = int(new or 0)
                except Exception:
                    continue
                if old != nv:
                    rep.append((rid, col, old, nv))
                    if not dry:
                        mask = ((1 << nbits) - 1) << start
                        buf[p] = (buf[p] & ~mask) | ((nv << start) & mask)
                continue
            if col not in laymap:                                  # CSV 里多出来的列（如 unknown_0x12）
                continue
            off, sz, kind, typ = laymap[col]
            p = base + off
            f = FMT.get(typ)
            if not f:
                continue
            if kind == "arr":
                parts = _parse_val(new)
                if not isinstance(parts, list):
                    parts = [parts]
                for j, s in enumerate(parts):
                    if off + (j + 1) * pl.SIZE.get(typ, 4) > rs:
                        break
                    v = float(s) if typ == "f32" else int(float(s or 0))
                    old = struct.unpack_from("<" + f, buf, p + j * pl.SIZE.get(typ, 4))[0]
                    if old != v:
                        rep.append((rid, "%s[%d]" % (col, j), old, v))
                        if not dry:
                            struct.pack_into("<" + f, buf, p + j * pl.SIZE.get(typ, 4), v)
            else:
                try:
                    v = float(new) if typ == "f32" else int(float(new or 0))
                except Exception:
                    continue
                if off + sz > rs:
                    continue
                old = struct.unpack_from("<" + f, buf, p)[0]
                if old != v:
                    rep.append((rid, col, old, v))
                    if not dry:
                        struct.pack_into("<" + f, buf, p, v)
    return rep, nrow


# ---------------------------------------------------------------- 主流程
def apply_to_bin(bin_path, jobs, dry=False):
    """jobs = [(表名, csv路径), ...]；改完覆盖写回 bin_path（先备份）。返回报告文本"""
    raw, dec, inner, info, entries = read_bin(bin_path)
    entmap = {n: i for i, (n, d, m) in enumerate(entries)}
    buf_inner = bytearray(inner)
    # BND4 里每个条目的数据是直接可定位的（dataOffset 已在解析时拿到）
    hdrs = {}
    fhs = info["fileHeaderSize"]
    for i, (n, d, m) in enumerate(entries):
        p = 0x40 + i * fhs
        hdrs[n] = (struct.unpack_from("<I", inner, p + 0x18)[0], m["csize"])     # (dataOffset, size)
    lines = []
    total_changes = 0
    for table, csvp in jobs:
        fname = table + ".param"
        if fname not in hdrs:
            lines.append("  !! 这个 bin 里没有 %s" % fname)
            continue
        doff, csize = hdrs[fname]
        seg = bytearray(buf_inner[doff:doff + csize])
        rep, nrow = apply_csv(seg, table, csvp, dry=dry)
        real = [r for r in rep if r[1] != "(整行)"]
        total_changes += len(real)
        lines.append("  %-28s CSV %d 行 → 改动 %d 处%s" %
                     (table, nrow, len(real), "（试运行，没写）" if dry else ""))
        for rid, col, old, new in real[:8]:
            lines.append("      行%-8s %-16s %s → %s" % (rid, col, old, new))
        if len(real) > 8:
            lines.append("      … 其余 %d 处" % (len(real) - 8))
        if not dry:
            buf_inner[doff:doff + csize] = seg
    if dry or total_changes == 0:
        return "\n".join(lines) + ("\n  （没有需要写回的改动）" if not dry else "")
    new_dcx = dcx_wrap(dec, bytes(buf_inner))
    out_bytes = aes_wrap(new_dcx)
    bak = bin_path + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(bin_path, bak)
    open(bin_path, "wb").write(out_bytes)
    lines.append("  ✅ 已写回：%s（%d B → %d B）" % (bin_path, len(raw), len(out_bytes)))
    lines.append("     改前已备份：%s" % bak)
    return "\n".join(lines)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == "info":
        raw, dec, inner, info, entries = read_bin(a[1])
        print("bin =", a[1], len(raw), "B")
        print("BND4：%d 个条目；fmt=0x%02X version=%s" % (info["fileCount"], info["format"], info["version"]))
        for n, d, m in entries[:15]:
            print("   %-42s %d B" % (n, len(d)))
        print("   … 共 %d 个" % len(entries))
        return 0
    if a[0] == "apply":
        dry = "--dry-run" in a
        args = [x for x in a[1:] if not x.startswith("--")]
        binp = args[0]
        rest = args[1:]
        if len(rest) % 2:
            print("!! 表名/CSV 必须成对"); return 1
        jobs = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
        print("写回：", binp)
        for t, c in jobs:
            print("   %-28s ← %s" % (t, c))
        print(apply_to_bin(binp, jobs, dry=dry))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
