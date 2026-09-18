# -*- coding: utf-8 -*-
r"""
nr_bin_unpack.py —— 自己的 NR regulation.bin 解包器（不使用 WitchyBND / 任何第三方工具）
  ① AES-256-CBC 解密（固定密钥，IV = 文件前 16 字节）
  ② DCX 解容器（DCS/DCP，支持 ZSTD / DFLT(zlib) / KRAK(oodle 不支持时明确报错)）
  ③ BND4 解析 → 落地成 <out>/<name>  （.param 等）
来源：soulsmods/SoulsFormatsNEXT 的 RegulationDecryptor（AES 密钥与 CBC 写法）+ BND4 读取逻辑。
用法：
  python nr_bin_unpack.py <regulation.bin> <输出目录> [--list]
"""
import os, sys, struct, zlib

# ---------------------------------------------------------------- AES
NR_KEY = bytes([0x9A,0x8E,0xE9,0x0C,0x4C,0x01,0xA4,0x31,0x68,0xA1,0x7D,0x9D,0x75,0xE4,0xA7,0xD0,
                0x21,0x07,0xEB,0xCF,0x43,0xD5,0xAC,0xB0,0x55,0x4F,0x94,0x16,0x01,0xB5,0x79,0x18])

def _aes_cbc_decrypt(key, iv, data):
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(data) + d.finalize()
    except Exception:
        pass
    raise RuntimeError("需要 AES 库（pycryptodome 或 cryptography）")

# ---------------------------------------------------------------- ZSTD
def _zstd_decompress(data, out_size):
    try:
        import zstandard as zs
        return zs.ZstdDecompressor().decompress(data, max_output_size=out_size or 0)
    except Exception:
        pass
    try:
        from compression import zstd as cz          # Python 3.14+
        return cz.decompress(data)
    except Exception:
        pass
    # 兜底：libzstd.dll（若同目录/系统有）
    import ctypes, ctypes.util
    for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "libzstd.dll"),
                 ctypes.util.find_library("zstd"), "libzstd.dll"):
        if not cand:
            continue
        try:
            dll = ctypes.CDLL(cand)
            dll.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
            dll.ZSTD_decompress.restype = ctypes.c_size_t
            n = dll.ZSTD_getFrameContentSize(data, len(data))
            cap = int(n) if 0 < n < (1 << 31) else (out_size or 0)
            buf = ctypes.create_string_buffer(cap)
            r = dll.ZSTD_decompress(buf, cap, data, len(data))
            if r:
                return buf.raw[:r]
        except Exception:
            continue
    raise RuntimeError("需要 zstd 解压（zstandard / Python3.14 内置 / libzstd.dll）")

# ---------------------------------------------------------------- DCX
def _be32(d, o): return struct.unpack_from(">i", d, o)[0]

def dcx_unpack(d):
    """FromSoft DCX：返回解压后的字节"""
    if d[:4] != b"DCX\x00":
        return d
    dcs = _be32(d, 8); dcp = _be32(d, 0xC)
    assert d[dcs:dcs+4] == b"DCS\x00", "DCX: 缺少 DCS 块"
    unc = _be32(d, dcs+4); comp = _be32(d, dcs+8)
    assert d[dcp:dcp+4] == b"DCP\x00", "DCX: 缺少 DCP 块"
    ctype = d[dcp+4:dcp+8]
    # ⚠️ 实测（NR 2026-09-18）：DCP 块后面还有一个 8 字节的 DCA 块，压缩流从它之后开始。
    #   稳妥做法 = 按压缩流的魔数定位（ZSTD 28 B5 2F FD / zlib 78 9C|01|DA），拿不到再退回"跳过 DCA"。
    start = None
    for magic in (b"\x28\xb5\x2f\xfd", b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
        i = d.find(magic, dcp + 0x10)
        if i >= 0:
            start = i
            break
    if start is None:
        start = dcp + 0x20 + (8 if d[dcp+0x20:dcp+0x24] == b"DCA\x00" else 0)
    payload = d[start: start + comp]
    if ctype == b"ZSTD":
        return _zstd_decompress(payload, unc)
    if ctype == b"DFLT":
        return zlib.decompressobj().decompress(payload)
    if ctype == b"KRAK":
        raise RuntimeError("DCX 用了 Oodle(KRAK) 压缩，本实现不支持（本工具的 bin 不是这种）")
    raise RuntimeError("未知 DCX 压缩类型 %r" % ctype)

# ---------------------------------------------------------------- BND4
def bnd4_parse(b):
    """解析 BND4，返回 [(name, bytes), ...]"""
    assert b[:4] == b"BND4", "不是 BND4（前 4 字节 %r）" % b[:4]
    unk04, unk05 = b[4], b[5]
    big = b[9] == 1
    bitbig = b[10] != 1
    E = ">" if big else "<"
    fileCount, = struct.unpack_from(E + "i", b, 0x0C)
    version = b[0x18:0x20].rstrip(b"\x00").decode("ascii", "replace")
    fileHeaderSize, = struct.unpack_from(E + "q", b, 0x20)
    headersEnd, = struct.unpack_from(E + "q", b, 0x28)
    unicode_ = b[0x30] == 1
    fmt = b[0x31]
    extended = b[0x32]
    hashTableOffset, = struct.unpack_from(E + "q", b, 0x38)
    info = dict(fileCount=fileCount, version=version, fileHeaderSize=fileHeaderSize,
                headersEnd=headersEnd, unicode=unicode_, format=fmt, extended=extended,
                big=big, bitbig=bitbig, unk04=unk04, unk05=unk05, hashTable=hashTableOffset)
    # 格式位（Binder.Format 位域，与 bit 序有关）
    def hasbit(mask):
        v = fmt
        if bitbig:
            v = int("{:08b}".format(fmt)[::-1], 2)
        return bool(v & mask)
    F_COMPRESSION, F_IDS, F_NAMES1, F_NAMES2 = 0x01, 0x02, 0x04, 0x08
    F_LONG = 0x40
    info["fmt_flags"] = dict(compression=hasbit(F_COMPRESSION), ids=hasbit(F_IDS),
                             names1=hasbit(F_NAMES1), names2=hasbit(F_NAMES2), long=hasbit(F_LONG))
    out = []
    off = 0x40
    fs = info["fmt_flags"]
    for i in range(fileCount):
        p = off + i * fileHeaderSize
        # ⭐ NR/ER regulation 的 BND4 文件头实测布局（fileHeaderSize = 0x24，逐字节反推）：
        #   +0x00 u32 flags(+3 zero) | +0x04 i32 -1 | +0x08 i64 csize | +0x10 i64 usize
        #   +0x18 u32 dataOffset     | +0x1C u32 序号   | +0x20 u32 nameOffset   （正好 0x24）
        flags = b[p]
        minus1, = struct.unpack_from(E + "i", b, p + 4)
        csize, = struct.unpack_from(E + "q", b, p + 8)
        usize, = struct.unpack_from(E + "q", b, p + 0x10)
        doff, = struct.unpack_from(E + "I", b, p + 0x18)
        seq, = struct.unpack_from(E + "I", b, p + 0x1C)
        fid = -1
        name = None
        noff, = struct.unpack_from(E + "I", b, p + 0x20)
        if noff:
            name = read_name(b, noff, unicode_)
        data = b[doff:doff + csize]
        if fs["compression"] and usize >= 0 and usize != csize:
            data = dcx_unpack(data)
        out.append((name or ("%d.entry" % i), data, dict(id=fid, flags=flags, csize=csize,
                                                         usize=usize, doff=doff)))
    return info, out

def read_name(b, off, unicode_):
    """BND4 里存的是**带构建路径的完整路径**（例：W:\\CL\\data\\Param\\param\\GameParam_prod\\NpcParam.param）
       ⇒ 取最后一段当文件名。编码实测是 UTF-16LE（unicode 标志位靠不住，这里两种都试、择优）。"""
    if off <= 0 or off >= len(b):
        return None
    def u16():
        end = off
        while end + 1 < len(b) and b[end:end + 2] != b"\x00\x00":
            end += 2
        return b[off:end].decode("utf-16-le", "replace")
    def sjis():
        end = off
        while end < len(b) and b[end] != 0:
            end += 1
        return b[off:end].decode("shift-jis", "replace")
    cands = [u16(), sjis()]
    def score(s):
        return sum(1 for ch in s if 32 <= ord(ch) < 127)
    best = max(cands, key=score)
    if not best or score(best) < 3:
        return None
    return best.split("\\")[-1].split("/")[-1]

# ---------------------------------------------------------------- 主流程
def unpack(bin_path, out_dir, list_only=False):
    raw = open(bin_path, "rb").read()
    iv, ct = raw[:16], raw[16:]
    if len(ct) % 16:
        ct = ct + b"\x00" * (16 - len(ct) % 16)
    dec = _aes_cbc_decrypt(NR_KEY, iv, ct)
    inner = dcx_unpack(dec)
    info, entries = bnd4_parse(inner)
    print("解密 %d B → DCX 解压 %d B → BND4：%d 个条目（version=%s fileHeaderSize=0x%X fmt=0x%02X %s）"
          % (len(raw), len(inner), info["fileCount"], info["version"], info["fileHeaderSize"],
             info["format"], info["fmt_flags"]))
    if list_only:
        for n, d, m in entries[:20]:
            print("   %-46s %8d B  id=%s flags=0x%02X" % (n, len(d), m["id"], m["flags"]))
        print("   ... 共 %d 个" % len(entries))
        return info, entries
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for name, data, m in entries:
        safe = name.replace("/", os.sep)
        p = os.path.join(out_dir, safe)
        os.makedirs(os.path.dirname(p) or out_dir, exist_ok=True)
        open(p, "wb").write(data)
        n += 1
    print("已写出 %d 个文件 → %s" % (n, out_dir))
    return info, entries

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    b = sys.argv[1]
    o = sys.argv[2] if len(sys.argv) > 2 else None
    lst = "--list" in sys.argv
    unpack(b, o, lst)


def unpack_dir(bin_path, out_dir):
    """给刷新链路用的入口：解包并落地到 out_dir，返回条目数"""
    info, entries = unpack(bin_path, out_dir)
    return len(entries)
