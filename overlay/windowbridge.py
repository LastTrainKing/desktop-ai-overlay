"""窗口镜像桥：接管"已经在跑、已经登录"的 AI 客户端，不新建会话、不要登录。

读：UI Automation 的 TextPattern 取整页文本，比对前后差异得到新回复。
写：切前台 → 点输入区 → 粘贴 → **写后校验** → 回车。

写后校验是关键：粘贴后重新读文本，确认刚才那句话真的落进了输入框，
否则 Ctrl+Z 撤销并报错。这样即使落点偏了，也不会把内容发到错误的地方。
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
except Exception:
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
VK_CONTROL, VK_RETURN, VK_V, VK_Z = 0x11, 0x0D, 0x56, 0x5A


def _key(vk: int, up: bool = False):
    inp = _INPUT(type=1)
    inp.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _combo(mod: int, vk: int):
    _key(mod)
    _key(vk)
    _key(vk, up=True)
    _key(mod, up=True)


def _click(x: int, y: int):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    for flags in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = _INPUT(type=0)
        inp.mi = _MOUSEINPUT(0, 0, 0, flags, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.03)


def set_clipboard(text: str) -> bool:
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


# 每个客户端一个配置：进程名 + 输入区落点比例（相对窗口）
PROFILES = {
    "workbuddy": {"proc": "WorkBuddyAI.exe", "class": "Chrome_WidgetWin_1",
                  "input": (0.49, 0.85), "label": "WorkBuddy"},
    "doubao": {"proc": "Doubao.exe", "class": "Chrome_WidgetWin_1",
               "input": (0.60, 0.91), "label": "豆包"},
    "dsh": {"proc": "DeepSeek Harness.exe", "class": "Chrome_WidgetWin_1",
            "input": (0.55, 0.90), "label": "DSH"},
}


class WindowBridge:
    def __init__(self, key: str):
        self.key = key
        self.cfg = PROFILES[key]
        self._hwnd = None
        self._ctrl = None

    # -- 发现 --------------------------------------------------------------
    def find(self):
        import psutil
        found = []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return True
            if win32gui.GetClassName(h) != self.cfg["class"]:
                return True
            _, pid = win32process.GetWindowThreadProcessId(h)
            try:
                if psutil.Process(pid).name().lower() == self.cfg["proc"].lower():
                    found.append(h)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(cb, None)
        self._hwnd = found[0] if found else None
        self._ctrl = None
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

    def _root(self):
        if not HAS_UIA or not self.hwnd:
            return None
        if self._ctrl is None:
            try:
                self._ctrl = auto.ControlFromHandle(self.hwnd)
            except Exception:
                return None
        return self._ctrl

    # -- 读 ----------------------------------------------------------------
    def read_text(self) -> str:
        root = self._root()
        if root is None:
            return ""
        try:
            doc = root.DocumentControl(searchDepth=14)
            if not doc.Exists(1):
                return ""
            return doc.GetTextPattern().DocumentRange.GetText(-1) or ""
        except Exception:
            self._ctrl = None
            return ""

    # -- 写 ----------------------------------------------------------------
    def focus(self) -> bool:
        h = self.hwnd
        if not h:
            return False
        try:
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            if win32gui.GetForegroundWindow() == h:
                return True
            fg = win32gui.GetForegroundWindow()
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

    def input_point(self):
        r = self.rect()
        if not r:
            return None
        left, top, right, bottom = r
        w, h = right - left, bottom - top
        rx, ry = self.cfg["input"]
        return int(left + w * rx), int(top + h * ry)

    def send(self, text: str) -> tuple[bool, str]:
        if not self.hwnd:
            return False, f"{self.cfg['label']} 未运行，请先打开它"
        if not set_clipboard(text):
            return False, "写入剪贴板失败"
        if not self.focus():
            return False, f"无法把 {self.cfg['label']} 切到前台（可能被全屏程序占用）"

        before = self.read_text()
        pt = self.input_point()
        if not pt:
            return False, "无法计算输入区位置"
        _click(*pt)
        time.sleep(0.25)
        _combo(VK_CONTROL, VK_V)
        time.sleep(0.45)

        # 写后校验：确认文字真的进了输入区
        after = self.read_text()
        marker = text.strip()[:12]
        if marker and marker not in after:
            _combo(VK_CONTROL, VK_Z)
            return False, ("没能把内容写进输入框（落点可能被界面变化影响）。"
                           "已撤销，未发送。可尝试把窗口最大化后重试。")

        _key(VK_RETURN)
        _key(VK_RETURN, up=True)
        time.sleep(0.1)
        return True, ""

    def status(self) -> dict:
        ok = self.is_running()
        return {"running": ok, "uia": HAS_UIA, "rect": self.rect() if ok else None,
                "label": self.cfg["label"]}
