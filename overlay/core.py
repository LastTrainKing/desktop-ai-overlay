"""核心层：状态持久化 + Windows 全局输入钩子（鼠标双击 / 全局热键）。"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# --------------------------------------------------------------------------
# 状态持久化
# --------------------------------------------------------------------------

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "DesktopAIOverlay"
STATE_FILE = APP_DIR / "state.json"

DEFAULT_PROFILE = {
    "ball": None,
    "chat": None,
    "input": None,
    "session_id": None,
    "messages": [],
    "collapsed": True,
}

TARGETS = [
    {"key": "workbuddy", "name": "WorkBuddy", "desc": "ACP 常驻会话"},
    {"key": "dsh", "name": "DSH", "desc": "headless 事件流"},
    {"key": "doubao", "name": "豆包", "desc": "窗口镜像"},
    {"key": "demo", "name": "演示", "desc": "本地回显"},
]


class Store:
    """窗口几何 / 会话 / 消息的持久化。写入采用临时文件替换，避免半截文件。"""

    def __init__(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.data = {"version": 1, "active": "workbuddy", "profiles": {}}
        self._load()
        for t in TARGETS:
            self.data["profiles"].setdefault(t["key"], json.loads(json.dumps(DEFAULT_PROFILE)))

    def _load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data.update(raw)
        except Exception:
            pass  # 状态文件损坏时静默回退到默认值，不影响启动

    def save(self) -> None:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass

    # -- 便捷访问 ----------------------------------------------------------
    @property
    def active(self) -> str:
        return self.data.get("active", "workbuddy")

    @active.setter
    def active(self, key: str) -> None:
        self.data["active"] = key

    def profile(self, key: str | None = None) -> dict:
        key = key or self.active
        return self.data["profiles"].setdefault(key, json.loads(json.dumps(DEFAULT_PROFILE)))


# --------------------------------------------------------------------------
# Win32 全局钩子
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_LBUTTONDOWN = 0x0201
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SPACE = 0x20
VK_H = 0x48

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
# 关键：不显式声明 restype 时 ctypes 默认按 32 位 int 处理，64 位下句柄会被截断，
# 导致 SetWindowsHookExW 直接失败（返回 0）。
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


def physical_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """取窗口在屏幕上的物理像素矩形（用于和钩子的物理坐标精确比对，规避 DPI 换算误差）。"""
    r = wintypes.RECT()
    if user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


# 扩展窗口样式：比 Qt 的 WindowTransparentForInput 更可靠——可在窗口可见时热切换，
# 不需要 hide/show，也不会因为标志位残留导致键盘事件收不到。
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000

user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]


def set_click_through(hwnd: int, enable: bool) -> None:
    """enable=True：鼠标穿透 + 不抢焦点；False：恢复可交互。"""
    h = ctypes.c_void_p(hwnd)
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    if enable:
        ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_LAYERED
    else:
        ex &= ~WS_EX_TRANSPARENT
        ex &= ~WS_EX_NOACTIVATE
    user32.SetWindowLongW(h, GWL_EXSTYLE, ex)
    # 改扩展样式后必须让系统重算窗口框架，否则 NOACTIVATE 的移除不会立即生效，
    # 窗口看似在前台却拿不到键盘焦点。
    user32.SetWindowPos(h, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


class WinHook(QObject):
    """被动式全局钩子：只观察，不拦截，永远 CallNextHookEx。"""

    double_clicked = Signal(int, int)  # 物理屏幕坐标
    hotkey_toggle = Signal()
    hotkey_collapse = Signal()

    DOUBLE_CLICK_MS = 0.40
    MOVE_TOLERANCE = 6

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._mouse_proc = None  # 必须保持引用，否则回调被 GC
        self._kbd_proc = None
        self._last_down_t = 0.0
        self._last_down_pt = (0, 0)
        self._running = False

    # -- 生命周期 ----------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="win-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

    # -- 钩子线程 ----------------------------------------------------------
    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._mouse_proc = HOOKPROC(self._on_mouse)
        self._kbd_proc = HOOKPROC(self._on_key)

        hmod = kernel32.GetModuleHandleW(None)
        h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, hmod, 0)
        err = 0
        if not h_mouse:
            err = ctypes.get_last_error()
            # 低级钩子允许把回调留在本进程，hMod 传 NULL 也能工作，作为兜底
            h_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, None, 0)
        if not h_kbd:
            h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, None, 0)
        if os.environ.get("OVERLAY_DEBUG"):
            print(f"[hook] mouse={bool(h_mouse)} kbd={bool(h_kbd)} err={err}", flush=True)

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if h_mouse:
            user32.UnhookWindowsHookEx(h_mouse)
        if h_kbd:
            user32.UnhookWindowsHookEx(h_kbd)

    def _on_mouse(self, n_code, w_param, l_param):
        try:
            if n_code >= 0 and w_param == WM_LBUTTONDOWN:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = info.pt.x, info.pt.y
                now = time.monotonic()
                dx = abs(x - self._last_down_pt[0])
                dy = abs(y - self._last_down_pt[1])
                if (
                    self._last_down_t
                    and (now - self._last_down_t) <= self.DOUBLE_CLICK_MS
                    and dx <= self.MOVE_TOLERANCE
                    and dy <= self.MOVE_TOLERANCE
                ):
                    self._last_down_t = 0.0
                    self.double_clicked.emit(x, y)
                else:
                    self._last_down_t = now
                    self._last_down_pt = (x, y)
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_key(self, n_code, w_param, l_param):
        try:
            if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = info.vkCode
                ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
                if ctrl and alt:
                    if vk == VK_SPACE:
                        self.hotkey_toggle.emit()
                    elif vk == VK_H:
                        self.hotkey_collapse.emit()
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)
