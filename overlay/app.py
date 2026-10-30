"""控制器：串联界面、全局钩子与适配器，负责状态机与持久化。

状态机只有两个状态：collapsed（只剩悬浮球）/ expanded（聊天窗 + 输入框）。
窗口隐藏不等于会话结束——适配器始终存活。
"""

from __future__ import annotations

import os
import uuid

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from . import theme
from .adapters import Adapter, build_adapter
from .core import TARGETS, Store, WinHook, physical_window_rect
from .ui import Ball, ChatWindow, InputBox


def _tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(76, 141, 255))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor(255, 255, 255))
    f = p.font()
    f.setPixelSize(26)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "AI")
    p.end()
    return QIcon(pm)


class Controller(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.store = Store()
        self.hook = WinHook()

        self.ball = Ball()
        self.chat = ChatWindow()
        self.input = InputBox()

        self.adapter: Adapter | None = None
        self.expanded = False
        self._last_geo: tuple = ()

        self._wire()
        self._restore()
        self._build_adapter()
        self._build_tray()

        self.hook.start()
        QTimer.singleShot(400, self._startup_state)
        self._persist_timer = QTimer(self)
        self._persist_timer.timeout.connect(self._persist_geometry)
        self._persist_timer.start(2000)

    # -- 初始化 ------------------------------------------------------------
    def _wire(self):
        self.ball.clicked.connect(self.toggle)
        self.ball.menu_requested.connect(self._ball_menu)
        self.chat.collapse_requested.connect(self.collapse)
        self.input.submitted.connect(self._on_submit)
        self.input.collapsed.connect(self.collapse)
        self.input.woke.connect(lambda: self.chat.raise_())
        self.hook.double_clicked.connect(self._on_global_double_click)
        self.hook.hotkey_toggle.connect(self.toggle)
        self.hook.hotkey_collapse.connect(self.collapse)

    def _restore(self):
        prof = self.store.profile()
        for win, key, default in (
            (self.ball, "ball", (QPoint(56, 300),)),
            (self.chat, "chat", (QRect(120, 220, 430, 480),)),
            (self.input, "input", (QRect(120, 740, 560, 58),)),
        ):
            geo = prof.get(key)
            if geo:
                rect = QRect(*geo) if len(geo) == 4 else None
                if rect and any(s.availableGeometry().intersects(rect)
                                for s in QGuiApplication.screens()):
                    win.setGeometry(rect)
                    continue
                if len(geo) == 2:
                    pt = QPoint(*geo)
                    if any(s.availableGeometry().contains(pt) for s in QGuiApplication.screens()):
                        win.move(pt)
                        continue
            d = default[0]
            if isinstance(d, QRect):
                win.setGeometry(d)
            else:
                win.move(d)

        self.chat.load_messages(prof.get("messages") or [])
        self._apply_target_ui()

    def _startup_state(self):
        self.ball.show()
        if not self.store.profile().get("collapsed", True):
            self.expand()

    def demo(self):
        """开发期自检：展开界面并走一遍完整的发送/流式回复链路。"""
        self.expand()
        self.input.wake()
        QTimer.singleShot(500, lambda: self._on_submit("帮我把今天的会议纪要整理成三条结论"))

    # -- 自检：展开 → 发送 → 收起 → 恢复 -----------------------------------
    def selftest(self):
        print("[1] expand")
        self.expand()
        self.input.wake()
        print("[2] send")
        self._on_submit("自检消息：验证收起后上下文与窗口状态是否保留")
        QTimer.singleShot(3000, self._st_collapse)

    def _st_collapse(self):
        print(f"[3] messages before collapse = {len(self.chat.messages())}")
        self.collapse()
        print(f"[4] collapsed: chat={self.chat.isVisible()} "
              f"input={self.input.isVisible()} ball={self.ball.isVisible()}")
        QTimer.singleShot(900, self._st_expand)

    def _st_expand(self):
        self.expand()
        print(f"[5] restored: messages={len(self.chat.messages())} "
              f"adapter={'alive' if self.adapter else 'none'}")
        print(f"    chat={self.chat.geometry().getRect()} input={self.input.geometry().getRect()}")
        print("[6] selftest done")

    def _apply_target_ui(self):
        meta = next((t for t in TARGETS if t["key"] == self.store.active), TARGETS[0])
        from .adapters import probe_target
        try:
            info = probe_target(meta["key"])
            sub = info["detail"]
        except Exception:
            sub = meta["desc"]
        self.chat.set_target(meta["name"], sub)

    def _build_adapter(self):
        if self.adapter is not None:
            self.adapter.shutdown()
            self.adapter.deleteLater()
            self.adapter = None
        prof = self.store.profile()
        cwd = str(self.store.data.get("cwd") or os.getcwd())
        self.adapter = build_adapter(self.store.active, cwd, prof.get("session_id"))
        self.adapter.chunk.connect(self.chat.update_stream)
        self.adapter.done.connect(self._on_done)
        self.adapter.failed.connect(self._on_failed)
        try:
            self.adapter.start()
        except Exception as exc:  # 适配器起不来也不能拖垮界面
            self._on_failed(str(exc))

    # -- 展开 / 收起 --------------------------------------------------------
    def expand(self):
        self._apply_target_ui()
        self.expanded = True
        self.chat.show()
        self.chat.raise_()
        self.input.show()          # 以空闲态出现：透明、穿透、不抢焦点
        self.input.sleep()
        self.ball.raise_()         # 悬浮球始终可点，不被展示窗压住
        self.store.profile()["collapsed"] = False
        self.store.save()

    def collapse(self):
        self.expanded = False
        self.chat.hide()
        self.input.hide()
        self.ball.show()
        self.ball.raise_()
        self._save_messages()
        self.store.profile()["collapsed"] = True
        self.store.save()

    def toggle(self):
        if self.expanded:
            self.collapse()
        else:
            self.expand()

    # -- 输入 / 回复 --------------------------------------------------------
    def _on_submit(self, text: str):
        if not self.expanded:
            self.expand()
        self.chat.add_message("user", text)
        self.chat.begin_stream()
        self.chat.set_busy(True)
        try:
            self.adapter.send(text)
        except Exception as exc:
            self._on_failed(str(exc))

    def _on_done(self):
        self.chat.end_stream()
        self.chat.set_busy(False)
        self._save_messages()

    def _on_failed(self, msg: str):
        self.chat.end_stream()
        self.chat.set_busy(False)
        self.chat.add_message("assistant", f"⚠ 通道异常：{msg}\n\n可右键悬浮球切换目标，或在 README 中查看适配说明。")

    def _save_messages(self):
        prof = self.store.profile()
        prof["messages"] = self.chat.messages()[-200:]
        if self.adapter is not None and getattr(self.adapter, "session_id", None):
            prof["session_id"] = self.adapter.session_id
        self.store.save()

    # -- 全局双击：唤醒输入框 ------------------------------------------------
    def _on_global_double_click(self, px: int, py: int):
        if not self.expanded:
            return
        if self.input.awake or not self.input.isVisible():
            return
        rect = physical_window_rect(int(self.input.winId()))
        if os.environ.get("OVERLAY_DEBUG"):
            print(f"[wake] dblclick=({px},{py}) input_rect={rect}", flush=True)
        if not rect:
            return
        x, y, w, h = rect
        if x <= px <= x + w and y <= py <= y + h:
            self.input.wake()

    # -- 几何持久化 ---------------------------------------------------------
    def _persist_geometry(self):
        cur = (
            self.ball.x(), self.ball.y(),
            self.chat.x(), self.chat.y(), self.chat.width(), self.chat.height(),
            self.input.x(), self.input.y(), self.input.width(), self.input.height(),
        )
        if cur == self._last_geo:
            return
        self._last_geo = cur
        prof = self.store.profile()
        prof["ball"] = [self.ball.x(), self.ball.y()]
        prof["chat"] = [self.chat.x(), self.chat.y(), self.chat.width(), self.chat.height()]
        prof["input"] = [self.input.x(), self.input.y(), self.input.width(), self.input.height()]
        self.store.save()

    # -- 菜单 / 托盘 --------------------------------------------------------
    def _menu(self) -> QMenu:
        m = QMenu()
        m.addAction("收起" if self.expanded else "展开", self.toggle)
        m.addSeparator()
        sub = m.addMenu("目标软件")
        for t in TARGETS:
            act = QAction(f"{t['name']}  ·  {t['desc']}", m, checkable=True)
            act.setChecked(t["key"] == self.store.active)
            act.triggered.connect(lambda _=False, k=t["key"]: self.switch_target(k))
            sub.addAction(act)
        m.addSeparator()
        m.addAction("连接设置…", self.open_settings)
        m.addAction("重置窗口位置", self.reset_positions)
        m.addAction("清空当前对话", self.clear_chat)
        m.addSeparator()
        m.addAction("退出", self.quit)
        return m

    def open_settings(self):
        from .ui import SettingsDialog
        dlg = SettingsDialog(self.store, None)
        dlg.adjustSize()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        dlg.move(scr.center() - dlg.rect().center())
        dlg.exec()
        self._build_adapter()

    def _ball_menu(self, pos: QPoint):
        self._menu().exec(pos)

    def switch_target(self, key: str):
        if key == self.store.active:
            return
        self._save_messages()
        self.store.active = key
        self.store.save()
        prof = self.store.profile()
        self.chat.load_messages(prof.get("messages") or [])
        self._apply_target_ui()
        self._build_adapter()

    def reset_positions(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.ball.move(screen.left() + 60, screen.top() + 260)
        self.chat.setGeometry(screen.left() + 60, screen.top() + 260, 430, 520)
        self.input.setGeometry(screen.left() + 60, screen.bottom() - 140, 560, 58)
        self._persist_geometry()

    def clear_chat(self):
        self.chat.clear()
        self._save_messages()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(_tray_icon())
        self.tray.setToolTip("桌面 AI 悬浮助手")
        self.tray.setContextMenu(self._menu())
        self.tray.activated.connect(
            lambda reason: self.toggle() if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def quit(self):
        self._save_messages()
        self._persist_geometry()
        if self.adapter is not None:
            self.adapter.shutdown()
        self.hook.stop()
        self.app.quit()
