"""端到端验证：向"透明穿透"的输入框发送真实鼠标双击，检验全局钩子能否唤醒它。

这是整个方案里最反常识的一环——窗口此时是 click-through 的，系统不会把点击
投递给它，只能靠 WH_MOUSE_LL 全局钩子感知。本脚本用 SendInput 走真实输入栈。
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def find(title: str):
    hwnd = user32.FindWindowW(None, title)
    return hwnd or None


def rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def click_abs(x: int, y: int):
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    nx = int(x * 65535 / (sw - 1))
    ny = int(y * 65535 / (sh - 1))
    seq = []
    for flags in (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,):
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(nx, ny, 0, flags, 0, None)
        seq.append(i)
    user32.SendInput(len(seq), (INPUT * len(seq))(*seq), ctypes.sizeof(INPUT))
    time.sleep(0.02)
    for _ in range(2):
        for flags in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
            i = INPUT(type=0)
            i.mi = MOUSEINPUT(0, 0, 0, flags, 0, None)
            user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
            time.sleep(0.03)
        time.sleep(0.09)


def main():
    # 必须让本进程 DPI 感知，否则 GetWindowRect 与 SendInput 都走逻辑坐标，
    # 与 Qt（DPI 感知）进程内的物理坐标对不上，点击会打偏。
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        user32.SetProcessDPIAware()

    hwnd = find("AI 输入")
    if not hwnd:
        print("FAIL: 未找到输入框窗口（是否已收起？）")
        return 1
    x, y, w, h = rect(hwnd)
    print(f"input rect = {(x, y, w, h)}")
    cx, cy = x + w // 2, y + h // 2

    old = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(old))
    click_abs(cx, cy)
    print(f"double-clicked at {(cx, cy)}")
    user32.SetCursorPos(old.x, old.y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
