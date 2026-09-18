# -*- coding: utf-8 -*-
r"""
自检_重封包.py —— 一键自证：把一份 regulation.bin 「原样重打包」一遍，看结果是否与原文件逐字节相同。

原理：如果我们的封包（DCX/zstd 参数 + AES 填充）和 FromSoft 原版完全一致，
      那么"不改任何值"地重打包，产出的字节应该**逐字节等于原文件**。
      相同 ⇒ 游戏一定能读我们写回的 bin；不同 ⇒ 打印出不同的位置。
用法（在工具箱 GUI 里由 --run-script 调用，也可直接命令行）：
    python 自检_重封包.py [要检查的 regulation.bin]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nr_bin_write as W
from nr_bin_unpack import _be32

DEFAULT = r"D:\NightreignModResearch\NRX测试区\mod\regulation.bin"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else DEFAULT
    if not os.path.isfile(src):
        print("找不到要检查的 bin：%s" % src)
        return 1
    orig = open(src, "rb").read()
    raw, dec, inner, info, entries = W.read_bin(src)
    new_dcx = W.dcx_wrap(dec, inner)
    out = W.aes_wrap(new_dcx)
    print("检查对象 : %s" % src)
    print("原文件   : %d B" % len(orig))
    print("重封包   : %d B" % len(out))
    print("表数     : %d ; 解压后 %d B ; DCS.compressed=%d" % (len(entries), len(inner), _be32(dec, 0x20)))
    print("DCX zstd 帧头 : %s  （原版规格应为 28b52ffd0030，DCX 头共 %d 字节）"
          % (new_dcx[76:82].hex(), 76))
    if out == orig:
        print("\n[OK] 与原文件【逐字节相同】—— 封包规格与原版一致，游戏能读。")
        return 0
    n = min(len(orig), len(out))
    diffs = [i for i in range(n) if orig[i] != out[i]]
    print("\n[!] 与原文件不同：%d 个字节（长度 %d vs %d）" % (len(diffs), len(orig), len(out)))
    if diffs:
        print("   前几个不同位置：%s" % [hex(x) for x in diffs[:10]])
    return 2


if __name__ == "__main__":
    try:                                   # 控制台可能是 GBK：避免 emoji/特殊字符把输出搞崩
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
