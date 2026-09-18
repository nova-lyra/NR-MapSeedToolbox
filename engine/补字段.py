# -*- coding: utf-8 -*-
"""
补字段：给 data.js 补上 export_web3/enhance_web_data 不产出的 6 个字段
（nightPool / eventPool / bossSite / patternOwners / freePatternIds / usedPatternIdRange）
规则已用 2026-09-17 的旧 data.js 备份逐项对账通过（9/9 一致）。
"""
import json
import os
import re
from collections import defaultdict

# ⚠ 必须跟随 NR_WEB_OUT（刷新数据.py 会传）：以前写死取本脚本的上上级目录，
#   一旦输出目录换到别处（测试/多 bin 验证），它会偷偷写到编辑器目录去。
ED = os.environ.get("NR_WEB_OUT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAT = os.path.join(ED, "data.js")


def load(p):
    t = open(p, encoding="utf-8").read()
    m = re.search(r"window\.NR_DATA\s*=\s*(\{.*\})", t, re.S)
    return m.group(0), json.loads(m.group(1))


def main():
    _, d = load(DAT)

    # ① nightPool / eventPool：把 100 行 MapPatternSet 的 (值, 权重) 求和
    def agg(idx):
        out = defaultdict(int)
        for rid, row in d["mapPatternSet"].items():
            for pair in (row[idx] or []):
                v, c = pair[0], pair[1]
                if v and c:
                    out[v] += c
        return [[v, c] for v, c in sorted(out.items())]

    d["nightPool"] = {"1": agg(3), "2": agg(4)}
    d["eventPool"] = agg(5)

    # ② bossSite：槽 → boss，按槽种类分组；同一 boss 多槽时取槽号最小的那个
    built = defaultdict(dict)
    for slot, mod in (d.get("slotBoss") or {}).items():
        k = (d.get("slotKind") or {}).get(slot)
        if k in ("field", "basement", "night") and mod:
            cur = built[k].get(mod)
            if cur is None or int(slot) < cur:
                built[k][mod] = int(slot)
    d["bossSite"] = {k: dict(sorted(v.items())) for k, v in built.items()}

    # ③ patternOwners：每个排布的 (夜王, 地形) 列表（保留重复，展示时去重）
    d["patternOwners"] = {pid: [[r[1], r[2]] for r in rows] for pid, rows in d["patternFlags"].items()}

    # ④ 空闲种子号 / 已用号段
    used = set(int(x) for x in d["patternFlags"])
    d["freePatternIds"] = [i for i in (list(range(320, 1000)) + list(range(1200, 1300))) if i not in used]
    d["usedPatternIdRange"] = [min(used), max(used)]

    out = "window.NR_DATA=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";"
    open(DAT, "w", encoding="utf-8").write(out)
    print("  补字段完成：nightPool %d/%d 项、eventPool %d 项、bossSite %d 组、空闲号 %d 个、已用号段 %s"
          % (len(d["nightPool"]["1"]), len(d["nightPool"]["2"]), len(d["eventPool"]),
             len(d["bossSite"]), len(d["freePatternIds"]), d["usedPatternIdRange"]))
    print("  写回 data.js：", os.path.getsize(DAT), "B")


if __name__ == "__main__":
    main()
