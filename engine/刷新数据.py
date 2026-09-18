# -*- coding: utf-8 -*-
"""
刷新网页数据（data.js）—— 把我们改好的 bin 重新变成网页/锁定器看到的样子
================================================================
干什么：
  1) 用 WitchyBND 解包你指定的 regulation.bin（默认 = NRX测试区\mod\regulation.bin）
  2) 跑 数据生成\export_web3.py     → 重建 data.js（表数据 / 坐标 / 底图 / 图标）
  3) 跑 数据生成\enhance_web_data.py → 增强（中文名 / 缩圈池 / 各类候选）
  4) 旧 data.js 自动备份成 data.js.bak_<时间>，并打印"这次变了什么"

用法：
  python 刷新数据.py                          # 用默认 bin
  python 刷新数据.py "D:\\路径\\regulation.bin"   # 指定 bin
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # 数据生成\
EDITOR = os.path.dirname(HERE)                             # 地图种子编辑器\
DAT = os.path.join(EDITOR, "data.js")

# ★ 输出编码修正（2026-09-17 用户实际踩到的崩溃）：
# 被 .bat / GUI 以管道方式调用时，Python 的 stdout/stderr 默认使用系统 locale（中文 Windows = GBK）。
# 只要要打印的内容含非 GBK 字符（例如解码子进程输出时产生的替换字符 U+FFFD），
# print 就会抛 UnicodeEncodeError，把整个刷新流程打断 —— 表现为"第一步永远跑不完"。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_BIN = r"D:\NightreignModResearch\NRX测试区\mod\regulation.bin"
# ⭐⭐ 2026-09-18：解包改用【我们自己的实现】（数据生成\nr_bin_unpack.py）——
#   原理 = AES-256-CBC(固定密钥, IV=前16字节) 解密 → DCX(ZSTD) 解容器 → BND4 落地成 .param
#   ⇒ 不再需要 WitchyBND（也不带任何第三方工具）。实测与 WitchyBND 输出 252/252 逐字节一致。
# 工具箱版：工作目录用环境变量（由工具箱 exe 指到可写目录），没给就用临时目录
WORK = os.environ.get("NR_TOOLBOX_WORK") or os.path.join(os.path.expanduser("~"), "NRSeedToolbox", "work")
# 出错时把完整日志写到这里（固定名，好找）—— 用户 2026-09-17：报错窗口一闪就没线索
ERRLOG = os.environ.get("NR_TOOLBOX_ERRLOG") or os.path.join(WORK, "refresh_last_error.txt")


def pycmd(script):
    """跑子脚本：开发态 = 用 python 跑该文件；冻结成 exe 后 = 让 exe 自己按 --run-script 分派"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-script", os.path.basename(script)]
    return [sys.executable, script]


def errlog(msg):
    """把出错信息追加到固定日志文件；写不进去也不影响主流程"""
    try:
        os.makedirs(os.path.dirname(ERRLOG), exist_ok=True)
        with open(ERRLOG, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 74 + "\n" + msg + "\n")
    except Exception:
        pass


def run(cmd, cwd=None, env=None, timeout=3600):
    print("  $", cmd if isinstance(cmd, str) else " ".join(cmd))
    kw = {}
    if os.name == "nt":
        # ⛔ 极关键（2026-09-17 实测矩阵结论）：绝不能让子进程继承控制台！
        # WitchyBND 内嵌的 PromptPlus 界面在"有控制台但输出被重定向"的环境下会直接拒绝工作：
        #   "PromptPlus requires a terminal/console without redirection environment" → rc=1
        # 用户双击 .bat 时 cmd 给了它真控制台 ⇒ 每次都失败；而 agent 直接跑（无控制台可继承）
        # ⇒ 每次都成功 —— 这就是"我这边测通过、她那点就报错"的真正原因。
        # 实测：加 CREATE_NO_WINDOW 后在有控制台环境下也能成功（252 张表）。
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    # ★ 子进程也必须用 UTF-8 输出：否则它们在管道里用 GBK 编码，一旦内容有非 GBK 字符
    #   就会抛 UnicodeEncodeError（这正是 2026-09-17 用户那次"第一步跑不完"的崩溃点）
    env2 = dict(env or os.environ)
    env2["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, cwd=cwd, env=env2, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", **kw)
    tail = (r.stdout or "").strip().splitlines()[-12:]
    for l in tail:
        print("    |", l)
    if r.returncode != 0:
        print("    !! rc =", r.returncode)
        print("    ", (r.stderr or "")[-800:])
        errlog("子命令失败 %s\n  rc = %s\n--- stdout 全文 ---\n%s\n--- stderr 全文 ---\n%s"
               % (cmd, r.returncode, r.stdout, r.stderr))
    return r.returncode


def keys_of(path):
    """读 data.js 的 patternFlags 键 + 每个 pattern 的内容指纹"""
    if not os.path.exists(path):
        return None, None
    t = open(path, encoding="utf-8").read()
    m = re.search(r"window\.NR_DATA\s*=\s*(\{.*\})", t, re.S)
    if not m:
        return None, None
    d = json.loads(m.group(1))
    pats = d.get("patterns", {})
    fp = {k: json.dumps(v, ensure_ascii=False, sort_keys=True) for k, v in pats.items()}
    return set(d.get("patternFlags", {}).keys()), fp


def parse_args(argv):
    """位置参数 = bin 路径；--out-dir <目录> 可把产物写到别处
    （多 bin 验证用：能跑真实的刷新流程，又完全不碰编辑器的 data.js）"""
    out_dir, bins = EDITOR, []
    k = 0
    while k < len(argv):
        a = argv[k]
        if a == "--out-dir" and k + 1 < len(argv):
            out_dir = argv[k + 1]
            k += 2
            continue
        if not a.startswith("--"):
            bins.append(a)
        k += 1
    return out_dir, (bins[0] if bins else None)


def main():
    out_dir, bin_arg = parse_args(sys.argv[1:])
    binp = bin_arg or DEFAULT_BIN
    dat = os.path.join(out_dir, "data.js")
    print("=" * 74)
    print("刷新网页数据")
    print("  bin     =", binp)
    print("  输出到   =", dat)
    if not os.path.exists(binp):
        print("!! 找不到 bin：", binp); return 1

    old_keys, old_fp = keys_of(dat)

    # ① 解包 —— ★按 bin 的 md5 分独立工作目录★
    # 用户 2026-09-17 指出：以前所有 bin 都解到同一个 WORK\regulation-bin、靠"运行前改名成
    # .prev"来隔离 —— 太脆弱：改名一失败、或解包失败后走了"找现成 *-bin 顶上"的兜底，
    # 就会把【上一份 bin 的数据】当成本次结果（静默读错），而且旧目录越堆越多。
    # 现在每份 bin 一个目录 WORK\<md5前8>\，同一份 bin 复用、互相绝不干扰。
    h = hashlib.md5()
    with open(binp, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    bin_md5 = h.hexdigest().upper()

    print("\n① 解包 bin（我们自己的解包器，不用 WitchyBND）")
    work = os.path.join(WORK, bin_md5[:8])
    os.makedirs(work, exist_ok=True)
    tgt_bin = os.path.join(work, "regulation.bin")
    old_dir = os.path.join(work, "regulation-bin")
    if os.path.isdir(old_dir):
        shutil.rmtree(old_dir, ignore_errors=True)      # 同一份 bin 的旧解包结果，直接清掉重解
        if os.path.isdir(old_dir):
            msg = ("清不掉旧的解包目录（多半被 Smithbox / 资源管理器 / 杀软占用）：%s\n"
                   "  关掉占用它的程序再重试。" % old_dir)
            print("!! " + msg)
            errlog(msg)
            return 1
    shutil.copy2(binp, tgt_bin)
    rc = 0
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from nr_bin_unpack import unpack_dir
        n_written = unpack_dir(binp, old_dir)
        print("  自己解包：落地 %d 个条目" % n_written)
    except Exception as e:
        import traceback
        msg = ("自己的解包器失败：%r\n%s\n  bin = %s" % (e, traceback.format_exc(), binp))
        print("!! " + msg); errlog(msg); return 1
    if not os.path.isdir(old_dir):
        msg = ("解包失败：没有生成解包目录（rc=%s）。\n"
               "  工作目录：%s\n"
               "  常见原因：这个文件不是夜临/法环的 regulation.bin、文件损坏。\n"
               "  详细错误已记到 %s" % (rc, work, ERRLOG))
        print("!! " + msg)
        errlog("解包失败 " + msg)
        return 1
    n = len(glob.glob(os.path.join(old_dir, "*.param")))
    print("  解包完成：%d 张表（工作目录 %s）" % (n, work))
    if n < 200:
        msg = "表数偏少（%d 张），解包不完整，先停下。解包目录：%s" % (n, old_dir)
        print("!! " + msg)
        errlog(msg)
        return 1
    # 数据指纹：把解包里最能代表"这局地图长什么样"的那张表的 md5 记下来，
    # 以后想核对"这次产物到底来自哪份数据"有据可查
    fp_file = os.path.join(old_dir, "MapPatternSet.param")
    if os.path.exists(fp_file):
        hh = hashlib.md5(open(fp_file, "rb").read()).hexdigest().upper()
        print("  数据指纹（MapPatternSet.param md5）= %s" % hh[:16])
        errlog("本次解包指纹 bin=%s  md5=%s  MapPatternSet=%s"
               % (binp, bin_md5, hh[:16]))
    # 清理：工作目录只保留最近 6 个（按修改时间），避免越攒越多
    try:
        subs = [d for d in glob.glob(os.path.join(WORK, "*")) if os.path.isdir(d)]
        if len(subs) > 6:
            subs.sort(key=lambda p: os.path.getmtime(p))
            for d in subs[:-6]:
                if os.path.abspath(d) != os.path.abspath(work):
                    shutil.rmtree(d, ignore_errors=True)
                    print("  （清理旧工作目录 %s）" % os.path.basename(d))
    except Exception as e:
        print("  （旧工作目录清理跳过：%r）" % e)

    # ② 重建 + ③ 增强
    env = dict(os.environ)
    env["NR_WEBGEN_BIN"] = old_dir
    env["NR_WEB_OUT"] = out_dir
    env["NR_REF"] = os.path.join(HERE, "ref")
    # --out-dir 模式下把中文表来源 index.html 也带过去（enhance_web_data.py 要读它）
    if os.path.abspath(out_dir) != os.path.abspath(EDITOR):
        os.makedirs(out_dir, exist_ok=True)
        src_html = os.path.join(EDITOR, "index.html")
        dst_html = os.path.join(out_dir, "index.html")
        if os.path.exists(src_html) and not os.path.exists(dst_html):
            shutil.copy2(src_html, dst_html)
    # 把"这份 bin 是谁"写进 data.js（含 md5）——启动器靠它判断"同源秒开"
    env["NR_SRC_LABEL"] = "%s  (%s)" % (os.path.basename(os.path.dirname(os.path.dirname(binp))) or binp,
                                        bin_md5)
    print("  数据来源标签 =", env["NR_SRC_LABEL"])
    # —— 备份现有 data.js（保留最近 3 份）
    if os.path.exists(dat):
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak = dat + ".bak_" + ts
        shutil.copy2(dat, bak)
        print("  （旧 data.js 已备份 → %s）" % os.path.basename(bak))
        baks = sorted(glob.glob(dat + ".bak_*"))
        for b in baks[:-3]:
            try:
                os.remove(b)
            except OSError as e:                    # 被占用不该让整个刷新崩掉
                print("  （旧备份删不掉，跳过：%s —— %s）" % (os.path.basename(b), e))
    print("\n② 重建 data.js（export_web3.py）")
    if run(pycmd(os.path.join(HERE, "export_web3.py")), cwd=HERE, env=env) != 0:
        print("!! 重建失败"); return 1
    print("\n③ 增强 data.js（enhance_web_data.py）")
    if run(pycmd(os.path.join(HERE, "enhance_web_data.py")), cwd=HERE, env=env) != 0:
        print("!! 增强失败"); return 1
    print("\n③b 补字段（补字段.py：nightPool / eventPool / bossSite / patternOwners / 空闲号）")
    if run(pycmd(os.path.join(HERE, "补字段.py")), cwd=HERE, env=env) != 0:
        print("!! 补字段失败"); return 1

    # ④ 校验 + 对比
    print("\n④ 校验")
    new_keys, new_fp = keys_of(dat)
    if new_keys is None:
        print("!! 新的 data.js 读不出来（格式坏了）"); return 1
    print("  新 data.js：%d 张图，%d 字节" % (len(new_keys), os.path.getsize(dat)))
    if old_keys:
        add = sorted(int(x) for x in (new_keys - old_keys))
        rm = sorted(int(x) for x in (old_keys - new_keys))
        chg = sorted(int(k) for k in (new_keys & old_keys) if new_fp.get(k) != old_fp.get(k))
        print("  相比上一次： +%d 张新图 %s" % (len(add), add[:20]))
        if rm:
            print("               -%d 张（不见了）%s" % (len(rm), rm[:20]))
        print("               %d 张内容变了 %s" % (len(chg), chg[:20]))
    print("\n✅ 刷新完成 —— 打开 index.html（Ctrl+F5 强刷）和 地图排布锁定器.bat 就能看到最新数据")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        errlog("未捕获异常：\n" + tb)
        print("\n（详细错误已记到 %s）" % ERRLOG)
        # ⚠ 这里不能 input()：本脚本多半被 GUI(binpicker.py) 用管道调用，
        #   管道里 input() 会再抛 EOFError，把真正的错误盖掉。窗口停留交给 .bat 的 pause。
        sys.exit(2)
