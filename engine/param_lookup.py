# -*- coding: utf-8 -*-
r"""NR/ER .param 离线查询工具集 —— 列字段 / 按字段值反查行 / 全目录扫值（不依赖 Smithbox）。

用法（子命令）:
  python param_lookup.py fields <表名> [关键词]                       # 列字段序列（名/偏移/类型）
  python param_lookup.py find   <表.param> <表名> <字段名> <值>        # 按「字段值」反查行 ID
  python param_lookup.py dump   <表.param> <表名> <字段名> [lo] [hi]   # 列该字段全部非零值
  python param_lookup.py scan   <参数目录> <值> [表名过滤子串]          # 全目录扫某个整数值

Defs 目录可用环境变量 NR_PARAM_DEFS 覆盖（默认见下面 DEFS）。

═══ 实现要点（都踩过坑，别改）═══
1) 行索引表里「行数据偏移」是【绝对文件偏移】，不是相对 dataOff。
   第一版写成 dataOff + off 会 struct.error 越界（NpcParam rowSize=848 时报
   "requires a buffer of at least 8422897 bytes ... actual buffer size is 8422193"）。
2) bit 字段按位打包：连续 bit 共用一个字节；遇到非 bit 字段或字段表收尾时，
   bitbuf 未清空必须先前进 1 字节 —— 漏掉这步整体偏移全错、后面字段全读歪。
3) 解析前必过结构自校验 0x40 + rowCount*24 == dataOff 且 (total-dataOff) % rowCount == 0。
4) 读 f32 字段要用 "<f"，按 int 读会得到 1065353216 这种位模式。
5) find/dump/scan 都只比对整数字段；f32 字段会被当成位模式整数比较，用来定位 ID 无害但要留意。
"""
import os
import re
import struct
import sys

DEFS = os.environ.get("NR_PARAM_DEFS",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "Defs"))
SIZE = {"u8": 1, "s8": 1, "dummy8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4,
        "f32": 4, "u64": 8, "s64": 8, "f64": 8, "dummy32": 4}


def def_fields(table):
    """解析 Defs/<table>.xml -> [(name, kind, nbits, type, arrlen)]"""
    txt = open(os.path.join(DEFS, table + ".xml"), encoding="utf-8-sig", errors="replace").read()
    out = []
    for m in re.finditer(r'<Field\s+Def="([^"]+)"', txt):
        d = m.group(1).split()
        if len(d) < 2:
            continue
        nm = d[1]
        bit = re.search(r":(\d+)$", nm)
        arr = re.search(r"\[(\d+)\]", nm)
        if bit:
            out.append((nm.split(":")[0], "bit", int(bit.group(1)), d[0], 1))
        elif arr:
            out.append((nm.split("[")[0], "arr", 0, d[0], int(arr.group(1))))
        else:
            out.append((nm, "num", 0, d[0], 1))
    return out


def layout(flds):
    """字段序列 -> ([(name, off, size, kind, type)], rowSize)；bit 按位打包"""
    out, o, bitbuf = [], 0, 0
    for name, kind, nbits, typ, arr in flds:
        if kind == "bit":
            if bitbuf + nbits > 8:
                o += 1
                bitbuf = 0
            out.append((name, o, 0, "bit", typ))
            bitbuf += nbits
        else:
            if bitbuf:
                o += 1
                bitbuf = 0
            sz = SIZE.get(typ, 4) * (arr if kind == "arr" else 1)
            out.append((name, o, sz, kind, typ))
            o += sz
    if bitbuf:
        o += 1
    return out, o


def parse_param(path):
    b = open(path, "rb").read()
    rc = struct.unpack_from("<H", b, 0x0A)[0]
    data_off = struct.unpack_from("<I", b, 0x30)[0]
    total = struct.unpack_from("<I", b, 0x00)[0]
    if rc <= 0 or (total - data_off) % rc or 0x40 + rc * 24 != data_off:
        raise SystemExit("结构校验失败：不是可信的 .param（rowCount/dataOff/索引表对不上）")
    rs = (total - data_off) // rc
    rows = {}
    for i in range(rc):
        base = 0x40 + i * 24
        rid = struct.unpack_from("<I", b, base)[0]
        off = struct.unpack_from("<I", b, base + 8)[0]      # ★ 绝对偏移，勿加 data_off
        rows[rid] = b[off:off + rs]
    return rows, rs


def find_field(table, fname):
    lay, comp = layout(def_fields(table))
    for (name, off, sz, kind, typ) in lay:
        if name == fname:
            return (name, off, sz, kind, typ), comp
    return None, comp


def raw_int(row, off, sz, typ):
    if sz == 4:
        return struct.unpack_from("<i", row, off)[0]
    if sz == 2:
        return struct.unpack_from("<h", row, off)[0]
    if sz == 1:
        return row[off]
    return None


def cmd_fields(a):
    table = a[0]
    kw = a[1].lower() if len(a) > 1 else None
    lay, comp = layout(def_fields(table))
    print(f"=== {table}  字段数={len(lay)}  推算 rowSize={comp} ===")
    off = 0
    for (name, foff, sz, kind, typ) in lay:
        if kw and kw not in name.lower():
            continue
        desc = f"{typ}[{sz // SIZE.get(typ, 4)}]" if kind == "arr" else typ
        print(f"  off={foff:6d}  {name:44s} {desc}")
    print(f"  (合计 {comp} 字节)")


def cmd_find(a):
    param, table, fname, target = a[0], a[1], a[2], int(a[3])
    tgt, comp = find_field(table, fname)
    if tgt is None:
        raise SystemExit(f"!! 字段不存在: {fname}")
    rows, rs = parse_param(param)
    name, off, sz, kind, typ = tgt
    print(f"[自校验] 推算 rowSize={comp}  文件 rowSize={rs}  字段 {fname} off={off} sz={sz} type={typ}")
    hits = []
    for rid, row in sorted(rows.items()):
        if off + sz > len(row):
            continue
        v = raw_int(row, off, sz, typ)
        if v == target:
            hits.append((rid, v))
    print(f"命中 {len(hits)} 行:")
    for rid, v in hits[:60]:
        print(f"  ID={rid}  {fname}={v}")


def cmd_dump(a):
    param, table, fname = a[0], a[1], a[2]
    lo = int(a[3]) if len(a) > 3 else None
    hi = int(a[4]) if len(a) > 4 else None
    tgt, comp = find_field(table, fname)
    if tgt is None:
        raise SystemExit(f"!! 字段不存在: {fname}")
    rows, rs = parse_param(param)
    name, off, sz, kind, typ = tgt
    res = []
    for rid, row in sorted(rows.items()):
        if off + sz > len(row):
            continue
        v = raw_int(row, off, sz, typ)
        if v == 0:
            continue
        if lo is not None and not (lo <= v <= hi):
            continue
        res.append((rid, v))
    print(f"[自校验] rowSize={rs}  字段 {fname} off={off}  非零条目 {len(res)}")
    for rid, v in res[:200]:
        print(f"  ID={rid}  {fname}={v}")


def cmd_scan(a):
    d, target = a[0], int(a[1])
    filt = a[2].lower() if len(a) > 2 else None
    found = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".param"):
            continue
        table = fn[:-6]
        if filt and filt not in table.lower():
            continue
        try:
            lay, comp = layout(def_fields(table))
        except Exception:
            continue
        try:
            rows, rs = parse_param(os.path.join(d, fn))
        except SystemExit:
            continue
        for rid, row in rows.items():
            for (name, off, sz, kind, typ) in lay:
                if kind == "bit" or sz == 0 or sz > 4 or off + sz > len(row):
                    continue
                if raw_int(row, off, sz, typ) == target:
                    found.append((table, rid, name))
    print(f"命中 {len(found)} 处:")
    for t, rid, f in found[:80]:
        print(f"  {t:40s} 行ID={rid:<12d} 字段={f}")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cmd, a = sys.argv[1], sys.argv[2:]
    {"fields": cmd_fields, "find": cmd_find, "dump": cmd_dump, "scan": cmd_scan}[cmd](a)


if __name__ == "__main__":
    main()
