"""凭据桥接：从已登录的 WorkBuddy GUI 进程读取其环境变量中的 CLI 令牌。

原理：GUI 启动 codebuddy 子进程时会注入 CODEBUDDY_AUTH_TOKEN 等变量。我们不去破解
任何加密存储，只读取同一用户、同一台机器上自己进程的公开环境块，复用用户已有的登录态。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def _read(h, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        return b""
    return buf.raw[: n.value]


def read_env(pid: int) -> dict[str, str]:
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return {}
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        ret = ntdll.NtQueryInformationProcess(ctypes.c_void_p(h), 0, ctypes.byref(pbi),
                                              ctypes.sizeof(pbi), None)
        if ret != 0 or not pbi.PebBaseAddress:
            return {}
        raw = _read(h, pbi.PebBaseAddress + 0x20, 8)
        if not raw:
            return {}
        params = int.from_bytes(raw, "little")
        # RTL_USER_PROCESS_PARAMETERS 在 x64 上 0x80 处是 Environment 的裸指针，
        # 不是 UNICODE_STRING。按指针读，再分块把整块环境字符串读出来。
        ptr_raw = _read(h, params + 0x80, 8)
        if len(ptr_raw) < 8:
            return {}
        env_ptr = int.from_bytes(ptr_raw, "little")
        if not env_ptr:
            return {}

        block = b""
        for i in range(16):  # 最多 64KB，足够容纳环境块
            chunk = _read(h, env_ptr + i * 4096, 4096)
            if not chunk:
                break
            block += chunk
            if b"\x00\x00\x00\x00" in block[-8:]:
                break
        text = block.decode("utf-16-le", "replace")
        env = {}
        for item in text.split("\0"):
            if "=" in item:
                k, v = item.split("=", 1)
                if k:
                    env[k] = v
        return env
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(h))


def find_workbuddy_pid() -> int | None:
    """找到主进程（有窗口、命令行不带 --type= 的那个）。"""
    import subprocess
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='WorkBuddyAI.exe'\" | "
             "Where-Object { $_.CommandLine -notmatch '--type=' } | "
             "Select-Object -ExpandProperty ProcessId"],
            text=True, timeout=15, creationflags=0x08000000,
        )
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    pid = find_workbuddy_pid()
    print("pid =", pid)
    if pid:
        env = read_env(pid)
        print("env count =", len(env))
        for k in sorted(env):
            if any(t in k.upper() for t in ("TOKEN", "KEY", "AUTH", "SECRET", "PASSWORD")):
                v = env[k]
                print(f"  {k} = {v[:8]}…({len(v)} chars)")
