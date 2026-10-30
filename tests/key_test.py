"""端到端验证：向已唤醒的输入框发送两次 Enter，检验"空输入双击 Enter 收起"。"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    # union 必须包含最大的成员 MOUSEINPUT，否则 sizeof(INPUT) 偏小，
    # SendInput 会因 cbSize 不符而静默失败（返回 0）。
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def press_enter():
    down = INPUT(type=1)
    down.ki = KEYBDINPUT(VK_RETURN, 0, 0, 0, None)
    up = INPUT(type=1)
    up.ki = KEYBDINPUT(VK_RETURN, 0, KEYEVENTF_KEYUP, 0, None)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.02)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def main():
    fg = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(ctypes.c_void_p(fg), buf, 256)
    print(f"foreground window = {buf.value!r}")

    press_enter()
    time.sleep(0.12)
    press_enter()
    print("sent 2x Enter (120ms apart)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
