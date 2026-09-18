# -*- coding: utf-8 -*-
"""用 overlay-helper 的标定坐标重建网页数据（底图 750 系，位置以它为准）"""
import os, sys, csv, json, glob, struct, shutil
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import param_lookup as pl

REF = os.environ.get('NR_REF', os.path.join(HERE, 'ref'))
V = os.environ.get('NR_WEBGEN_BIN', r'D:\NRX_tmp\nrx_bin\regulation-bin')
# 工具箱版：默认读【随包自带的精简资产】engine\assets（只含用到的 27 个文件，0.38 MB）
A = os.environ.get('NR_SMITHBOX_ASSETS', os.path.join(HERE, 'assets'))
ICON = os.environ.get('NR_ICON', r'D:\NRX_tmp\map_study\preview\icons')
WEB = os.environ.get('NR_WEB_OUT', r'D:\NightreignModResearch\地图种子编辑器')
STD = 750

def loadp(t):
    rows, rs = pl.parse_param(os.path.join(V, t + '.param'))
    lay, comp = pl.layout(pl.def_fields(t))
    return rows, rs, lay
def gf(row, lay, name):
    FMT = {'s8':'b','u8':'B','dummy8':'B','s16':'h','u16':'H','s32':'i','u32':'I','f32':'f'}
    for (n, off, sz, kind, typ) in lay:
        if n == name:
            if kind == 'bit' or sz == 0 or off + sz > len(row): return None
            return struct.unpack_from('<' + FMT[typ], row, off)[0]
    return None
def jload(sub, table):
    for p in (glob.glob(os.path.join(A, sub, '**', table + '.json'), recursive=True)
              + glob.glob(os.path.join(A, sub, '**', table.lower() + '.json'), recursive=True)):
        for enc in ('utf-8-sig', 'utf-16'):
            try: return json.load(open(p, encoding=enc))
            except BaseException: pass
    return None
def rownames(table):
    d = jload('Param Row Names', table); out = {}
    if not d: return out
    for e in d.get('Entries', []):
        n = e.get('Entries', [])
        out[e.get('ID')] = n[0] if n else ''
    return out

# ---- 1) 标定坐标：picX/picY → 750 系 ----
def o2s(x, y):
    return (round((x - 907.5537109) / 6.045 + 127.26920918617023, 1),
            round((y - 1571.031006) / 6.045 + 242.71771372340424, 1))
POS = {}
with open(os.path.join(REF, 'data', 'csv', 'positions.csv'), encoding='utf-8') as f:
    f.readline()
    for row in csv.reader(f):
        if len(row) < 9: continue
        POS[int(row[0])] = o2s(float(row[7]), float(row[8]))
print('标定坐标条数:', len(POS))

# ---- 2) 底图：用 overlay-helper 的 maps/0~5.jpg（750×750，与坐标同系）----
maps = {}
for i in range(6):
    src = os.path.join(REF, 'data', 'maps', f'{i}.jpg')
    if os.path.exists(src):
        dst = os.path.join(WEB, f'map{i}.jpg')
        shutil.copy(src, dst)
        maps[i] = f'map{i}.jpg'
print('底图:', maps)

# ---- 3) 图标：overlay-helper 的 icons + 我切的地图图标 ----
os.makedirs(os.path.join(WEB, 'ovicons'), exist_ok=True)
for f in glob.glob(os.path.join(REF, 'data', 'icons', '*.png')):
    shutil.copy(f, os.path.join(WEB, 'ovicons', os.path.basename(f)))
# 子目录（nightlord/boss 等）
for d in sorted(glob.glob(os.path.join(REF, 'data', 'icons', '*'))):
    if os.path.isdir(d):
        sub = os.path.join(WEB, 'ovicons', os.path.basename(d))
        os.makedirs(sub, exist_ok=True)
        for f in glob.glob(os.path.join(d, '*.png')):
            shutil.copy(f, os.path.join(sub, os.path.basename(f)))
print('overlay 图标复制完成:', len(os.listdir(os.path.join(WEB, 'ovicons'))))

# ---- 4) 表数据（NRX 测试区）----
lrs, _, laylr = loadp('LotResultSmallBaseAndSpot')
ap, _, layap = loadp('SmallBaseAndSpotAttachPoint'); apn = rownames('SmallBaseAndSpotAttachPoint')
var, _, layv = loadp('SmallBaseMapVariationParam'); vrn = rownames('SmallBaseMapVariationParam')
sp, _, laysp = loadp('SmallBaseAndSpotDefine'); spn = rownames('SmallBaseAndSpotDefine')
pap, _, laypa = loadp('LotResultPlayAreaParam')
flag, _, layf = loadp('LotResultMapPatternFlag')
mp, _, laym = loadp('MapPatternSet'); mpn = rownames('MapPatternSet')
wps, _, laywp = loadp('WorldMapPointParam'); wpn = rownames('WorldMapPointParam')
pad, _, laypad = loadp('PlayAreaCreateDefaultParam')
pcp, _, laypc = loadp('PlayAreaCreateParam'); pcn = rownames('PlayAreaCreateParam')
nb, _, layn = loadp('NightBossMenuParam'); nbn = rownames('NightBossMenuParam')

data = {'meta': {'std': STD, 'maps': maps,
                 'source': os.environ.get('NR_SRC_LABEL', 'NRX测试区 regulation.bin (D038023BAE04AC1C380CBC3A6E540BC0)'),
                 'coordSource': 'overlay-helper 标定坐标(positions.csv → 750系)',
                 'rareMap2map': {'0': 0, '1': 1, '2': 2, '3': 3, '5': 5}}}
# 点位坐标（直接用标定值）
data['pos'] = {str(k): v for k, v in POS.items()}
data['attachPoints'] = {rid: [apn.get(rid, ''), gf(ap[rid], layap, 'gridXNo'), gf(ap[rid], layap, 'gridZNo'),
                              round(gf(ap[rid], layap, 'posX') or 0, 2), round(gf(ap[rid], layap, 'posZ') or 0, 2),
                              gf(ap[rid], layap, 'defaultSmallBase')] for rid in ap}
data['slots'] = {rid: [vrn.get(rid, ''), gf(var[rid], layv, 'modifier1'), gf(var[rid], layv, 'modifier2'),
                       gf(var[rid], layv, 'modifier3')] for rid in var}
data['groups'] = {}
for rid in sp:
    slots = [gf(sp[rid], laysp, f'mapId{i}') for i in range(1, 19)]
    data['groups'][rid] = [spn.get(rid, ''), gf(sp[rid], laysp, 'worldMapPointIconId1'),
                           gf(sp[rid], laysp, 'detailIconId1'), [s for s in slots if s and s > 0]]
pat = defaultdict(list)
for rid, r in lrs.items():
    pat[gf(r, laylr, 'patternId')].append([rid, gf(r, laylr, 'attachId'), gf(r, laylr, 'smallBaseMapId'),
                                          gf(r, laylr, 'variationId'), gf(r, laylr, 'modifier'),
                                          gf(r, laylr, 'mapIndex')])
data['patterns'] = pat
data['playAreas'] = {rid: [gf(r, laypa, 'patternId'), gf(r, laypa, 'playArea1'), gf(r, laypa, 'playArea2'),
                           gf(r, laypa, 'bossId1'), gf(r, laypa, 'bossId2'), gf(r, laypa, 'bossModifier1'),
                           gf(r, laypa, 'bossModifier2'), gf(r, laypa, 'extraBossId1'), gf(r, laypa, 'extraBossId2'),
                           gf(r, laypa, 'extraBossModifier1'), gf(r, laypa, 'extraBossModifier2')] for rid, r in pap.items()}
fl = defaultdict(list); pmap = defaultdict(lambda: [None, None])
for rid, r in flag.items():
    tb, rm = gf(r, layf, 'targetBoss'), gf(r, layf, 'rareMap'); pid = gf(r, layf, 'patternId')
    fl[pid].append([rid, tb, rm, gf(r, layf, 'modifier'), gf(r, layf, 'modifierSet'),
                    gf(r, layf, 'eventFlag'), gf(r, layf, 'patternSetId')])
    if pmap[pid][0] is None: pmap[pid] = [tb, rm]
data['patternFlags'] = fl
data['patternCombo'] = pmap
data['mapPatternSet'] = {rid: [mpn.get(rid, ''), gf(mp[rid], laym, 'weight'), gf(mp[rid], laym, 'patternCount'),
                               [[gf(mp[rid], laym, f'night1BossModifier{i}'), gf(mp[rid], laym, f'night1BossModifier{i}_Count')] for i in range(1, 17)],
                               [[gf(mp[rid], laym, f'night2BossModifier{i}'), gf(mp[rid], laym, f'night2BossModifier{i}_Count')] for i in range(1, 17)],
                               [[gf(mp[rid], laym, f'eventModifier{i}'), gf(mp[rid], laym, f'eventModifier{i}_Count')] for i in range(1, 9)]]
                        for rid in mp}
data['worldMapPoints'] = {rid: [wpn.get(rid, ''), gf(wps[rid], laywp, 'worldMapPointIconId'),
                                gf(wps[rid], laywp, 'gridXNo'), gf(wps[rid], laywp, 'gridZNo'),
                                round(gf(wps[rid], laywp, 'posX') or 0, 2), round(gf(wps[rid], laywp, 'posZ') or 0, 2)]
                          for rid in wps}
data['playAreaInfo'] = {rid: [pcn.get(rid, '')] for rid in pcp}
data['nightBossMenu'] = {rid: [nbn.get(rid, '')] for rid in nb}
data['maxIds'] = {'LotResultSmallBaseAndSpot': max(lrs), 'LotResultMapPatternFlag': max(flag),
                  'LotResultPlayAreaParam': max(pap)}
byrare = defaultdict(list)
for p, v in pmap.items():
    if v[1] is not None: byrare[v[1]].append(p)
data['patternsByRare'] = {k: sorted(v) for k, v in byrare.items()}

with open(os.path.join(WEB, 'data.js'), 'w', encoding='utf-8') as f:
    f.write('window.NR_DATA=')
    json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    f.write(';')
print('data.js', os.path.getsize(os.path.join(WEB, 'data.js')), 'B')
# 抽查几个坐标
for k in (1000, 100, 190, 756, 757):
    print(f'  坐标 {k} → {POS.get(k)}')
