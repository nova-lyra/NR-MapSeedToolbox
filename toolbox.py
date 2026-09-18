# -*- coding: utf-8 -*-
r"""
黑夜君临 · 地图种子工具箱（单文件 exe）
=================================================================
三个功能，都在这一个程序里：
  ① 读取 regulation.bin → 生成地图数据（**自带解包器，不需要 WitchyBND**）
  ② 打开地图编辑器（本地网页，点地图改点位/事件/出生点…，导出 CSV）
  ③ 地图种子锁定（**直接写游戏内存**，不需要第三方 DLL、不需要 me3）

打包：pyinstaller --onefile --windowed --add-data "engine;engine" --add-data "web;web" --add-data "lock;lock" toolbox.py
"""
import os
import re
import sys
import json
import shutil
import subprocess
import threading
import time
import traceback

VERSION = "1.0"
APP = "NRSeedToolbox"

# ----------------------------------------------------------------- 路径
def bundle_dir():
    """打包后 = 解包临时目录(_MEIPASS)；开发态 = 本文件所在目录"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

def runtime_root():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP)

RUN = os.path.join(runtime_root(), "run")          # 可写运行目录（engine/web/work 都在这）
LOG_PATH = os.path.join(runtime_root(), "toolbox.log")
ENG = os.path.join(RUN, "engine")
WEB = os.path.join(RUN, "web")
LOCK = os.path.join(RUN, "lock")
WORK = os.path.join(RUN, "work")
LAST_BIN = os.path.join(runtime_root(), "last_bin.txt")

def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(runtime_root(), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if GUI_LOG is not None:
        try:
            GUI_LOG(line)
        except Exception:
            pass

GUI_LOG = None      # GUI 挂钩：往界面日志框里写


# ----------------------------------------------------------------- 管理员权限（锁定功能必需）
def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """以管理员身份重启自己（会弹 UAC）。返回 True 表示已经拉起来（本进程应退出）"""
    import ctypes
    try:
        args = list(sys.argv[1:])
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, " ".join('"%s"' % a for a in args)
        else:
            exe = sys.executable
            params = " ".join('"%s"' % a for a in [os.path.abspath(sys.argv[0])] + args)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception as e:
        log("提升权限失败：%r" % e)
        return False

# ----------------------------------------------------------------- 物化（把随包文件铺到可写目录）
def _fingerprint(src):
    """给随包目录做一个内容指纹（相对路径 + 大小 + mtime），用来判断要不要重铺"""
    import hashlib
    h = hashlib.md5()
    items = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "build", "dist")]
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(root, f)
            items.append("%s|%d|%d" % (os.path.relpath(p, src), os.path.getsize(p), int(os.path.getmtime(p))))
    for s in sorted(items):
        h.update(s.encode("utf-8", "replace"))
    return h.hexdigest() + "|v" + VERSION


def materialize(force=False):
    """把随包文件铺到可写运行目录。⚠️ 用【内容指纹】判断，别只用版本号
       —— 实测踩过：加了个新脚本但版本号没变 ⇒ 运行时目录还是旧副本 ⇒ ModuleNotFoundError。"""
    for sub in ("engine", "web", "lock"):
        src = os.path.join(bundle_dir(), sub)
        dst = os.path.join(RUN, sub)
        if not os.path.isdir(src):
            continue
        stamp = os.path.join(dst, ".version")
        want = _fingerprint(src)
        have = None
        if os.path.exists(stamp):
            try:
                have = open(stamp, encoding="utf-8").read().strip()
            except Exception:
                have = None
        if force or not os.path.isdir(dst) or have != want:
            shutil.rmtree(dst, ignore_errors=True)
            os.makedirs(dst, exist_ok=True)            # ⚠️ rmtree 可能被占用文件挡住而静默失败
            shutil.copytree(src, dst, dirs_exist_ok=True)   # ⇒ 必须允许目标已存在，否则 FileExistsError 崩
            try:
                open(stamp, "w", encoding="utf-8").write(want)
            except Exception:
                pass
    os.makedirs(WORK, exist_ok=True)

# ----------------------------------------------------------------- 子脚本分派（冻结后 sys.executable = 本 exe）
def dispatch(name):
    """--run-script <脚本名> [脚本自己的参数…]：按子进程身份跑 engine 里的脚本
    ⚠️ 必须把 sys.argv 换成【脚本自己的】argv，否则脚本会把 exe 的参数（--run-script/脚本名）当成自己的参数
       —— 实测踩过：刷新数据.py 把"刷新数据.py"当成 bin 路径。"""
    path = os.path.join(ENG, name)
    if not os.path.exists(path):
        sys.stderr.write("找不到脚本 %s\n" % path)
        return 2
    sys.argv = [name] + list(sys.argv[3:])
    sys.path.insert(0, ENG)
    os.chdir(ENG)
    import runpy
    runpy.run_path(path, run_name="__main__")
    return 0

def pycmd(script):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-script", os.path.basename(script)]
    return [sys.executable, script]

# ----------------------------------------------------------------- ① 读 bin
def refresh(bin_path, out_dir=WEB, on_line=None):
    """跑完整刷新流程：解包(自带) → 生成 data.js。返回 (ok, 说明)"""
    env = dict(os.environ)
    env["NR_TOOLBOX_WORK"] = WORK
    env["NR_TOOLBOX_ERRLOG"] = os.path.join(runtime_root(), "refresh_last_error.txt")
    env["NR_WEB_OUT"] = out_dir
    env["NR_REF"] = os.path.join(ENG, "ref")
    env["NR_SMITHBOX_ASSETS"] = os.path.join(ENG, "assets")
    env["NR_SRC_LABEL"] = "%s  (%s)" % (os.path.basename(os.path.dirname(os.path.dirname(bin_path))) or bin_path,
                                        "用户选择")
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = pycmd(os.path.join(ENG, "刷新数据.py")) + [bin_path, "--out-dir", out_dir]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        p = subprocess.Popen(cmd, cwd=ENG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", **kw)
        for line in p.stdout:
            line = line.rstrip("\n")
            if on_line:
                on_line(line)
        rc = p.wait()
    except Exception as e:
        log("刷新异常：%r" % e)
        return False, "刷新异常：%r" % e
    dat = os.path.join(out_dir, "data.js")
    ok = (rc == 0 and os.path.exists(dat))
    return ok, ("data.js %d 字节" % os.path.getsize(dat)) if ok else ("刷新失败 rc=%s" % rc)

# ----------------------------------------------------------------- ③ 锁定（写游戏内存）
# ----------------------------------------------------------------- ③ 写回 bin（CSV → 直接改进 regulation.bin）
def guess_table(csv_name, table_names):
    """从 CSV 文件名推表名：我们的导出名 = <表名>_modify[_start]_<pid>.csv"""
    base = os.path.basename(csv_name)
    for t in sorted(table_names, key=len, reverse=True):
        if base.startswith(t):
            return t
    return None


def apply_csv_files(bin_path, csv_paths):
    """把若干份 CSV 写回 bin（就地覆盖 + 自动备份）。返回 (ok, 说明)"""
    sys.path.insert(0, ENG)
    import nr_bin_write as NW
    raw, dec, inner, info, entries = NW.read_bin(bin_path)
    tables = [n[:-6] if n.endswith(".param") else n for (n, d, m) in entries]
    jobs, unknown = [], []
    for c in csv_paths:
        t = guess_table(c, tables)
        if t:
            jobs.append((t, c))
        else:
            unknown.append(os.path.basename(c))
    if not jobs:
        return False, "认不出这些 CSV 属于哪张表：%s\n（文件名应当以表名开头，例如 LotResultSmallBaseAndSpot_modify_243.csv）" % "、".join(unknown)
    rep = NW.apply_to_bin(bin_path, jobs)
    if unknown:
        rep += "\n  ⚠ 这几份没认出来、已跳过：" + "、".join(unknown)
    return ("✅ 已写回" in rep), rep


# ----------------------------------------------------------------- ③b 锁定（推荐：DLL 方式，和以前那套锁定工具同一机制）
# me3 会整目录加载一个 mod；这里定义"哪些子目录名算资源层"，用来认出 mod 目录
LAYERS = ("action", "chr", "event", "map", "material", "menu", "msg", "parts",
          "script", "sd", "sfx", "shader", "regulation")


def has_layers(d):
    """这个目录里有资源层吗（chr/parts/msg… 至少一个）⇒ 是"mod 内容的家" """
    try:
        names = set(n.lower() for n in os.listdir(d))
    except Exception:
        return False
    return any(n in names for n in LAYERS)


def mod_root_of(bin_path):
    """这份 bin 对应的【mod 目录】（= me3 要整目录加载的那个目录）。
    规则（按顺序，最贴近现状优先）：
      ① bin 所在目录本身就是 mod 目录（含 chr/parts/…）⇒ 用它（MMV 这类标准 mod 就是这样）
      ② 否则在它的子目录里找 mod 目录（例：NRX测试区\\regulation.bin 的 mod 内容在 NRX测试区\\mod）
      ③ 都不满足 ⇒ 退回 bin 所在目录（不猜、不乱动）
    """
    if not bin_path or not os.path.exists(bin_path):
        return ""
    d = os.path.dirname(os.path.abspath(bin_path))
    if has_layers(d):
        return d
    subs = []
    try:
        for e in sorted(os.listdir(d)):
            p = os.path.join(d, e)
            if os.path.isdir(p) and has_layers(p):
                subs.append(p)
    except Exception:
        pass
    if subs:
        for p in subs:                      # 多个候选时优先叫 mod 的
            if os.path.basename(p).lower() == "mod":
                return p
        return subs[0]
    return d


def _md5(p):
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def sync_bin_into_mod(bin_path):
    """保证【游戏启动时读到的 bin】就是【你读的/改好的那份】。
    mod 目录里有自己的 regulation.bin 时把它替换成你这份（原份先备份，可回退）。
    返回 (mod_dir, 说明)"""
    mod = mod_root_of(bin_path)
    src = os.path.abspath(bin_path)
    dst = os.path.join(mod, "regulation.bin") if mod else ""
    if not mod:
        return "", "认不出 mod 目录，跳过同步"
    if os.path.normcase(src) == os.path.normcase(dst):
        return mod, "bin 本来就在 mod 目录里，直接用它"
    if not os.path.exists(dst):
        return mod, "mod 目录里没有别的 regulation.bin，直接用它"
    try:
        if os.path.getsize(src) == os.path.getsize(dst) and _md5(src) == _md5(dst):
            return mod, "mod 里的 bin 与你读的那份内容相同，不用动"
    except Exception:
        pass
    bak = dst + ".bak_工具箱_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(dst, bak)
    shutil.copy2(src, dst)
    return mod, ("已把你改好的 bin 同步进 mod（这样游戏进去读到的就是你改的那份）：\n"
                 "      %s\n   →  %s\n      原 mod 里的 bin 已备份：%s" % (src, dst, bak))


def me3_exe():
    """找【你机器上那个 me3.exe】——优先 .me3 双击关联的那个（= 你平时启动 mod 用的那条路），
       再退回工具箱自带副本。返回 (路径, 说明)"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"me3.mod-profile\shell\open\command")
        cmd = winreg.QueryValueEx(k, "")[0]
        m = re.search(r'"([^"]*me3\.exe)"', cmd, re.I) or re.search(r'(\S*me3\.exe)', cmd, re.I)
        if m:
            p = os.path.expandvars(m.group(1).strip('"'))
            if os.path.isfile(p):
                return p, "（.me3 双击关联的那个 = 你平时用的）"
    except Exception:
        pass
    p = os.path.join(LOCK, "needed", "me3", "bin", "me3.exe")
    if os.path.isfile(p):
        return p, "（工具箱自带的副本）"
    return "", ""


def lock_paths(bin_path=None):
    """返回 (dll, ini, me3副本, 锁定器) 四个路径。
    锁定器（.me3）放在【这份 bin 对应的 mod 目录的上一级】，与你能正常用的
    `NRX测试区\\Launch NRX seedtest.me3` 完全同一位置、同一套路。"""
    need = os.path.join(LOCK, "needed")
    prof = os.path.join(RUN, "SeedLock.me3")
    if bin_path and os.path.exists(bin_path):
        mod = mod_root_of(bin_path)
        if mod:
            prof = os.path.join(os.path.dirname(mod), "SeedLock.me3")
    return (os.path.join(need, "NightreignRandomizerHelper.dll"),
            os.path.join(need, "NightreignRandomizerHelper_config.ini"),
            os.path.join(need, "me3", "bin", "me3.exe"),
            prof)


def lock_ini_write(seed, on=True):
    """写锁定 DLL 的配置：patchSeed=true + seed=0x…（游戏启动时由 DLL 执行一次）"""
    dll, ini, me3, prof = lock_paths()
    os.makedirs(os.path.dirname(ini), exist_ok=True)
    open(ini, "w", encoding="utf-8", newline="\r\n").write(
        "; 由工具箱写入 —— 锁定 DLL 在游戏启动时读这个文件\n"
        "[settings]\n\n"
        "; 是否覆盖出击种子（游戏启动时执行一次）\n"
        "patchSeed = %s\n\n"
        "; 要锁定的种子\n"
        "seed = 0x%08X\n\n"
        "; 备用存档后缀：只读写 NRX.tst 副本，绝不碰你的主档\n"
        "saveFileExtension = tst\n" % ("true" if on else "false", seed & 0xFFFFFFFF))
    return ini


def mod_info(bin_path):
    """看一眼"这份 bin 所在的 mod 目录"：装了哪些资源层、有没有无缝联机。
       ⇒ exe 就按这个当 mod 启动器（me3 的 packages = 整目录加载，资源层全带）。"""
    d = os.path.dirname(os.path.abspath(bin_path)) if bin_path else ""
    info = {"dir": d, "layers": [], "files": 0, "coop": False, "coop_dll": ""}
    if not d or not os.path.isdir(d):
        return info
    for e in sorted(os.listdir(d)):
        p = os.path.join(d, e)
        if os.path.isdir(p):
            n = sum(len(f) for _, _, f in os.walk(p))
            info["layers"].append((e, n))
            info["files"] += n
        else:
            info["files"] += 1
    sc = os.path.join(os.path.dirname(d), "SeamlessCoop", "nrsc.dll")
    if os.path.exists(sc):
        info["coop"], info["coop_dll"] = True, sc
    return info


def pick_savefile():
    """给 me3 挑存档文件名（**不写死**，换电脑也能用）。★ 先把所有候选收集齐再判优，
       不能"遍历到哪个目录就用哪个"（多 steamid 目录时会挑错）。
       ① 任一存档目录里已有 `NRX.tst`（已实测通过的默认）⇒ 用它
       ② 否则用最近改动的 `*.tst`（测试副本）
       ③ 否则用最近的主档名 + `.tst`（只动副本，不碰主档）
       ④ 都没有 ⇒ "NRX.tst" 兜底
    """
    try:
        base = os.path.join(os.environ.get("APPDATA", ""), "Nightreign")
        if os.path.isdir(base):
            subs = [os.path.join(base, s) for s in os.listdir(base)]
            subs = [d for d in subs if os.path.isdir(d)]
            for d in subs:                                   # ①
                if os.path.isfile(os.path.join(d, "NRX.tst")):
                    return "NRX.tst"
            cand = []
            for d in subs:                                   # ②
                try:
                    for f in os.listdir(d):
                        if f.lower().endswith(".tst"):
                            cand.append((os.path.getmtime(os.path.join(d, f)), f))
                except Exception:
                    pass
            if cand:
                cand.sort(reverse=True)
                return cand[0][1]
            cand = []
            for d in subs:                                   # ③
                try:
                    for f in os.listdir(d):
                        if f.lower().endswith(".sl2"):
                            cand.append((os.path.getmtime(os.path.join(d, f)),
                                         os.path.splitext(f)[0] + ".tst"))
                except Exception:
                    pass
            if cand:
                cand.sort(reverse=True)
                return cand[0][1]
    except Exception:
        pass
    return "NRX.tst"


def lock_profile_write(bin_path=None):
    """生成 me3 锁定器（.me3）。结构**照抄你一直在用、能正常启动的那份**
    `NRX测试区\\Launch NRX seedtest.me3`，只把路径按「你读的那份 bin」自动改好：
        [[packages]] id/path = "<mod 目录名>"  ← 整个 mod（chr/parts/msg…资源层全带）一起加载
        [[natives]]  我们的锁定 DLL（load_early = 游戏启动瞬间写种子）
        savefile = "NRX.tst"                   ← 只动副本，绝不碰你的主档
    """
    dll, ini, me3, prof = lock_paths(bin_path)
    pkg = ""
    if bin_path and os.path.exists(bin_path):
        mod = mod_root_of(bin_path)
        if mod and not os.path.isfile(os.path.join(mod, "nightreign.exe")):
            # 本体（目录里有 nightreign.exe）不挂 mod 包，只挂锁定 DLL；mod 才挂整目录
            nm = os.path.basename(mod)
            pkg = '[[packages]]\nid = "%s"\npath = "%s"\n\n' % (nm, nm)
    txt = ('profileVersion = "v1"\nsavefile = "%s"\nstart_online = true\ndisable_arxan = true\n\n'
           '[[supports]]\ngame = "nightreign"\n\n' % pick_savefile()) + pkg + \
          '[[natives]]\npath = "%s"\nload_early = true\n' % dll.replace("\\", "\\\\")
    os.makedirs(os.path.dirname(prof), exist_ok=True)
    open(prof, "w", encoding="utf-8", newline="\n").write(txt)
    return prof


def lock_launch(bin_path=None, mode="lock", coop=False):
    """锁定 + 自动启动（全自动三步，玩家不用再做别的）：
      ① 把【你读的/改好的那份 bin】同步进 mod 目录（保证游戏进去读到的就是你改的那份）
      ② 在 mod 目录的上一级生成 me3 锁定器（.me3）—— 整个 mod 一起加载
      ③ 用【你机器上那个 me3】启动（= 你双击 .me3 走的同一条路）
    返回 (ok, 说明)"""
    dll, ini, me3copy, prof = lock_paths(bin_path)
    if not os.path.exists(dll):
        return False, "缺少锁定 DLL：%s" % dll
    notes = []
    if bin_path and os.path.exists(bin_path):
        note = sync_bin_into_mod(bin_path)[1]
        if note:
            notes.append(note)
    prof = lock_profile_write(bin_path)
    # ★ 启动 = 用系统关联打开这个 .me3（= 你手动双击那一下，走的就是你能正常用的那个 me3）
    exe, how = me3_exe()
    try:
        os.startfile(prof)
        notes.append("已启动游戏：系统关联打开 .me3（与你双击完全同一条路）"
                     + ("；关联的 me3 = %s %s" % (exe, how) if exe else ""))
    except Exception as e:
        if not exe:
            return False, "启动失败：%r\n（锁定器已生成：%s）" % (e, prof)
        try:
            subprocess.Popen([exe, "launch", "-p", prof], cwd=os.path.dirname(exe))
        except Exception as e2:
            return False, "启动失败：%r / %r\n（me3：%s）" % (e, e2, exe)
        notes.append("已启动游戏（me3）：%s %s" % (exe, how))
    notes.append("锁定器：%s" % prof)
    return True, "\n".join(notes)


def lock_dll_log(n=12):
    """读锁定 DLL 的日志（里面会打印 Game state: seed 0x……, pattern N —— 可用来核对锁的是哪张）"""
    dll, ini, me3, prof = lock_paths()
    p = os.path.join(os.path.dirname(dll), "NightreignRandomizerHelper_log.txt")
    if not os.path.exists(p):
        return "（还没有日志：)%s）先用锁定版启动过一次游戏才会生成" % p
    lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
    tail = [l for l in lines if l.strip()][-n:]
    return "【锁定 DLL 日志】%s\n" % p + "\n".join(tail)


class Locker:
    def __init__(self):
        self.probe = None
        self.seedmod = None
        self.thread = None
        self.stop_flag = False
        self.target = None
        self.orig = None

    def _load(self):
        if self.probe is None:
            sys.path.insert(0, LOCK)
            import importlib.util
            def load(nm, fn):
                spec = importlib.util.spec_from_file_location(nm, os.path.join(LOCK, fn))
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                return m
            self.seedmod = load("nr_seed_tool", "nr_seed_tool.py")
            self.probe = load("seed_probe", "seed_probe.py")
        return self.probe, self.seedmod

    def _ptr_of(self, h, pid):
        """拿到"种子结构体"的指针：优先 模块基址+偏移，失败就退回绝对地址。返回 (ptr, 用的地址) 或 (0, 0)
        ⭐ 实测：列模块（CreateToolhelp32Snapshot）在普通权限下可能失败 ⇒ 必须有"绝对地址"这条兜底
           （该 exe 没开 ASLR：逆向它的 dll 用的就是绝对 VA 0x143C13258）"""
        import struct as _s
        probe, _ = self._load()
        cands = []
        mb = probe.module_base(pid)
        if mb:
            cands.append(mb[0] + (probe.DEFAULT_GLOBAL - 0x140000000))
        cands.append(probe.DEFAULT_GLOBAL)
        for a in cands:
            raw = probe.rpm(h, a, 8)
            if not raw:
                continue
            v = _s.unpack("<Q", raw)[0]
            if 0x10000 < v < 0x7FFFFFFFFFFF:
                return v, a
        return 0, 0

    def _pick(self):
        """在【全部】nightreign 进程里找一个"能打开、且能读到种子结构指针"的。
        ⭐ 实测：机器上同时有 3 个 nightreign 进程（启动器/本体/…），只有其中一部分能读
           —— 只取第一个会踩到"错误码 5 拒绝访问"。
        返回 (pid, handle, ptr, gva, 说明)"""
        probe, _ = self._load()
        import ctypes as _c
        pids = probe.alive_pids()
        if not pids:
            zom = probe.find_pids()
            return None, None, 0, 0, ("没找到【正在运行】的 nightreign.exe" +
                                      ("（有 %d 个已退出的残留进程，已忽略）" % len(zom) if zom else "") +
                                      " —— 先正常启动游戏，进到圆桌再点本按钮")
        tried = []
        for pid in pids:
            h = probe.open_proc(pid)
            if not h:
                tried.append("%s 打不开(错误码 %s)" % (pid, _c.get_last_error()))
                continue
            ptr, gva = self._ptr_of(h, pid)
            if not ptr:
                tried.append("%s 读不到指针(可能还没进圆桌)" % pid)
                continue
            return pid, h, ptr, gva, ""
        return pids[0], None, 0, 0, ("试过全部 nightreign 进程： " + "；".join(tried) +
                                     "（若都打不开，右键本工具→以管理员身份运行）")

    def status(self):
        """返回 (pid, seed, pattern, struct_addr, 说明)；读不到时说明会写清卡在哪一步
        （⛔ 别只给 None —— 用户看不出是"没开游戏"还是"打不开进程"还是"还没进圆桌"）"""
        import struct as _s
        probe, _ = self._load()
        pid, h, ptr, gva, why = self._pick()
        if not ptr:
            return pid, None, None, None, why
        pat = None
        raw2 = probe.rpm(h, ptr + probe.DEFAULT_OFF_PATTERN, 4)
        if raw2:
            pat = _s.unpack("<I", raw2)[0]
        raw3 = probe.rpm(h, ptr + probe.DEFAULT_OFF, 4)
        if not raw3:
            return pid, None, pat, ptr, "读到结构（0x%X）但种子字段读不出来" % ptr
        seed = _s.unpack("<I", raw3)[0]
        return pid, seed, pat, ptr, "（进程 %s，地址 0x%X）" % (pid, gva)

    def seed_of(self, pattern_id, rare, tries=5):
        """给一张图算锁定种子。
        ⚠️ 实测接口：nr_seed_tool.find_seed(offset, conjured) 返回【候选种子列表】；
                    get_pattern(seed, conjured) 返回 (偏移, 说明)，其中偏移 = patternId % 40。
        地形码：普通 0（按权重抽）/ 雪山 11 / 火山 12 / 腐森 13 / 隐城 15（召唤变幻大地时锁定）"""
        _, sm = self._load()
        conj = {0: 0, 1: 11, 2: 12, 3: 13, 5: 15}.get(int(rare), 0)
        off = int(pattern_id) % 40
        cands = sm.find_seed(off, conj, tries=tries)
        good = []
        for s in (cands or []):
            try:
                chk, _how = sm.get_pattern(s, conj)
            except Exception:
                continue
            if chk == off:
                good.append(s)
        return (good[0] if good else None), (len(good), off, conj)

    def seed_check(self, seed, rare):
        """复核某个种子会抽到哪张图（返回 (偏移, 说明)）"""
        _, sm = self._load()
        conj = {0: 0, 1: 11, 2: 12, 3: 13, 5: 15}.get(int(rare), 0)
        return sm.get_pattern(seed, conj)

    def write_once(self, seed):
        probe, _ = self._load()
        import struct as _s
        pid, h, ptr, gva, why = self._pick()
        if not ptr:
            return False, why
        if self.orig is None:                     # 记下"我改之前"的种子，供「撤销」用
            raw = probe.rpm(h, ptr + probe.DEFAULT_OFF, 4)
            self.orig = _s.unpack("<I", raw)[0] if raw else None
        ok = probe.wpm(h, ptr + probe.DEFAULT_OFF, _s.pack("<I", seed & 0xFFFFFFFF))
        return (True, "已写入 0x%08X（进程 %s，地址 0x%X）" % (seed, pid, gva)) if ok else (False, "写入失败")

    def start(self, seed, interval=0.3):
        self.stop_flag = False
        self.target = seed
        def loop():
            while not self.stop_flag:
                ok, msg = self.write_once(self.target)
                if not ok:
                    log("锁定写入：" + msg)
                    break
                time.sleep(interval)
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag = True

    def restore(self):
        if self.orig is not None:
            ok, msg = self.write_once(self.orig)
            return ok, msg
        return False, "没有记录到原始种子"

# ----------------------------------------------------------------- 数据（读 data.js 给界面用）
def load_data():
    p = os.path.join(WEB, "data.js")
    if not os.path.exists(p):
        return None
    t = open(p, encoding="utf-8").read()
    m = re.search(r"window\.NR_DATA\s*=\s*(\{.*\})\s*;?\s*$", t, re.S)
    if not m:
        m = re.search(r"window\.NR_DATA\s*=\s*(\{.*\})", t, re.S)
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

NIGHT_CN = {0: "黑夜野兽 格拉狄乌斯", 1: "艾德雷", 2: "格诺斯塔", 3: "玛利斯", 4: "利普拉",
            5: "弗格尔", 6: "卡莉果", 7: "布德奇冥", 8: "哈尔莫尼亚", 9: "斯特拉格斯"}
RARE_CN = {0: "普通", 1: "雪山", 2: "火山", 3: "腐败森林", 4: "大空洞", 5: "隐城"}

# ----------------------------------------------------------------- GUI
def main_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("黑夜君临 · 地图种子工具箱  v%s" % VERSION)
    root.geometry("940x620")
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass

    box = ttk.Frame(root, padding=10)
    box.pack(fill="both", expand=True)

    # ---- ① 读取 bin
    f1 = ttk.LabelFrame(box, text="① 读取 regulation.bin（生成地图数据）", padding=8)
    f1.pack(fill="x")
    ttk.Label(f1, text="你自己的 regulation.bin：").grid(row=0, column=0, sticky="w")
    var_bin = tk.StringVar(value=(open(LAST_BIN, encoding="utf-8").read().strip() if os.path.exists(LAST_BIN) else ""))
    ttk.Entry(f1, textvariable=var_bin, width=72).grid(row=0, column=1, padx=6, sticky="we")
    ttk.Button(f1, text="选择文件…", command=lambda: choose_bin()).grid(row=0, column=2)
    bar = ttk.Progressbar(f1, mode="determinate", maximum=100, length=880)
    bar.grid(row=1, column=0, columnspan=3, pady=(8, 4), sticky="we")
    stat = ttk.Label(f1, text="还没读取")
    stat.grid(row=2, column=0, columnspan=3, sticky="w")

    def choose_bin():
        p = filedialog.askopenfilename(title="选择 regulation.bin", filetypes=[("regulation.bin", "regulation.bin"), ("所有文件", "*.*")])
        if p:
            var_bin.set(p)

    def do_read():
        p = var_bin.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showwarning("提示", "先选一个存在的 regulation.bin")
            return
        try:
            open(LAST_BIN, "w", encoding="utf-8").write(p)
        except Exception:
            pass
        bar["value"] = 5
        stat.config(text="读取中…（解包 + 生成数据，约 10~30 秒）")
        log("开始读取：%s" % p)

        def on_line(l):
            if "①" in l or "解包" in l:
                bar["value"] = 25
            elif "②" in l:
                bar["value"] = 55
            elif "③" in l and "补字段" in l:
                bar["value"] = 80
            elif "④" in l:
                bar["value"] = 92
            log("   " + l)

        def work():
            ok, msg = refresh(p, WEB, on_line)
            def fin():
                bar["value"] = 100 if ok else 0
                stat.config(text=("✅ 完成：" + msg + "　→ 现在可以点 ② 打开编辑器") if ok
                            else ("❌ " + msg + "（详见日志）"))
                log(("读取完成：" if ok else "读取失败：") + msg)
                if not ok:
                    messagebox.showerror("读取失败", msg + "\n\n详细日志：\n" + LOG_PATH)
            root.after(0, fin)
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(f1, text="开始读取", command=do_read).grid(row=3, column=0, pady=(4, 0))

    # ---- ② 打开编辑器
    f2 = ttk.LabelFrame(box, text="② 地图编辑器（网页）", padding=8)
    f2.pack(fill="x", pady=(8, 0))
    def open_web():
        p = os.path.join(WEB, "index.html")
        if not os.path.exists(p):
            messagebox.showwarning("提示", "还没有数据，先做第 ① 步")
            return
        os.startfile(p) if os.name == "nt" else subprocess.Popen(["xdg-open", p])
        log("已打开编辑器网页：%s" % p)
    ttk.Button(f2, text="打开地图编辑器", command=open_web).pack(side="left")
    ttk.Label(f2, text="（点地图上的点位/事件 → 改 → ⑨ 一次导出全部已修改 → 得到 CSV）").pack(side="left", padx=8)

    # ---- ③ 写回 bin（不用 Smithbox）
    f3 = ttk.LabelFrame(box, text="③ 写回 bin（把导出的 CSV 直接写进 regulation.bin —— 不需要 Smithbox）", padding=8)
    f3.pack(fill="x", pady=(8, 0))
    var_csv = tk.StringVar(value="")
    ttk.Entry(f3, textvariable=var_csv, width=70).grid(row=0, column=0, columnspan=2, padx=6, sticky="we")

    def choose_csvs():
        ps = filedialog.askopenfilenames(title="选择编辑器导出的 CSV（可多选）",
                                         filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")])
        if ps:
            var_csv.set(";".join(ps))
    ttk.Button(f3, text="选择 CSV…", command=choose_csvs).grid(row=0, column=2)
    wstat = ttk.Label(f3, text="（先做第 ① 步读过 bin；然后在编辑器里改完、导出 CSV、再回到这里）")
    wstat.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))

    def do_write():
        binp = var_bin.get().strip()
        csvs = [x for x in var_csv.get().split(";") if x.strip()]
        if not binp or not os.path.exists(binp):
            messagebox.showwarning("提示", "先在第 ① 步选好 regulation.bin"); return
        if not csvs:
            messagebox.showwarning("提示", "先选编辑器导出的 CSV"); return
        if not messagebox.askyesno("确认写回", "将把选中的 CSV 写进：\n%s\n\n（会自动先备份成 .bak_时间戳，随时能还原）\n\n确认继续？" % binp):
            return
        wstat.config(text="写回中…")
        try:
            ok, msg = apply_csv_files(binp, csvs)
            log("写回：\n" + msg)
            wstat.config(text=("✅ 写回完成（已自动备份）" if ok else "❌ " + msg.replace("\n", "　")[:120]))
            (messagebox.showinfo if ok else messagebox.showerror)("写回结果", msg)
        except Exception as e:
            log("写回异常：%r\n%s" % (e, traceback.format_exc()))
            wstat.config(text="❌ 写回异常（看日志）")
            messagebox.showerror("写回异常", repr(e))

    ttk.Button(f3, text="★ 写回 bin（自动备份）", command=lambda: thread(do_write)).grid(row=1, column=0, pady=(6, 0))

    # ---- ④ 锁定
    f4 = ttk.LabelFrame(box, text="④ 地图种子锁定（直接写游戏内存，不需要额外 DLL/加载器）", padding=8)
    f4.pack(fill="x", pady=(8, 0))
    mk = tk.Frame(f4); mk.pack(fill="x")
    ttk.Label(f4, text="怎么用：① 选好 夜王/地形/图 → ② 点【锁定这张图（推荐·DLL 方式）】→ ③ 点【用锁定版启动游戏】→ "
                       "④ 进游戏点出击 = 你选的这张图。（这个流程不需要管理员权限）",
              foreground="#555", wraplength=880, justify="left").pack(anchor="w", pady=(0, 6))
    ttk.Label(mk, text="夜王").pack(side="left")
    var_night = tk.StringVar()
    cb_night = ttk.Combobox(mk, textvariable=var_night, width=22, state="readonly")
    cb_night.pack(side="left", padx=4)
    ttk.Label(mk, text="地形").pack(side="left")
    var_rare = tk.StringVar()
    cb_rare = ttk.Combobox(mk, textvariable=var_rare, width=12, state="readonly")
    cb_rare.pack(side="left", padx=4)
    ttk.Label(mk, text="图（排布号）").pack(side="left")
    var_pid = tk.StringVar()
    cb_pid = ttk.Combobox(mk, textvariable=var_pid, width=40, state="readonly")
    cb_pid.pack(side="left", padx=4)

    lk2 = tk.Frame(f4); lk2.pack(fill="x", pady=(6, 0))
    var_info = tk.StringVar(value="（还没读游戏状态）")
    ttk.Label(lk2, textvariable=var_info).pack(side="left")
    ttk.Button(lk2, text="看游戏当前状态", command=lambda: thread(status_once)).pack(side="right", padx=4)
    # ★ 当前用的 mod（= 你 ① 读的那份 bin 所在目录）—— 完全自动，不需要另外选
    var_mod = tk.StringVar(value="")
    ttk.Label(f4, textvariable=var_mod, foreground="#2a7", wraplength=880, justify="left").pack(anchor="w", pady=(4, 0))

    def upd_mod(*_a):
        b = var_bin.get().strip()
        if b and os.path.exists(b):
            d = mod_root_of(b) or os.path.dirname(os.path.abspath(b))
            n, lay = 0, []
            try:
                for e in sorted(os.listdir(d)):
                    p = os.path.join(d, e)
                    if os.path.isdir(p):
                        c = sum(len(f) for _, _, f in os.walk(p))
                        lay.append("%s %d" % (e, c)); n += c
                    else:
                        n += 1
            except Exception:
                pass
            var_mod.set("当前 mod：%s\n锁定启动就按这个 mod 整体加载（%d 个文件）：%s%s\n"
                        "你改好的 bin 会在锁定时同步进这个目录 —— 保证游戏进去读到的就是你改的那份。"
                        % (d, n, "｜".join(lay[:8]), "…" if len(lay) > 8 else ""))
        else:
            var_mod.set("当前 mod：（先在第 ① 步选一份 regulation.bin）")
    try:
        var_bin.trace_add("write", lambda *a: upd_mod())
    except Exception:
        pass
    upd_mod()
    locker = Locker()
    lkD = tk.Frame(f4); lkD.pack(fill="x", pady=(6, 0))
    ttk.Button(lkD, text="★ 锁定这张图（推荐·DLL 方式）", command=lambda: thread(do_lock_dll)).pack(side="left")
    ttk.Button(lkD, text="用锁定版启动游戏", command=lambda: thread(do_launch)).pack(side="left", padx=6)
    ttk.Button(lkD, text="解除锁定", command=lambda: thread(do_unlock_dll)).pack(side="left", padx=6)
    ttk.Button(lkD, text="看锁定日志", command=lambda: thread(do_show_dll_log)).pack(side="left", padx=6)
    ttk.Label(f4, text="↑ 用法就一步：选好图 →【★ 锁定这张图】→ 弹窗点“是” = 自动用这个 mod 启动游戏。"
                       "（种子由锁定 DLL 在游戏启动瞬间写进去，所以一锁就锁死；进去看到的就是这个 mod 的完整内容）",
              foreground="#555", wraplength=880, justify="left").pack(anchor="w", pady=(2, 6))
    ttk.Label(f4, text="备用（不用 DLL：游戏已经开着时，直接往内存里写）—— 实测不如上面那条稳，一般不推荐：",
              foreground="#777").pack(anchor="w")
    lk3 = tk.Frame(f4); lk3.pack(fill="x", pady=(2, 0))
    ttk.Button(lk3, text="备用：写内存（游戏开着时试）", command=lambda: thread(do_lock)).pack(side="left")
    ttk.Button(lk3, text="停止写内存", command=lambda: thread(do_unlock)).pack(side="left", padx=6)
    ttk.Button(lk3, text="撤销：写回原来的种子值", command=lambda: thread(do_restore)).pack(side="left", padx=6)

    # ---- 日志
    f4 = ttk.LabelFrame(box, text="日志", padding=6)
    f4.pack(fill="both", expand=True, pady=(8, 0))
    txt = tk.Text(f4, height=12, wrap="none")
    txt.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(f4, orient="vertical", command=txt.yview)
    sb.pack(side="right", fill="y")
    txt.config(yscrollcommand=sb.set)

    def gui_log(line):
        txt.insert("end", line + "\n")
        txt.see("end")
    global GUI_LOG
    GUI_LOG = gui_log

    def thread(fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # 填组合框
    DATA = load_data()
    def fill_combos():
        nonlocal DATA
        DATA = load_data()
        if not DATA:
            cb_night["values"] = ["（先做第 ① 步读 bin）"]; return
        pf = DATA.get("patternFlags", {})
        nights, rares = set(), set()
        for pid, rows in pf.items():
            for r in rows:
                if len(r) > 2:
                    nights.add(int(r[1])); rares.add(int(r[2]))
        cb_night["values"] = ["%d %s" % (n, NIGHT_CN.get(n, n)) for n in sorted(nights)]
        cb_rare["values"] = ["%d %s" % (n, RARE_CN.get(n, n)) for n in sorted(rares)]
        if not var_night.get() and cb_night["values"]:
            var_night.set(cb_night["values"][0])
        if not var_rare.get() and cb_rare["values"]:
            var_rare.set(cb_rare["values"][0])
        on_combo()

    def on_combo(*_):
        if not DATA:
            return
        try:
            n = int(var_night.get().split()[0]); rm = int(var_rare.get().split()[0])
        except Exception:
            return
        ids = []
        for pid, rows in DATA.get("patternFlags", {}).items():
            if any(len(r) > 2 and int(r[1]) == n and int(r[2]) == rm for r in rows):
                ids.append(int(pid))
        ids.sort()
        cb_pid["values"] = ["%d （据点 %d 个）" % (i, len(DATA.get("patterns", {}).get(str(i), []))) for i in ids]
        if ids:
            var_pid.set(cb_pid["values"][0])

    cb_night.bind("<<ComboboxSelected>>", on_combo)
    cb_rare.bind("<<ComboboxSelected>>", on_combo)
    ttk.Button(f4, text="刷新图列表", command=fill_combos).pack(side="left", padx=(8, 0))

    def status_once():
        try:
            pid, seed, pat, ptr, why = locker.status()
            if seed is None:
                info = "读不到：" + (why or "未知原因")
            else:
                info = "游戏 pid=%s ｜ 当前内存里：种子=0x%08X  pattern=%s" % (pid, seed, pat)
            var_info.set(info); log(info)
        except Exception as e:
            var_info.set("读取失败：%r" % e); log("读取失败：%r\n%s" % (e, traceback.format_exc()))

    def do_lock():
        try:
            if not DATA:
                messagebox.showwarning("提示", "先做第 ① 步读 bin"); return
            pid_txt = var_pid.get()
            if not pid_txt:
                messagebox.showwarning("提示", "先选一张图"); return
            pattern_id = int(pid_txt.split()[0])
            rare = int(var_rare.get().split()[0])
            s, info = locker.seed_of(pattern_id, rare)
            if s is None:
                messagebox.showerror("算不出来", "这张图（大空洞 / DLC 号段）暂时算不出锁定种子。"); return
            n_good, off, conj = info
            chk, how = locker.seed_check(s, rare)
            log("图 %d → 锁定种子 0x%08X（偏移 %d，地形码 %d，候选 %d 个；复核 get_pattern=%s「%s」）"
                % (pattern_id, s, off, conj, n_good, chk, how))
            if chk != off:
                messagebox.showerror("复核不通过", "算出的种子复核对不上（得到 %s，期望偏移 %s），已中止。" % (chk, off))
                return
            ok2, msg = locker.write_once(s)
            if not ok2:
                messagebox.showerror("写入失败", msg); log("写入失败：" + msg); return
            locker.start(s)
            messagebox.showinfo("已锁定", "已写进游戏内存：种子 0x%08X（图 %d，%s×%s）\n\n"
                                "现在点出击 → 夜王要选「%s」→ 就是这张图。\n"
                                "（本工具会持续写入，防止被游戏覆盖；点「解除锁定」停止）"
                                % (s, pattern_id, var_night.get(), var_rare.get(),
                                   var_night.get().split(" ", 1)[-1]))
            log("锁定中：0x%08X（持续写入）" % s)
        except Exception as e:
            log("锁定异常：%r\n%s" % (e, traceback.format_exc()))
            messagebox.showerror("锁定异常", repr(e))

    def do_unlock():
        locker.stop()
        log("已停止持续写入（解除锁定）")
        messagebox.showinfo("已解除", "已停止写入。想完全回到原样，点「还原我进游戏时的种子」。")

    # ---- 主方案（推荐）：DLL 方式 = 和以前那套锁定工具同一机制（游戏启动瞬间写种子）
    def do_lock_dll():
        try:
            if not DATA:
                messagebox.showwarning("提示", "先做第 ① 步读 bin"); return
            pid_txt = var_pid.get()
            if not pid_txt:
                messagebox.showwarning("提示", "先选一张图"); return
            pattern_id = int(pid_txt.split()[0])
            rare = int(var_rare.get().split()[0])
            s, info = locker.seed_of(pattern_id, rare)
            if s is None:
                messagebox.showerror("算不出来", "这张图（大空洞 / DLC 号段）暂时算不出锁定种子。"); return
            n_good, off, conj = info
            chk, how = locker.seed_check(s, rare)
            if chk != off:
                messagebox.showerror("复核不通过", "种子复核对不上（得到 %s，期望偏移 %s），已中止。" % (chk, off)); return
            lock_ini_write(s, True)
            log("【DLL 方式】已写锁定配置：图 %d（偏移 %d，地形码 %d）→ 种子 0x%08X；复核=%s「%s」"
                % (pattern_id, off, conj, s, chk, how))
            ans = messagebox.askyesno("已锁定（DLL 方式）",
                                      "已锁定：图 %d（%s × %s）\n种子 0x%08X\n\n现在就用【锁定版】启动游戏吗？\n"
                                      "（启动起来就是【这个 mod 的完整内容】+ 你改好的 bin，进游戏点出击 = 你选的这张图）"
                                      % (pattern_id, var_night.get(), var_rare.get(), s))
            if ans:
                ok, msg = lock_launch(var_bin.get().strip())   # ★ 同步 bin → 生成锁定器 → 用你那个 me3 启动
                log("启动锁定版游戏：\n" + (msg if msg else ""))
                if not ok:
                    messagebox.showerror("启动失败", msg)
                else:
                    messagebox.showinfo("已启动", "游戏正在启动（锁定版）。\n"
                                                  "进去看到的是【这个 mod 的完整内容】+ 你改好的 bin，\n"
                                                  "点出击 = 就是你选的这张图。\n\n"
                                                  "想核对：点【看锁定日志】，里面会打印 "
                                                  "Game state: seed 0x……, pattern N。")
        except Exception as e:
            log("锁定异常：%r\n%s" % (e, traceback.format_exc()))
            messagebox.showerror("锁定异常", repr(e))

    def do_launch():
        ok, msg = lock_launch(var_bin.get().strip())   # 同步 bin → 生成锁定器 → 用你那个 me3 启动
        log("启动锁定版游戏：\n" + (msg if msg else ""))
        if not ok:
            messagebox.showerror("启动失败", msg)

    def do_unlock_dll():
        lock_ini_write(0, False)
        log("【DLL 方式】已解除锁定（patchSeed=false）—— 下次启动游戏即原版随机")
        messagebox.showinfo("已解除锁定", "已关掉锁定（patchSeed = false）。\n重启游戏后就是原版随机。")

    def do_show_dll_log():
        t = lock_dll_log()
        log(t)
        messagebox.showinfo("锁定 DLL 日志", t[-1800:])

    def do_restore():
        locker.stop()
        ok, msg = locker.restore()
        log("还原原始种子：" + msg)
        messagebox.showinfo("还原", msg)

    fill_combos()
    log("工具箱启动 v%s；运行目录 %s" % (VERSION, RUN))
    if not DATA:
        log("提示：还没有地图数据，先做第 ① 步（选 regulation.bin → 开始读取）")
    root.mainloop()

# ----------------------------------------------------------------- 入口
def main():
    args = sys.argv[1:]
    if args and args[0] == "--run-script":
        materialize()
        return dispatch(args[1])
    # ---- 隐藏自检/命令行模式（给我自己验证冻结后的链路用；普通用户用不到）----
    if args and args[0] == "--selftest":
        materialize()
        out = ["RUN=%s" % RUN, "engine=%s" % os.path.isdir(ENG), "web=%s" % os.path.isdir(WEB),
               "lock=%s" % os.path.isdir(LOCK),
               "engine 文件=%d" % len(os.listdir(ENG)), "web 文件=%d" % len(os.listdir(WEB)),
               "zstd=%s" % _have("zstandard"), "Crypto=%s" % _have("Crypto"),
               "frozen=%s" % getattr(sys, "frozen", False)]
        _write_result("\n".join(out)); return 0
    if args and args[0] == "--cli-read" and len(args) > 1:
        materialize()
        ok, msg = refresh(args[1], WEB, lambda l: log("   " + l))
        extra = "data.js md5=" + _md5(os.path.join(WEB, "data.js"))
        _write_result("read=%s  %s\n%s\nwork=%s" % (ok, msg, extra, WORK))
        return 0 if ok else 1
    if args and args[0] == "--cli-write" and len(args) > 2:
        materialize()
        ok, msg = apply_csv_files(args[1], args[2:])
        _write_result("write=%s\n%s" % (ok, msg))
        return 0 if ok else 1
    if args and args[0] == "--cli-lock":
        # 自检：锁定模块能不能加载（冻结后最容易出问题的就是 ctypes 这类系统模块）+ 算种子对不对
        materialize()
        try:
            lk = Locker()
            pid, seed, pat, ptr, why = lk.status()
            s, info = lk.seed_of(188, 2)
            _write_result("lock 模块 OK\npid=%s seed=%s pattern=%s\n读不到的原因：%s\n算种子(188/火山)=0x%08X info=%s"
                          % (pid, seed, pat, why, s if s else 0, info))
            return 0
        except Exception as e:
            _write_result("lock 模块加载失败：%r\n%s" % (e, traceback.format_exc()))
            return 1
    if args and args[0] == "--cli-lock-dll" and len(args) >= 3:
        # 自检：DLL 方式的文件生成（写 ini + 写 me3 profile）—— 不启动游戏
        materialize()
        try:
            lk = Locker()
            s, info = lk.seed_of(int(args[1]), int(args[2]))
            if s is None:
                _write_result("算不出锁定种子（大空洞/DLC 号段）"); return 1
            ini = lock_ini_write(s, True)
            bp = args[3] if len(args) > 3 else None
            prof = lock_profile_write(bp)
            dll, _i, me3, _p = lock_paths(bp)
            _write_result("seed=0x%08X  info=%s\nini=%s（存在=%s）\nprofile=%s（存在=%s）"
                          "\nme3=%s（存在=%s）\ndll存在=%s\n--- profile 内容 ---\n%s"
                          % (s, info, ini, os.path.exists(ini), prof, os.path.exists(prof),
                             me3, os.path.exists(me3), os.path.exists(dll),
                             open(prof, encoding="utf-8").read()))
            return 0
        except Exception as e:
            _write_result("DLL 方式自检失败：%r\n%s" % (e, traceback.format_exc()))
            return 1
    materialize()
    try:
        main_gui()
    except Exception:
        log("GUI 崩溃：\n" + traceback.format_exc())
        try:
            import tkinter.messagebox as mb
            mb.showerror("出错了", "详见日志：\n" + LOG_PATH)
        except Exception:
            pass
        return 1
    return 0


def _have(mod):
    try:
        __import__(mod)
        return True
    except Exception as e:
        return "否(%r)" % e


def _md5(p):
    if not os.path.exists(p):
        return "(没生成)"
    import hashlib
    return hashlib.md5(open(p, "rb").read()).hexdigest().upper()


def _write_result(txt):
    p = os.path.join(runtime_root(), "cli_result.txt")
    try:
        os.makedirs(runtime_root(), exist_ok=True)
        open(p, "w", encoding="utf-8").write(txt)
    except Exception:
        pass
    try:
        print(txt)
    except Exception:
        pass

if __name__ == "__main__":
    sys.exit(main())
