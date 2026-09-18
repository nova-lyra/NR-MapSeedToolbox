# -*- coding: utf-8 -*-
"""增强 web/data.js（第二版）：
  ① 幂等修复（slots 的变体字段重复追加问题）
  ② 中文名统一：槽中文名 slotCN / 缩圈中文名 playAreaCN
  ③ 缩圈候选池 playAreaPool（按地形分第1夜/第2夜——用户的规则，数据实测得出）
  ④ 中文表整体写进 data.js 的 cn 字段（python 侧唯一来源 = index.html 顶部那几个表）
可重复运行（幂等）。⚠️ 重跑了 export_web3.py 之后必须再跑本脚本。
"""
import os, sys, json, re, struct
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import param_lookup as pl

V = os.environ.get('NR_WEBGEN_BIN', r'D:\NRX_tmp\nrx_bin\regulation-bin')
A = os.environ.get('NR_SMITHBOX_ASSETS',
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets'))
WEB = os.environ.get('NR_WEB_OUT', r'D:\NightreignModResearch\地图种子编辑器')
DAT = os.path.join(WEB, 'data.js')
FMT = {'s8': 'b', 'u8': 'B', 'dummy8': 'B', 's16': 'h', 'u16': 'H', 's32': 'i', 'u32': 'I', 'f32': 'f'}


def loadp(t):
    rows, rs = pl.parse_param(os.path.join(V, t + '.param'))
    lay, _ = pl.layout(pl.def_fields(t))
    return rows, lay


def gf(row, lay, name):
    for (n, off, sz, kind, typ) in lay:
        if n == name:
            if kind == 'bit' or sz == 0 or off + sz > len(row):
                return None
            return struct.unpack_from('<' + FMT[typ], row, off)[0]
    return None


# ---------- 0) 从 index.html 提取中文表（用户可改，改完重跑本脚本全域生效）----------
html = open(os.path.join(WEB, 'index.html'), encoding='utf-8').read()


def js_obj(name):
    m = re.search(r'const %s=(\{.*?\});' % name, html, re.S)
    if not m:
        raise SystemExit('index.html 里没找到 %s' % name)
    s = m.group(1)
    s = re.sub(r'([{,]\s*)(\d+)\s*:', r'\1"\2":', s)          # 数字键加引号
    s = s.replace("'", '"').replace(',}', '}')
    return json.loads(s)


BOSS_CN = {int(k): v for k, v in js_obj('BOSS_CN').items()}
ICON_CN = {int(k): v for k, v in js_obj('ICON_CN').items()}
NIGHT_CN = {int(k): v for k, v in js_obj('NIGHT_CN').items()}
EVENT_CN = {int(k): v for k, v in js_obj('EVENT_CN').items()}
CASTLE_CN = {int(k): v for k, v in js_obj('CASTLE_CN').items()}
RARE_CN = {int(k): v for k, v in js_obj('RARE_CN').items()}
KIND_CN = {'field': '野外头目', 'night': '夜Boss', 'basement': '地下室Boss', 'horde': '夜潮',
           'evergaol': '封印监牢', 'castle': '主城', 'base': '据点', 'large': '大据点', 'unknown': '未命名据点'}
print('从 index.html 读到中文表: BOSS %d / ICON %d / NIGHT %d / CASTLE %d' %
      (len(BOSS_CN), len(ICON_CN), len(NIGHT_CN), len(CASTLE_CN)))

# ---------- 1) 读现有 data.js ----------
s = open(DAT, encoding='utf-8').read()
D = json.loads(s[s.index('{'):s.rstrip().rstrip(';').rindex('}') + 1])

# ---------- 2) 槽的变体值/变体图标（幂等：只保留前 4 个原始字段再追加）----------
var, layv = loadp('SmallBaseMapVariationParam')
for rid in list(D['slots'].keys()):
    i = int(rid)
    base = D['slots'][rid][:4]
    if i not in var:
        D['slots'][rid] = base + [[0] * 10, [0] * 10]
        continue
    vv = [gf(var[i], layv, 'variationValue_%d' % k) or 0 for k in range(1, 11)]
    vi = [gf(var[i], layv, 'variationIconId_%d' % k) or 0 for k in range(1, 11)]
    D['slots'][rid] = base + [vv, vi]
print('slots 变体字段已刷新（幂等，每槽 6 个元素）')

# ---------- 3) 槽类别 + boss 值 ----------
slot_kind, slot_boss = {}, {}
for rid, v in D['slots'].items():
    nm = (v[0] or '').strip()
    boss = 0
    for m in (v[2], v[3]):
        if m and 0 < m < 20000:
            boss = m
            break
    if nm.startswith('Field Boss'):
        k = 'field'
    elif nm.startswith('Night Boss'):
        k = 'night'
    elif nm.startswith('Basement Boss'):
        k = 'basement'
    elif nm.startswith('Night Horde'):
        k = 'horde'
    elif nm.startswith('Evergaol'):
        k = 'evergaol'
    elif nm.startswith('Castle'):
        k = 'castle'
    elif nm:
        k = 'base'
    else:
        k = 'unknown'
    slot_kind[rid] = k
    if boss:
        slot_boss[rid] = boss
D['slotKind'], D['slotKindCN'], D['slotBoss'] = slot_kind, KIND_CN, slot_boss

# ---------- 4) ★ 据点中文名（细分：方位 + 大类 + 部位 + 属性/敌人）----------
BIG = [('Blacksmith', '锻造村'), ('Underground Fort', '地下要塞'), ('Great Church', '大教堂'),
       ('Sorcerer', '魔法师塔'), ('Church', '教堂'), ('Marsh', '湖沼'), ('Encampment', '营地'),
       ('Camp', '营地'), ('Caravans', '商队'), ('Caravan', '商队'), ('Ruins', '遗迹'), ('Fort', '要塞'),
       ('Township', '小镇'), ('Temple', '神殿'), ('Castle', '主城'), ('Evergaol', '封印监牢'),
       ('Boss Raid', '夜王突袭地'), ('Catacomb', '地下墓地')]
DIR_CN = [('Western', '西部'), ('Eastern', '东部')]
PART_CN = [('Underground Part', '地下部分'), ('Underground', '地下'), ('Main', '主部'), ('Interior', '内部'),
           ('Open Fort', '开放型'), ('Sewer Fort', '下水道'),
           ('Outside', '外部'), ('Library', '书库'), ('Upper Boss', '上层有Boss'), ('Spread Group', '分散群'),
           ('Divine Tower', '神塔'), ('Icon', '图标版')]
ELEM_CN = [('Poison', '毒'), ('Frost', '冰霜'), ('Sleep', '睡眠'), ('Rot', '腐败'), ('Madness', '疯狂'),
           ('Blood', '出血'), ('Holy', '神圣'), ('Lightning', '雷电'), ('Magic', '魔法'), ('Fire', '火焰')]
ENEMY_CN = [('Bloodhound Knights', '猎犬骑士'), ('Bloodhound Knight', '猎犬骑士'), ('Omenkillers', '凶兆杀手'),
            ('Blackflame Monks', '黑火焰僧'), ('Fire Knights', '火焰骑士'), ('Curseblades', '咒剑士'),
            ('Banished Knights', '失乡骑士'), ('Sorcerers', '魔法师众'), ('Carians', '卡利亚众'), ('Omen', '凶兆'),
            ('Crucible Knights', '熔炉骑士'), ('Pot Trolls', '投壶山妖')]
TERRAIN_CN = [('Great Hollow', '大空洞'), ('Rotted Woods', '腐败森林'), ('Mountaintop', '雪山'),
              ('The Crater', '火山'), ('Shrouded City', '隐城')]
# 行名括号里的"属性/形态"（★ 用户要求：A/B 到底是啥必须说清）
PAREN_CN = [('Standard', '标准'), ('Tower', '塔'), ('Ruined', '废墟'), ('Above ground', '地上'),
            ('Underground Part', '地下部分'), ('Underground', '地下'), ('Starter', '起始区'),
            ('Open Fort', '开放型'), ('Sewer Fort', '下水道'), ('Upper Boss', '上层有Boss'),
            ('Spread Group', '分散群'), ('Rotted Samll Camp', '腐败森林营地'), ('Crypt', '地下墓室'),
            ('Icon', '图标版'), ('Variant', '变体'), ('Main', '主部'), ('Interior', '内部'),
            ('Outside', '外部'), ('Library', '书库'), ('Part', '部分')]


def _exact(lst, t):
    for en, cn in lst:
        if en.lower() == (t or '').lower():
            return cn
    return None
HORDE_CN = {'Royal Army': '王家军', 'Giant Dogs and Crows': '巨犬与鸦群', 'Giant Ants': '巨蚁',
            'Fingercreepers': '指虫', 'Dragons': '龙群', 'Guardian Golems': '守护石像鬼',
            'Mounted Revenant Followers': '骑马亡者仆从'}
# ★ 行名里的 boss 英文 → 中文（比"枚举值→中文"准：同一枚举可能对应两只怪，如 416）
EN_BOSS_CN = {
    'Red Wolf of the King Consort': '王夫的红狼', 'Draconic Tree Sentinel': '龙装大树守卫',
    'Tree Sentinel': '大树守卫', 'Royal Carian Knight': '王室骑士 罗蕾塔', 'Leonine Misbegotten': '狮子混种',
    'Bell Bearing Hunter': '铃珠猎人', 'Elder Lion': '老狮子', 'Flying Dragon of the Hills': '飞龙',
    'Royal Revenant': '王室幽魂', "Night's Cavalry": '黑夜骑兵', 'Ulcerated Tree Spirit': '腐败树灵',
    'Ancestor Spirit': '祖灵', 'Grafted Scion': '接肢的贵族后裔', 'Black Blade Kindred': '黑剑眷属',
    'Erdtree Avatar': '黄金树的化身', 'Magma Wyrm': '熔岩土龙', 'Ancient Hero of Zamor': '萨米尔的古英雄',
    'Miranda Blossom': '米兰达之花', 'Golden Hippopotamus': '黄金河马', 'Death Rite Bird': '死亡仪式鸟',
    'Demi-Human Queen': '亚人女王', 'Black Knife Assassin': '黑刀刺客', 'Wormface': '尸脸',
    'Tibia Mariner/Those Who Live in Death': '提比亚的唤声船', 'Gaping Dragon': '贪食魔龙',
    'Centipede Demon': '蜈蚣恶魔', "Duke's Dear Freja": '公爵的挚爱 芙蕾雅', 'Smelter Demon': '融铁恶魔',
    'Nameless King': '无名王者', 'Dancer of the Boreal Valley': '冷冽谷的舞娘',
    'Fell Omen (Morgott)': '恶兆妖鬼 玛尔基特', 'Godskin Apostle': '神皮使徒', 'Godskin Duo': '神皮双人组',
    'Grafted Monarch (Godrick)': '接肢葛瑞克', 'Valiant Gargoyle': '英武石像鬼', 'Great Wyrm': '大土龙',
    'Ancient Dragon': '古龙', 'Full-Grown Fallingstar Beast': '成熟坠星兽',
    'Nox Dragonkin Soldier': '诺克斯龙人兵', 'Bell-bearing Hunter (Elemer)': '铃珠猎人 艾莱梅尔',
    'Crucible Knight/Golden Hippopotamus': '熔炉骑士与黄金河马', 'Outland Commander (Niall)': '异国指挥官 尼阿尔',
    "Battlefield Commander (O'Neil)": '战场指挥官 欧尼尔', "Night's Cavalry Duo": '黑夜骑兵(双人)',
    'Demi-Human Queen/Demi-Human Swordmaster': '亚人女王／亚人剑圣', 'Curseblade/Divine Beast Warrior': '咒剑士与神兽战士',
    'Great Red Bear': '大红熊', 'Death Knights': '死亡骑士',
    'Demon in Pain/Demon from Below': '痛苦恶魔与地底恶魔', 'Lord of Blood (Mohg)': '血王 莫格',
    'Divine Beast Dancing Lion': '神兽舞狮', 'Knight Artorias': '骑士 亚尔特留斯', 'Demon Prince': '恶魔王子',
    'Royal Army': '王家军', 'Giant Dogs and Crows': '巨犬与鸦群', 'Giant Ants': '巨蚁',
    'Fingercreepers': '指虫', 'Guardian Golems': '守护石像鬼',
}
# 位置行名里的英文 → 中文（用于"换位置"时显示人话）
PLACE_CN = [('Ruins North of Castle', '主城北·遗迹'), ('North of Castle', '主城之北'), ('South of Castle', '主城之南'),
            ('Southwest Corner', '西南角'), ('Northwest Corner', '西北角'), ('Northeast Corner', '东北角'),
            ('Southeast Corner', '东南角'), ('Castle Front', '主城前'), ('Southeast Lakeshore', '东南湖岸'),
            ('East of Saintsbridge', '圣桥东'), ('West Stormhill Graveyard', '西·暴风丘墓地'),
            ('Northwest Stormhill', '暴风丘西北'), ('Northeast Stormhill', '暴风丘东北'),
            ('Stormhill North of Gate', '暴风丘·城门前北'), ('Gatefront', '城门前'), ('Groveside', '林边'),
            ('South Lake', '南湖'), ('Northwest Lake', '西北湖'), ('Alexander Spot', '亚历山大所在地'),
            ('Waypoint Ruins', '路点遗迹'), ('West Mistwood', '雾林西'), ('Northwest Mistwood', '雾林西北'),
            ('Northeast Mistwood', '雾林东北'), ('South Mistwood', '雾林南'), ("Artist's Shack", '画家的小屋'),
            ('Summonwater Approach', '召水村入口'), ('Summonwater', '召水村'), ('Minor Erdtree', '小黄金树'),
            ('Southwest Mistwood', '雾林西南'), ('Northwest Mistwood Pond', '雾林西北池塘'),
            ('Northeast of Lake', '湖东北'), ('Divine Tower', '神塔')]
TIER_CN = {'1': '低阶', '2': '中阶', '3': '高阶'}
# 大据点（2000~2150，不在槽表里，来自 attachPoint.defaultSmallBase 的行名）
LARGE_CN = {2000: '林边', 2010: '城门前', 2020: '暴风丘·城门前北', 2030: '暴风丘西北', 2040: '南湖',
            2050: '亚历山大所在地', 2060: '暴风丘东北', 2070: '路点遗迹', 2080: '雾林西', 2090: '雾林西北',
            2100: '画家的小屋', 2110: '召水村入口', 2120: '雾林南', 2130: '小黄金树', 2140: '雾林东北',
            2150: '召水村'}
# 夜王突袭地（Boss Raid - XXX (夜王英文名)）
NIGHT_EN = {'Gladius': '黑夜野兽 格拉狄乌斯', 'Adel': '艾德雷', 'Gnoster': '格诺斯塔',
            'Maris': '玛利斯', 'Libra': '利普拉', 'Fulghor': '弗格尔',
            'Caligo': '卡莉果', 'Heolstor': '布德奇冥', 'Harmonia': '哈尔莫尼亚',
            'Straghess': '斯特拉格斯', 'Morgott': '恶兆妖鬼 玛尔基特'}


def _hit(lst, nm, word=True):
    for en, cn in lst:
        pat = (r'\b%s\b' % re.escape(en)) if word else re.escape(en)
        if re.search(pat, nm, re.I):
            return cn
    return None


# 槽 → 所属遭遇组名（补充语义：如 4090 属于 "Ruined Sorcerer's Rise" = 远古魔法师塔）
slot_groups = defaultdict(list)
for gid, g in D['groups'].items():
    for s in g[3]:
        slot_groups[s].append(g[0] or '')


def slot_cn(rid):
    v = D['slots'][rid]
    nm = (v[0] or '').strip()
    k = slot_kind[rid]
    boss = slot_boss.get(rid, 0)
    n = int(rid)
    if 2000 <= n <= 2150:                      # 大据点
        return '大据点·' + LARGE_CN.get(n, str(n))
    if not nm and 5300 <= n <= 5400:           # 只在大空洞种子里出现的 DLC 专用据点
        return '（大空洞专用据点）'
    if k in ('field', 'night', 'basement') and nm:
        bs = nm.split(' - ', 1)[-1].strip() if ' - ' in nm else nm
        cn_b = EN_BOSS_CN.get(bs)
        if cn_b:
            return KIND_CN[k] + '·' + cn_b
        if boss:
            return KIND_CN[k] + '·' + BOSS_CN.get(boss, str(boss))
    if k == 'horde':
        tail = nm.split(' - ', 1)[-1] if ' - ' in nm else nm
        return '夜潮·' + EN_BOSS_CN.get(tail, HORDE_CN.get(tail, tail))
    if k == 'castle':
        return '主城·' + CASTLE_CN.get(n, nm)
    if k == 'evergaol':
        letter = nm.split(' ')[1] if len(nm.split(' ')) > 1 else '?'
        tier = nm.split('Tier')[-1].strip() if 'Tier' in nm else '?'
        return '封印监牢' + letter + '（' + TIER_CN.get(tier, '') + '）'
    if not nm:
        return '（本体未占用）'
    gs = ' / '.join(slot_groups.get(n, []))
    gs = ' / '.join(slot_groups.get(n, []))
    if 'Ruined Sorcerer' in gs:
        return '古老魔法师塔'
    # 山羊交易（Libra 交易点）——必须在夜王突袭地判断之前
    if 'Scale-bearing Merchant' in nm:
        return '山羊交易（利普拉）'
    # 斜括号里带夜王英文名 = 夜王突袭地
    mb2 = re.search(r'\(([A-Za-z ]+)\)', nm)
    if mb2 and mb2.group(1).strip() in NIGHT_EN:
        return '夜王突袭地·' + NIGHT_EN[mb2.group(1).strip()]
    it = _hit(BIG, nm) or (_hit(BIG, gs) if gs else None) or nm
    nm2 = nm
    for en, _cn in BIG:
        if re.search(re.escape(en), nm2, re.I):
            nm2 = re.sub(re.escape(en), ' ', nm2, flags=re.I)
            break
    mb = re.search(r'\b([A-F])\b', nm2)
    if mb and it and not it.endswith(mb.group(1)):
        it = it + mb.group(1)
    d = _hit(DIR_CN, nm2)
    # 括号里的每一项都翻成中文（这就是"A 到底是什么"的答案）
    props = []
    for m in re.finditer(r'\(([^)]*)\)', nm):
        t = m.group(1).strip()
        cn = _exact(PAREN_CN, t) or _exact(ENEMY_CN, t) or _exact(TERRAIN_CN, t) or _exact(PART_CN, t) or t
        if cn and cn not in props:
            props.append(cn)
    s2 = ((d or '') + it)
    if props:
        s2 += '（%s）' % '·'.join(props)
    elem = _hit(ELEM_CN, nm2)
    enemy = _hit(ENEMY_CN, nm2)
    tail = ([enemy] if enemy else ([elem] if elem else []))
    if tail:
        s2 += '·' + '·'.join(tail)
    # 西部/东部地下要塞（5349~5391）实测只出现在大空洞（rareMap=4）的种子里
    if 5340 <= n <= 5399:
        s2 += '｜大空洞专属'
    return s2


D['slotCN'] = {rid: slot_cn(rid) for rid in D['slots']}
# 大据点（2000~2150）不在槽表里，但会被种子直接引用 → 补进中文名表
for n, cn in LARGE_CN.items():
    D['slotCN'].setdefault(str(n), '大据点·' + cn)

# ---------- 4b) 位置（同类型可出现的位置）+ 位置中文名（"换位置 / 这里不放"要用）----------
pos = defaultdict(Counter)
for pid, rows in D['patterns'].items():
    for r in rows:
        pos[str(r[2])][r[1]] += 1
D['positionsBySlot'] = {k: [p for p, _ in v.most_common()] for k, v in pos.items()}

EVT_WORD_CN = [('Scale-Bearing Merchant', '山羊交易'), ('Fell Omen', '凶兆'), ('Smoldering Butterfly', '燃蝶'),
               ('Night Horde', '夜潮'), ('Field Boss', '野外头目'), ('Night Boss', '夜Boss'),
               ('Great Hollow', '大空洞'), ('Divine Tower', '神塔')]


def place_cn(nm):
    s = (nm or '').strip()
    for en, cn in PLACE_CN + EVT_WORD_CN:
        if en.lower() in s.lower():
            s = re.sub(re.escape(en), cn, s, flags=re.I)
    s = re.sub(r'^(Event|Major Base|Minor Base|Starter Major Base|Starter Minor Base|Field Boss|Night Boss|'
               r'Basement Boss|Night Horde|Castle|Evergaol|\[Castle\]|\[Large Base\]|\[Great Hollow\])\s*-\s*', '', s)
    s = s.strip(' ,-')
    return s or '（未命名位置）'


D['attachCN'] = {rid: place_cn(v[0]) for rid, v in D['attachPoints'].items()}
print()
print('位置中文名抽查（持秤商人的 710~718）：')
for i in ('710', '711', '712', '713', '715', '716', '717', '718'):
    p_ = D['attachPoints'].get(i)
    if p_:
        print('   %s %-52s → %s' % (i, (p_[0] or '')[:52], D['attachCN'][i]))
print('   该槽可换位置数（持秤商人 4940）: %d 个' % len(D['positionsBySlot'].get('4940', [])))

# ---------- 4c) 位置按地形分开（★ 用户 2026-09-16 指出：不同特异地形位置不同）----------
posr = defaultdict(lambda: defaultdict(Counter))
for pid, rows in D['patterns'].items():
    fl0 = (D['patternFlags'].get(pid) or [[0, 0, 0, 0, 0, 0, 0]])[0]
    rm = str(fl0[2])
    for r in rows:
        posr[rm][str(r[2])][r[1]] += 1
D['positionsByRare'] = {rm: {s: [p for p, _ in c.most_common()] for s, c in d.items()}
                        for rm, d in posr.items()}
print()
print('位置按地形（持秤商人 4940）：')
for rm in sorted(D['positionsByRare']):
    lst = D['positionsByRare'][rm].get('4940', [])
    print('   地形 %s %-10s → %s' % (rm, RARE_CN.get(int(rm), ''), lst))

# ---------- 4d) 主城三件套（类型 / 楼顶boss / 楼底boss）----------
def slots_of_attach(a):
    return sorted({str(r[2]) for rows in D['patterns'].values() for r in rows if str(r[1]) == a},
                  key=lambda x: int(x))


D['castleSlots'] = {'typeSlot': slots_of_attach('190'),      # 4941/4942/4943
                    'roof': slots_of_attach('756'),          # 楼顶 boss（野外头目槽系列）
                    'base': slots_of_attach('757'),          # 楼底 boss（地下室Boss 槽）
                    'attach': {'type': 190, 'roof': 756, 'base': 757}}
print()
print('主城三件套：类型槽 %s ｜ 楼顶候选 %d 个 ｜ 楼底候选 %d 个' %
      (D['castleSlots']['typeSlot'], len(D['castleSlots']['roof']), len(D['castleSlots']['base'])))

# ---------- 4e) 造新种子时可选的事件（登记行的 modifier）----------
# 事件（modifier = 事件本身；modifierSet = 该事件的"条件组"，配 800/801 表示"第几天出现"）
EVENT_SETS = {'第1天': 800, '第2天': 801}
D['eventSetDays'] = EVENT_SETS
D['eventOptions'] = [
    {'v': 180, 'name': '夜潮', 'set': 3030},
    {'v': 200, 'name': '陨石坠落', 'set': 3010},
    {'v': 210, 'name': '灵庙', 'set': 3020},
    {'v': 120, 'name': '额外夜晚Boss（第一/二夜多打一只）', 'set': 3000},
    {'v': 140, 'name': '疯狂之塔', 'set': 3080},
    {'v': 230, 'name': '古老魔法师塔', 'set': 3090},
    {'v': 604, 'name': '夜王突袭·恶兆妖鬼玛尔基特', 'set': 3040, 'raid': True},
    {'v': 603, 'name': '夜王突袭·玛利斯', 'set': 3050, 'raid': True},
    {'v': 601, 'name': '夜王突袭·格诺斯塔', 'set': 3060, 'raid': True},
    {'v': 602, 'name': '夜王突袭·利普拉', 'set': 3070, 'raid': True},
    {'v': 600, 'name': '夜王突袭·格拉狄乌斯', 'set': 3120, 'raid': True},
    {'v': 10000, 'name': '夜王突袭·卡莉果', 'set': 3110, 'raid': True},
    {'v': 10001, 'name': '夜王突袭·哈尔莫尼亚', 'set': 3130, 'raid': True},
    {'v': 14000, 'name': '湖沼·学者的追忆（DLC）', 'set': 0},
]
# 大空洞专属事件（requireModifier 14，本工具暂不做大空洞，列出来备查）
D['eventOptionsGH'] = [
    {'v': 120, 'name': '额外夜晚Boss（大空洞）', 'set': 505},
    {'v': 140, 'name': '疯狂之塔（大空洞）', 'set': 530},
    {'v': 600, 'name': '夜王突袭·格拉狄乌斯（大空洞）', 'set': 520},
    {'v': 603, 'name': '夜王突袭·玛利斯（大空洞）', 'set': 540},
    {'v': 601, 'name': '夜王突袭·格诺斯塔（大空洞）', 'set': 550},
    {'v': 602, 'name': '夜王突袭·利普拉（大空洞）', 'set': 560},
    {'v': 10000, 'name': '夜王突袭·卡莉果（大空洞）', 'set': 510},
]
print('事件选项：%d 个（+ 大空洞专属 %d 个）' % (len(D['eventOptions']), len(D['eventOptionsGH'])))

# ---------- 4g) ★ 地图点图标体系：槽号 → iconId（真文件名 = MENU_MenuIcon_<iconId>）----------
# 世界地图点的 worldMapPointIconId 是"槽号"，要经 WorldMapPointIconParam 换成 iconId，再找 MENU_MenuIcon_<iconId>
rows_ip, lay_ip = loadp('WorldMapPointIconParam')
ipm = {}
for rid, r in rows_ip.items():
    v = gf(r, lay_ip, 'iconId')
    if v:
        ipm[str(rid)] = v
D['iconParam'] = ipm
# 变体小图标：variationIconId（42600~42610）→ 文件名 MENU_MenuIcon_<该值>
D['variantIconPrefix'] = 'MENU_MenuIcon_'
print('地图点图标映射（槽号→iconId）：%d 个，例 %s' % (len(ipm), dict(list(sorted(ipm.items(), key=lambda x: int(x[0])))[:6])))

# ---------- 4h) ★ 覆盖导入时要写回原值的 unknown_0（全库只有 48 行是 1，固定写 0 会改坏它们）----------
_lrs, _lay = loadp('LotResultSmallBaseAndSpot')
D['sbUnknownOne'] = [str(rid) for rid, r in _lrs.items() if gf(r, _lay, 'unknown_0')]
_flg, _layf = loadp('LotResultMapPatternFlag')
D['flUnknownOne'] = [str(rid) for rid, r in _flg.items() if gf(r, _layf, 'unknown_0')]
_pap, _layp = loadp('LotResultPlayAreaParam')
D['paUnknownOne'] = [str(rid) for rid, r in _pap.items() if gf(r, _layp, 'unknown_0')]
print('unknown_0=1 的行数：据点表 %d / 登记表 %d / 夜boss表 %d' %
      (len(D['sbUnknownOne']), len(D['flUnknownOne']), len(D['paUnknownOne'])))

# ---------- 4i) ★ 地形抽签行（LotBaseMapPatternFlag）—— "必定抽到种子"必须同时固定地形，否则地形随机就轮不到 ----
_BF_FIELDS = ['unknown_0', 'modifierSet', 'requireModifier1', 'requireModifier2',
              'excludeModifier1', 'excludeModifier2', 'modifier', 'eventFlag', 'weight']
_lb, _layb = loadp('LotBaseMapPatternFlag')
D['baseFlagFields'] = _BF_FIELDS
D['baseFlag'] = {str(rid): [gf(r, _layb, f) for f in _BF_FIELDS] for rid, r in _lb.items()}
_terr = [rid for rid, v in D['baseFlag'].items() if v[6] in (10, 11, 12, 13, 15)]
print('地形抽签行：%s（modifier=%s，weight=%s）' % (
    sorted(_terr, key=int),
    [D['baseFlag'][r][6] for r in sorted(_terr, key=int)],
    [D['baseFlag'][r][8] for r in sorted(_terr, key=int)]))

# ---------- 4f) 封印监牢：Tier / 可用变体（0-based）/ 牢里会出现哪些怪 ----------
eg = {}
for rid, v in sorted(D['slots'].items()):
    if not (v[0] or '').startswith('Evergaol'):
        continue
    vv = v[4] if len(v) > 4 else []
    letter = v[0].split(' ')[1] if len(v[0].split(' ')) > 1 else '?'
    tier = v[0].split('Tier')[-1].strip() if 'Tier' in v[0] else '?'
    eg[rid] = {'name': v[0], 'letter': letter, 'tier': tier, 'tierCN': TIER_CN.get(tier, ''),
               'variants': [i for i, x in enumerate(vv) if x]}
# 牢里的怪：从 4 张场地 msb 的实体 → NpcParam 行名读出（行名自带 "(Evergaol)" 标记）
EVERGAOL_ROSTER = {
    '4650': ['凶兆', '墓守决斗者', '狮子混种', '有翼狮子混种', '鳞皮狮子混种', '阿祖拉兽人'],
    '4660': ['失乡骑士（盾／双剑／戟）', '诺克斯僧侣', '诺克斯剑士', '结晶人（矛／环刃／法杖）',
             '白玉王', '玛瑙王', '猎犬骑士'],
    '4670': ['熔炉骑士（剑／树枪）', '神皮使徒', '神皮贵族', '龙人兵'],
    '4680': ['神皮使徒', '神皮贵族', '古龙', '死亡仪式鸟'],
}
for _k, _v in EVERGAOL_ROSTER.items():
    if _k in eg:
        eg[_k]['roster'] = _v
D['evergaol'] = eg
D['slotVariants'] = {rid: [i for i, x in enumerate(v[4]) if x]
                     for rid, v in D['slots'].items() if len(v) > 4}
print('监牢：', {k: (v['letter'], v['tierCN'], v['variants'], len(v.get('roster', []))) for k, v in eg.items()})
emo = [rid for rid in D['slotCN'] if D['slotCN'][rid] == '（本体未占用）']
print('槽中文名已生成；行名为空的槽 %d 个' % len(emo))
# 覆盖率检查：种子里真正用到的槽，中文名不能是"未占用"
used_slots = set()
for pid, rows in D['patterns'].items():
    for r in rows:
        used_slots.add(str(r[2]))
noName = sorted(s for s in used_slots if D['slotCN'].get(s, '（本体未占用）') == '（本体未占用）')
print('★ 种子里用到但解析不出中文名的槽：%d 个 %s' % (len(noName), noName[:20]))
for rid in ['5000', '5002', '5010', '5349', '5350', '5352', '5362', '5367', '4000', '4090', '4100', '4130',
            '3030', '3000', '3200', '3400', '4500', '4551', '4678', '4552', '4555', '4940']:
    if rid in D['slotCN']:
        print('   %-5s %-40s → %s' % (rid, (D['slots'][rid][0] or '')[:40], D['slotCN'][rid]))

# ---------- 5) 缩圈：中文名 + 按地形的候选池（第1夜/第2夜分开）----------
PA_CN = {1000: '标准·西南角', 1001: '标准·西·暴风丘墓地', 1002: '标准·西北角', 1003: '标准·南湖',
         1004: '标准·西北湖', 1005: '标准·主城之南', 1006: '标准·主城西北', 1007: '标准·湖东北',
         1008: '标准·圣桥东', 1009: '标准·雾林西南', 1010: '标准·雾林西北池塘', 1011: '标准·东北角',
         1021: '腐败森林·森林东南', 1022: '腐败森林·森林西北', 1023: '雪山·雪山东南',
         1024: '火山·火山口北', 1025: '隐城·诺克拉提欧入口', 1026: '隐城·城内',
         11000: '大空洞·A', 11001: '大空洞·B', 11002: '大空洞·C', 12000: '大空洞·神塔', 12001: '大空洞·神塔B'}
D['playAreaCN'] = {str(k): v for k, v in PA_CN.items()}
combo = {}
for pid, rows in D['patternFlags'].items():
    combo[pid] = (rows[0][1], rows[0][2])
pool = defaultdict(lambda: {'1': set(), '2': set()})
for pid, v in D['playAreas'].items():
    if pid not in combo:
        continue
    rm = str(combo[pid][1])
    pool[rm]['1'].add(v[1])
    pool[rm]['2'].add(v[2])
D['playAreaPool'] = {rm: {'1': sorted(x for x in d['1'] if x and x > 0),
                          '2': sorted(x for x in d['2'] if x and x > 0)} for rm, d in pool.items()}
print()
print('缩圈候选池（按地形）：')
for rm in sorted(D['playAreaPool']):
    print('   地形 %s %-12s  第1夜 %s   第2夜 %s' %
          (rm, RARE_CN.get(int(rm), ''), D['playAreaPool'][rm]['1'], D['playAreaPool'][rm]['2']))

# ---------- 6) 合并中文表进 data.js ----------
D['cn'] = {'boss': {str(k): v for k, v in BOSS_CN.items()}, 'icon': {str(k): v for k, v in ICON_CN.items()},
           'night': {str(k): v for k, v in NIGHT_CN.items()}, 'event': {str(k): v for k, v in EVENT_CN.items()},
           'castle': {str(k): v for k, v in CASTLE_CN.items()}, 'rare': {str(k): v for k, v in RARE_CN.items()},
           'kind': KIND_CN, 'tier': TIER_CN}

out = 'window.NR_DATA=' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';'
open(DAT, 'w', encoding='utf-8').write(out)
print()
print('写回 data.js：', os.path.getsize(DAT), 'B')
