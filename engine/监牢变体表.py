# -*- coding: utf-8 -*-
"""
监牢「变体 → 具体是谁」对照表生成器
================================================================
证据链（全部实测，不猜）：
  ① 场地 msb（m46_50/60/70/80）→ 每个怪实体：entId / npcId / 分组号 grp
  ② NpcParam 行名 → 中文名
  ③ 场地事件脚本 emevd：每个变体分支 `90045001 ... <变体号> <ref> ...`
     · ref 是"实体 id" → 该变体就是这一只（单体）
     · ref 是"组号"（出现在某实体的 grp 里）→ 该变体是一个族（游戏在族内挑一只）
输出：监牢变体表.js（可直接贴进编辑器）+ 监牢变体表.txt（人看）
"""
import glob
import json
import os
import re
import struct
import subprocess
import sys

MSBEDIT_DIR = r"D:\NightreignModResearch\EldenMod\projects\MSBEdit\bin\Debug\net10.0"
EMEVDPROBE_DIR = r"D:\NightreignModResearch\EldenMod\projects\EmevdProbe\bin\Release\net10.0"
SMITHBOX = r"D:\NightreignModResearch\EldenMod\tools\Smithbox\Assets\PARAM\NR"
GAOL_MSB = r"D:\NRX_tmp\map_study\evergaol_probe"      # 46_50\m46_50_00_00.msb.dcx 等
GAOL_VAN_EMEVD = r"D:\NRX_tmp\gaol_vanilla"            # m46_50_00_00.emevd.dcx 等
OUT = r"D:\NightreignModResearch\地图种子编辑器\数据生成"

GAOLS = [(4650, "46_50", "m46_50_00_00", "A", "低阶"),
         (4660, "46_60", "m46_60_00_00", "B", "中阶"),
         (4670, "46_70", "m46_70_00_00", "C", "中阶"),
         (4680, "46_80", "m46_80_00_00", "D", "高阶")]

# 中文名（官方译名；按英文行名精确匹配，匹配不到的保留英文）
CN = {"Omen (Evergaol)": "凶兆",
      "Grave Warden Duelist (Evergaol)": "墓守决斗者",
      "Misbegotten (Evergaol)": "狮子混种",
      "Misbegotten (Evergaol, Winged)": "有翼狮子混种",
      "Scaly Misbegotten (Evergaol)": "鳞皮狮子混种",
      "Azula Beastman (Evergaol, Cleaver)": "阿祖拉兽人（砍刀）",
      "Azula Beastman (Evergaol, Curved Sword)": "阿祖拉兽人（弯刀）",
      "Azula Beastman (Evergaol, Shield)": "阿祖拉兽人（盾）",
      "Banished Knight (Evergaol, Shield)": "失乡骑士（盾）",
      "Banished Knight (Evergaol, Dual Swords)": "失乡骑士（双剑）",
      "Banished Knight (Evergaol, Halberd)": "失乡骑士（戟）",
      "Nox Monk (Evergaol)": "诺克斯僧侣",
      "Nox Swordstress (Evergaol)": "诺克斯剑士",
      "Crystalian (Evergaol, Spear)": "结晶人（矛）",
      "Crystalian (Evergaol, Ringblade)": "结晶人（环刃）",
      "Crystalian (Evergaol, Staff)": "结晶人（法杖）",
      "Alabaster Lord (Evergaol)": "白玉王",
      "Onyx Lord (Evergaol)": "玛瑙王",
      "Bloodhound Knight (Evergaol)": "猎犬骑士",
      "Crucible Knight (Evergaol, Sword)": "熔炉骑士（剑）",
      "Crucible Knight (Evergaol, Treespear)": "熔炉骑士（树枪）",
      "Godskin Apostle (Evergaol)": "神皮使徒",
      "Godskin Noble (Evergaol)": "神皮贵族",
      "Dragonkin Soldier (Evergaol)": "龙人兵",
      "Godskin Apostle (The Oldest Gaol)": "神皮使徒",
      "Godskin Noble (The Oldest Gaol)": "神皮贵族",
      "Ancient Dragon (The Oldest Gaol)": "古龙",
      "Death Rite Bird (The Oldest Gaol)": "死亡仪式鸟"}


def jload(p):
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return json.load(open(p, encoding=enc))
        except BaseException:
            pass
    return None


def rownames(table):
    f = glob.glob(os.path.join(SMITHBOX, "Param Row Names", "**", table + ".json"), recursive=True)
    out = {}
    if f:
        d = jload(f[0])
        for e in (d or {}).get("Entries", []):
            n = e.get("Entries") or [""]
            out[e.get("ID")] = n[0]
    return out


def msb_entities(msb_path):
    r = subprocess.run([os.path.join(MSBEDIT_DIR, "MSBEdit.exe"), "inspect", msb_path],
                       cwd=MSBEDIT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ents = []
    for line in (r.stdout or "").splitlines():
        m = re.match(r"ent=(\d+) npc=(\d+) think=(\d+) model=(\S+) grp=\[([^\]]*)\] name=(\S+)", line.strip())
        if m:
            grp = [int(x) for x in m.group(5).split(",") if x.strip() and int(x) > 0]
            ents.append({"ent": int(m.group(1)), "npc": int(m.group(2)),
                         "model": m.group(4), "grp": grp, "name": m.group(6)})
    return ents


def emevd_variants(emevd_path):
    r = subprocess.run([os.path.join(EMEVDPROBE_DIR, "EmevdProbe.exe"), "dumpemevd", emevd_path],
                       cwd=EMEVDPROBE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = {}
    for line in (r.stdout or "").splitlines():
        m = re.search(r"90045001,(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\]", line.replace(" ", ""))
        if m:
            # 该行结构： 0,90045001,<base>,<x>,<变体号>,<引用>,<组补充>,<标志>
            variant = int(m.group(3))
            ref = int(m.group(4))
            out[variant] = ref
    return out


def main():
    npcname = rownames("NpcParam")
    js = {}
    lines = []
    for slot, sub, base, letter, tier in GAOLS:
        msb = os.path.join(GAOL_MSB, sub, base + ".msb.dcx")
        em = os.path.join(GAOL_VAN_EMEVD, base + ".emevd.dcx")
        ents = msb_entities(msb)
        vars_ = emevd_variants(em)
        # 组号 → 成员
        groups = {}
        for e in ents:
            for g in e["grp"]:
                groups.setdefault(g, []).append(e)
        lines.append("=== 监牢%s（%s） 槽 %d  场地 %s ===" % (letter, tier, slot, base))
        named = {}
        for e in ents:
            if e["model"] == "c1000":
                continue                       # 起始区的假人，不是 boss
            nm = npcname.get(e["npc"], "") or ("npc%d" % e["npc"])
            cn = CN.get(nm, nm)
            named[e["ent"]] = cn
            lines.append("   实体 %-9d %-38s %s%s" % (e["ent"], nm, cn,
                                                    ("   [组 %s]" % ",".join(map(str, e["grp"]))) if e["grp"] else ""))
        vd = {}
        for v, ref in sorted(vars_.items()):
            if ref in named:
                vd[v] = {"kind": "single", "ents": [ref], "label": named[ref]}
            elif ref in groups:
                mem = [x for x in groups[ref]]
                names = sorted({named.get(x["ent"], x["model"]) for x in mem})
                vd[v] = {"kind": "group", "ents": [x["ent"] for x in mem], "label": "／".join(names),
                         "group": ref}
            else:
                vd[v] = {"kind": "unknown", "ents": [], "label": "?(ref %d)" % ref}
            lines.append("   变体 %-2d → %s：%s" % (v, "单体" if vd[v]["kind"] == "single" else "族", vd[v]["label"]))
        js[str(slot)] = {"letter": letter, "tier": tier, "variants": vd,
                         "roster": sorted(set(named.values()))}
        lines.append("")

    txt = "\n".join(lines)
    open(os.path.join(OUT, "监牢变体表.txt"), "w", encoding="utf-8").write(txt)
    print(txt)

    jsout = ("/* 监牢：变体 → 具体是谁（2026-09-17 从场地 msb + 事件脚本实测得出，禁手改） */\n"
             "const EG_DETAIL=" + json.dumps(js, ensure_ascii=False, separators=(",", ":")) + ";\n")
    open(os.path.join(OUT, "监牢变体表.js"), "w", encoding="utf-8").write(jsout)
    print("已写出:", os.path.join(OUT, "监牢变体表.txt"), "和 监牢变体表.js")
    print("\n---- JS（贴进 index.html）----")
    print(jsout[:1500])


if __name__ == "__main__":
    main()
