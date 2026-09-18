# -*- coding: utf-8 -*-
"""
nightreign.exe 内存探针 / 种子写入器（外部进程读写，不需要注入 dll）

候选落点（来自 thefifthmatt helper dll 的特征码，在 1.3.3.0 上命中）：
    mov rax, [rip+disp]  → 全局指针在 VA 0x143C13258
    mov [rax+0xB54], ebx → 值就写在这
所以：候选种子 = *(uint32*)(*(uint64*)0x143C13258 + 0xB54)

用法:
    python seed_probe.py watch                 # 每 0.5s 打印候选值（值变化时高亮）
    python seed_probe.py watch --addr 0x143C13258 --off 0xB54
    python seed_probe.py set 0x12345678        # 写入种子（锁图用）
    python seed_probe.py set 0x12345678 --repeat 20   # 连续写 20 次（防被覆盖）
"""
import ctypes
import struct
import sys
import time

# ⛔ 不能用 ctypes.wintypes：打包成单 exe 后这个子模块不一定被带进去（实测踩过：
#    ModuleNotFoundError: No module named 'ctypes.wintypes'）⇒ 直接用 ctypes 原生类型
DWORD = ctypes.c_ulong
HMODULE = ctypes.c_void_p

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DEFAULT_GLOBAL = 0x143C13258
DEFAULT_OFF = 0xB54          # 种子（逆向自 NightreignRandomizerHelper 的 Game state 日志）
DEFAULT_OFF_PATTERN = 0xB48  # 当前 pattern 号（同一结构里，种子前面 0xC 字节）


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", DWORD),
                ("cntThreads", DWORD), ("th32ParentProcessID", DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", DWORD),
                ("szExeFile", ctypes.c_char * 260)]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("th32ModuleID", DWORD), ("th32ProcessID", DWORD),
                ("GlblcntUsage", DWORD), ("ProccntUsage", DWORD),
                ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)), ("modBaseSize", DWORD),
                ("hModule", HMODULE), ("szModule", ctypes.c_char * 256),
                ("szExePath", ctypes.c_char * 260)]


def find_pid(name=b"nightreign.exe"):
    """只返回第一个（兼容老用法）；新代码用 find_pids() 把全部候选都试一遍"""
    ps = find_pids(name)
    return ps[0] if ps else None


def find_pids(name=b"nightreign.exe"):
    """列出所有同名进程。⭐ NR 实测同时有 3 个 nightreign 进程（启动器/本体/…），
       其中有的打不开（错误码 5 = 拒绝访问）⇒ 必须逐个试，不能只取第一个。"""
    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return out
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name:
                out.append(e.th32ProcessID)
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def is_alive(pid):
    """进程还活着吗？—— ⭐ 实测：机器上会留一堆"已退出的 nightreign 残留进程"（僵尸），
       它们照样能被 Process32 枚举到、但打不开（错误码 5）⇒ 必须先过滤，
       否则新开的游戏还没测，工具就先报"打不开"了。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong(0)
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def alive_pids(name=b"nightreign.exe"):
    """只返回【正在运行】的同名进程"""
    return [p for p in find_pids(name) if is_alive(p)]


def module_base(pid, name=b"nightreign.exe"):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID_HANDLE_VALUE:
        return None
    try:
        m = MODULEENTRY32()
        m.dwSize = ctypes.sizeof(m)
        ok = k32.Module32First(snap, ctypes.byref(m))
        while ok:
            if m.szModule.lower() == name:
                return ctypes.cast(m.modBaseAddr, ctypes.c_void_p).value, m.modBaseSize
            ok = k32.Module32Next(snap, ctypes.byref(m))
    finally:
        k32.CloseHandle(snap)
    return None


def open_proc(pid):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
                        False, pid)
    return h


def rpm(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)):
        return None
    return buf.raw[:read.value]


def wpm(h, addr, data):
    written = ctypes.c_size_t(0)
    return bool(k32.WriteProcessMemory(h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(written)))


def resolve(h, base, gva, off):
    """返回 (种子值, 结构体地址)"""
    ptr_raw = rpm(h, base + (gva - 0x140000000), 8)
    if not ptr_raw:
        return None, None
    ptr = struct.unpack("<Q", ptr_raw)[0]
    if ptr < 0x10000 or ptr > 0x7FFFFFFFFFFF:
        return None, ptr
    v = rpm(h, ptr + off, 4)
    if not v:
        return None, ptr
    return struct.unpack("<I", v)[0], ptr


def main():
    gva = DEFAULT_GLOBAL
    off = DEFAULT_OFF
    args = sys.argv[1:]
    if "--addr" in args:
        gva = int(args[args.index("--addr") + 1], 16)
    if "--off" in args:
        off = int(args[args.index("--off") + 1], 16)
    mode = args[0] if args else "watch"

    pid = find_pid()
    if not pid:
        print("[x] 没找到 nightreign.exe 进程 —— 先启动游戏")
        return 1
    mb = module_base(pid)
    if not mb:
        print("[x] 找不到 nightreign.exe 模块（可能被保护）")
        return 1
    base, size = mb
    print("[+] pid=%d  imagebase=0x%X  size=0x%X" % (pid, base, size))
    print("[+] 候选落点: [0x%X] + 0x%X   (换算后实际地址 = *(u64*)0x%X + 0x%X)"
          % (gva, off, base + (gva - 0x140000000), off))
    h = open_proc(pid)
    if not h:
        print("[x] OpenProcess 失败 err=%d" % ctypes.get_last_error())
        return 1

    if mode == "set":
        seed = int(args[1], 0)
        rep = 20
        if "--repeat" in args:
            rep = int(args[args.index("--repeat") + 1])
        print("[*] 目标种子 = 0x%08X，连续写 %d 次" % (seed, rep))
        ok_any = False
        for i in range(rep):
            ptr_raw = rpm(h, base + (gva - 0x140000000), 8)
            if not ptr_raw:
                time.sleep(0.2)
                continue
            ptr = struct.unpack("<Q", ptr_raw)[0]
            if 0x10000 < ptr < 0x7FFFFFFFFFFF:
                if wpm(h, ptr + off, struct.pack("<I", seed)):
                    ok_any = True
            time.sleep(0.15)
        cur, ptr = resolve(h, base, gva, off)
        print("[%s] 写后现读 = %s (struct=0x%X)" % ("OK" if ok_any else "FAIL",
                                                    ("0x%08X" % cur) if cur is not None else "None", ptr or 0))
        return 0 if ok_any else 1

    # watch：同时读 pattern(+0xB48) 和 种子(+0xB54)
    print("[*] 监视中（Ctrl+C 结束）。变化时用 <<< 标出：")
    last = None
    try:
        while True:
            cur, ptr = resolve(h, base, gva, off)
            pat = None
            if ptr and 0x10000 < ptr < 0x7FFFFFFFFFFF:
                raw = rpm(h, ptr + DEFAULT_OFF_PATTERN, 4)
                if raw:
                    pat = struct.unpack("<I", raw)[0]
            tag = ""
            if cur is not None and last is not None and cur != last:
                tag = "   <<< 种子变了"
            if cur is not None:
                last = cur
            print("%s  pattern=%-6s seed=%s  struct=0x%X%s" % (
                time.strftime("%H:%M:%S"),
                ("%d" % pat) if pat is not None else "?",
                ("0x%08X" % cur) if cur is not None else "读取失败",
                ptr or 0, tag))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[*] 停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
