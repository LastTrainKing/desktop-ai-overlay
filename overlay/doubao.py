"""豆包桥接：通过 Windows UI Automation 读取对话、通过模拟输入发送消息。

豆包是原生 Chromium 外壳，没有插件接口，所以只能"从外部看、从外部敲"：
读走 UIA 的 TextPattern，写走剪贴板 + 键盘注入。豆包本体完全不动。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import win32clipboard
import win32con
import win32gui
import win32process

try:
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2)
    HAS_UIA = True
except Exception:  # 没装 uiautomation 时降级为"仅检测窗口"
    HAS_UIA = False

user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56


def _key(vk: int, up: bool = False):
    inp = _INPUT(type=1)
    inp.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _click(x: int, y: int):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for flags in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = _INPUT(type=0)
        inp.mi = _MOUSEINPUT(0, 0, 0, flags, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.03)


def set_clipboard(text: str):
    for _ in range(4):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            time.sleep(0.1)
    return False


class DoubaoBridge:
    WINDOW_NAME = "豆包"
    WINDOW_CLASS = "Chrome_WidgetWin_1"

    def __init__(self):
        self._win = None
        self._hwnd = None

    # -- 发现 --------------------------------------------------------------
    def find(self) -> int | None:
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == self.WINDOW_NAME:
                found.append(h)
            return True

        win32gui.EnumWindows(cb, None)
        self._hwnd = found[0] if found else None
        return self._hwnd

    @property
    def hwnd(self):
        if not self._hwnd or not win32gui.IsWindow(self._hwnd):
            self.find()
        return self._hwnd

    def is_running(self) -> bool:
        return bool(self.hwnd)

    def rect(self):
        h = self.hwnd
        return win32gui.GetWindowRect(h) if h else None

    def _control(self):
        if not HAS_UIA:
            return None
        if self._win is None:
            self._win = auto.WindowControl(searchDepth=1, ClassName=self.WINDOW_CLASS,
                                           Name=self.WINDOW_NAME)
        if not self._win.Exists(1):
            self._win = None
            return None
        return self._win

    # -- 读 ----------------------------------------------------------------
    def read_text(self) -> str:
        win = self._control()
        if win is None:
            return ""
        try:
            doc = win.DocumentControl(searchDepth=14)
            if not doc.Exists(1):
                return ""
            tp = doc.GetTextPattern()
            return tp.DocumentRange.GetText(-1) or ""
        except Exception:
            return ""

    # -- 写 ----------------------------------------------------------------
    def focus(self) -> bool:
        h = self.hwnd
        if not h:
            return False
        try:
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            fg = win32gui.GetForegroundWindow()
            if fg == h:
                return True
            t1 = win32process.GetWindowThreadProcessId(fg)[0]
            t2 = win32process.GetCurrentThreadId()
            try:
                win32process.AttachThreadInput(t2, t1, True)
            except Exception:
                pass
            try:
                win32gui.BringWindowToTop(h)
                win32gui.SetForegroundWindow(h)
            finally:
                try:
                    win32process.AttachThreadInput(t2, t1, False)
                except Exception:
                    pass
            time.sleep(0.25)
            return win32gui.GetForegroundWindow() == h
        except Exception:
            return False

    def _input_point(self):
        """豆包输入区在右侧主区底部。按窗口比例估算，避开左侧会话栏。"""
        r = self.rect()
        if not r:
            return None
        left, top, right, bottom = r
        w, h = right - left, bottom - top
        return int(left + w * 0.70), int(bottom - h * 0.075)

    def send(self, text: str) -> tuple[bool, str]:
        if not self.hwnd:
            return False, "未找到豆包窗口，请先启动豆包"
        if not set_clipboard(text):
            return False, "写入剪贴板失败"
        if not self.focus():
            return False, "无法将豆包窗口切到前台（可能被全屏程序占用）"
        pt = self._input_point()
        if not pt:
            return False, "无法计算输入框位置"
        _click(*pt)
        time.sleep(0.2)
        _key(VK_CONTROL)
        _key(VK_V)
        _key(VK_V, up=True)
        _key(VK_CONTROL, up=True)
        time.sleep(0.35)
        _key(VK_RETURN)
        _key(VK_RETURN, up=True)
        return True, ""


def probe() -> dict:
    """供界面显示连接状态。"""
    b = DoubaoBridge()
    ok = b.is_running()
    return {
        "running": ok,
        "uia": HAS_UIA,
        "rect": b.rect() if ok else None,
        "text_len": len(b.read_text()) if ok and HAS_UIA else 0,
    }


if __name__ == "__main__":
    print(probe())
