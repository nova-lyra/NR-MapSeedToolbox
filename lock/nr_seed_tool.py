# -*- coding: utf-8 -*-
"""
NR 地图种子工具（我们自己的版本，源自对 thefifthmatt 随机化器/去随机化器的逆向）

核心算法（逆向自 NightreignRandomizer 的 PatternSeedSet.GetPattern）:
    val = SFMT19937(seed).NextUInt32()          # 游戏用 SFMT 取 1 个 u32
    # 1) 先按权重决定"地形组"
    weights  = [8400, 400, 400, 400, 400]       # 无变幻大地：84% 普通 / 各 4% 特异地形
             = [5000,1250,1250,1250,1250]       # "大幅提高一次性变幻大地" 选项
    r = val % sum(weights); 找落在哪一档 → idx
      idx==0 → 普通, offset 基准 0, 范围 20
      idx>0  → 该地形, offset 基准 20+(idx-1)*5, 范围 5
    # 2) 若已装备/召唤了变幻大地 → 跳过权重，直接 offset 基准 = {11:20, 12:25, 13:30, 15:35}, 范围 5
    patternId = TargetBoss*40 + base + (val % 范围)

    地形编号(RareMap/modifier): 10 普通 · 11 雪山 · 12 火山 · 13 腐败森林 · 15 隐城（14 = 大空洞，本算法未含）
    夜王编号(TargetBoss): 0 格拉狄乌斯 · 1 艾德雷 · 2 格诺斯塔 · 3 玛利斯 · 4 利普拉 · 5 弗格尔 · 6 卡莉果 · 7 布德奇冥

用法:
  python nr_seed_tool.py pattern <seed> [--conjured 0]            # 算：这个种子会抽到哪张 pattern
  python nr_seed_tool.py find <patternId> [--conjured 0] [--count 5] [--shiftmore]
  python nr_seed_tool.py find-for 夜王号 地形号 偏移 [--conjured ...]   # 用 夜王+地形+偏移 指定
  python nr_seed_tool.py selftest                                  # 打印若干种子的结果，便于和游戏日志对照
"""
import argparse
import random
import sys

MSK = 0xFFFFFFFF

# ---- SFMT19937（精确复刻 Rei.Random.SFMT 的 MEXP=19937 分支）----
N32 = 624
PARITY = (0x00000001, 0x00000000, 0x00000000, 0x13C9E684)


def _init_state(seed):
    a = [0] * N32
    a[0] = seed & MSK
    for i in range(1, N32):
        a[i] = (1812433253 * (a[i - 1] ^ (a[i - 1] >> 30)) + i) & MSK
    # period_certification
    num = 0
    for i in range(4):
        num ^= a[i] & PARITY[i]
    for i in (16, 8, 4, 2, 1):
        num ^= num >> i
    if (num & 1) == 0:
        for i in range(4):
            bit = 1
            for _ in range(32):
                if bit & PARITY[i]:
                    a[i] ^= bit
                    break
                bit <<= 1
    return a


def _gen_rand_all(a):
    num, num2, num3, num4 = 0, 488, 616, 620
    while True:
        a[num + 3] = (a[num + 3] ^ ((a[num + 3] << 8) & MSK) ^ (a[num + 2] >> 24)
                      ^ (a[num3 + 3] >> 8) ^ ((a[num2 + 3] >> 11) & 0xBFFFFFF6)
                      ^ ((a[num4 + 3] << 18) & MSK)) & MSK
        a[num + 2] = (a[num + 2] ^ ((a[num + 2] << 8) & MSK) ^ (a[num + 1] >> 24)
                      ^ ((a[num3 + 3] << 24) & MSK) ^ (a[num3 + 2] >> 8)
                      ^ ((a[num2 + 2] >> 11) & 0xBFFAFFFF)
                      ^ ((a[num4 + 2] << 18) & MSK)) & MSK
        a[num + 1] = (a[num + 1] ^ ((a[num + 1] << 8) & MSK) ^ (a[num] >> 24)
                      ^ ((a[num3 + 2] << 24) & MSK) ^ (a[num3 + 1] >> 8)
                      ^ ((a[num2 + 1] >> 11) & 0xDDFECB7F)
                      ^ ((a[num4 + 1] << 18) & MSK)) & MSK
        a[num] = (a[num] ^ ((a[num] << 8) & MSK) ^ ((a[num3 + 1] << 24) & MSK)
                  ^ (a[num3] >> 8) ^ ((a[num2] >> 11) & 0xDFFFFFEF)
                  ^ ((a[num4] << 18) & MSK)) & MSK
        num3 = num4
        num4 = num
        num += 4
        num2 += 4
        if num2 >= N32:
            num2 = 0
        if num >= N32:
            break


def sfmt_first_uint32(seed, variant=0):
    """
    复刻 new SFMT((int)seed).NextUInt32()。

    variant（对不上游戏日志时用来秒试其它可能）:
      0 = 他的实现：初始化 → gen_rand_all() → 取 s[0]      ← 默认，优先用这个
      1 = 初始化后直接取 s[0]（不先跑一轮生成）
      2 = 生成后取 s[1]（第 2 个 u32）
      3 = 生成后取 s[2]
    """
    a = _init_state(seed)
    pre0 = a[0]
    _gen_rand_all(a)
    if variant == 1:
        return pre0
    if variant in (2, 3):
        return a[variant - 1]
    return a[0]


# ---- 游戏的地图选择逻辑 ----
WEIGHTS_NORMAL = (8400, 400, 400, 400, 400)       # 无变幻大地
WEIGHTS_SHIFTMORE = (5000, 1250, 1250, 1250, 1250)  # 大幅提高一次性变幻大地
CONJURE_BASE = {11: 20, 12: 25, 13: 30, 15: 35}   # 召唤变幻大地时的偏移基准
RARE_NAME = {0: "无（普通）", 10: "普通", 11: "雪山", 12: "火山", 13: "腐败森林", 14: "大空洞", 15: "隐城"}
BOSS_NAME = ["格拉狄乌斯", "艾德雷", "格诺斯塔", "玛利斯", "利普拉", "弗格尔", "卡莉果", "布德奇冥",
             "哈尔莫尼亚", "斯特拉格斯"]


def get_pattern(seed, conjured=0, shiftmore=False, variant=0):
    """返回 (patternId, 说明字符串)"""
    val = sfmt_first_uint32(seed, variant)
    base, span = 0, 20
    if conjured in CONJURE_BASE:
        base, span = CONJURE_BASE[conjured], 5
        how = "已召唤%s" % RARE_NAME[conjured]
    else:
        weights = WEIGHTS_SHIFTMORE if shiftmore else WEIGHTS_NORMAL
        total = sum(weights)
        r = val % total
        acc, idx = 0, -1
        for i, w in enumerate(weights):
            acc += w
            if acc > r:
                idx = i
                break
        how = "按权重抽到 第%d档" % idx
        if idx > 0:
            span = 5
            base = 20 + (idx - 1) * 5
    pid = base + (val % span)
    return pid, how


def describe(pid):
    """patternId → 夜王 / 偏移 / 地形"""
    boss = pid // 40
    off = pid % 40
    if off < 20:
        rare = 10
    elif off < 25:
        rare = 11
    elif off < 30:
        rare = 12
    elif off < 35:
        rare = 13
    else:
        rare = 15
    bn = BOSS_NAME[boss] if 0 <= boss < len(BOSS_NAME) else "夜王#%d" % boss
    return bn, off, rare


def find_seed(target_offset, conjured=0, shiftmore=False, limit=500000, tries=5, variant=0):
    """暴力搜：找到 seed 使得 get_pattern(seed) 的【偏移】== target_offset（0~39）"""
    out = []
    rnd = random.Random()
    for _ in range(limit):
        s = rnd.getrandbits(32)
        pid, _ = get_pattern(s, conjured, shiftmore, variant)
        if pid == target_offset:
            out.append(s)
            if len(out) >= tries:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("pattern")
    p1.add_argument("seed")
    p1.add_argument("--conjured", type=int, default=0)
    p1.add_argument("--shiftmore", action="store_true")
    p1.add_argument("--variant", type=int, default=0)

    p2 = sub.add_parser("find")
    p2.add_argument("pattern_id", type=int)
    p2.add_argument("--conjured", type=int, default=0)
    p2.add_argument("--shiftmore", action="store_true")
    p2.add_argument("--count", type=int, default=5)
    p2.add_argument("--variant", type=int, default=0)

    p3 = sub.add_parser("find-for")
    p3.add_argument("boss", type=int)
    p3.add_argument("rare", type=int, help="10 普通 / 11 雪山 / 12 火山 / 13 腐森 / 15 隐城")
    p3.add_argument("offset", type=int)
    p3.add_argument("--conjured", type=int, default=0)
    p3.add_argument("--count", type=int, default=5)
    p3.add_argument("--variant", type=int, default=0)

    p4 = sub.add_parser("selftest")

    p5 = sub.add_parser("check", help="对账：给(种子, 游戏日志里的 pattern)，看哪个变体对得上")
    p5.add_argument("seed")
    p5.add_argument("actual_pattern", type=int)

    args = ap.parse_args()
    if args.cmd == "pattern":
        seed = int(args.seed, 0)
        pid, how = get_pattern(seed, args.conjured, args.shiftmore, args.variant)
        bn, off, rare = describe(pid)
        print("种子 %s (0x%08X)" % (seed, seed & MSK))
        print("  → patternId = %d   （%s，偏移 %d）" % (pid, bn, off))
        print("  → 地形 = %s   推算依据: %s" % (RARE_NAME.get(rare, rare), how))
    elif args.cmd == "find":
        pid = args.pattern_id
        if not (0 <= pid < 320):
            print("⚠️ patternId %d 不在 0~319 本体段里 —— 仍按同一套公式（偏移 = pid %% 40）搜种子；未验证，回家在游戏里试。" % pid)
        boss, off = pid // 40, pid % 40
        bn, _, rare = describe(pid)
        if rare != 10 and args.conjured == 0:
            print("⚠ 这不提醒就算了：pattern %d 属于【%s】，若等候室不带变幻大地，'按权重抽' 也能抽到，只是概率 4%%×1/5" % (pid, RARE_NAME.get(rare)))
        seeds = find_seed(off, args.conjured, args.shiftmore,
                          tries=args.count, variant=args.variant)
        print("目标 pattern %d = %s · %s · 偏移%d  →  实际按【偏移 %d】搜" % (
            pid, bn, RARE_NAME.get(rare), off, off))
        if not seeds:
            print("没找到（加大 limit 或检查参数）")
        for s in seeds:
            chk, how = get_pattern(s, args.conjured, args.shiftmore, args.variant)
            print("  seed = 0x%08X  (%d)   复核: 偏移%d ✓  %s" % (s, s, chk, how))
    elif args.cmd == "find-for":
        base = 0 if args.rare == 10 else {11: 20, 12: 25, 13: 30, 15: 35}[args.rare]
        off = base + args.offset
        seeds = find_seed(off, args.conjured, False, tries=args.count, variant=args.variant)
        print("夜王%d · %s · 第%d张  → patternId %d（按偏移 %d 搜）→" % (
            args.boss, RARE_NAME.get(args.rare), args.offset + 1, args.boss * 40 + off, off))
        for s in seeds:
            print("  seed = 0x%08X  (%d)" % (s, s))
    elif args.cmd == "check":
        seed = int(args.seed, 0)
        print("对账：种子 0x%08X 在游戏日志里 = pattern %d" % (seed & MSK, args.actual_pattern))
        hit = []
        for v in (0, 1, 2, 3):
            pid, _ = get_pattern(seed, 0, False, v)
            mark = "  <<< 对上了" if pid == args.actual_pattern else ""
            if mark:
                hit.append(v)
            print("  变体%d → pattern %-4d%s" % (v, pid, mark))
        if hit:
            print("⇒ 用变体 %s 重跑 find（加 --variant %d）" % (hit, hit[0]))
        else:
            print("⇒ 都不对：可能取了别的 u32 位序，或该局带了变幻大地（试 --conjured 11/12/13/15）")
    else:
        print("seed         patternId  夜王 / 偏移 / 地形")
        for s in (0, 1, 2, 3, 0x12345678, 0xDEADBEEF, 0xFFFFFFFF):
            pid, _ = get_pattern(s)
            bn, off, rare = describe(pid)
            print("0x%08X   %-9d %s / %d / %s" % (s, pid, bn, off, RARE_NAME.get(rare)))


if __name__ == "__main__":
    main()
