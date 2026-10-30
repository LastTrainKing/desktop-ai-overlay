"""界面层：悬浮球 / 聊天展示窗 / 独立输入框。

三者是彼此独立的顶层窗口，各自持有自己的几何状态，互不依附。
"""

from __future__ import annotations

import html
import os
import re
import time

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .core import set_click_through

BASE_FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM = 1, 2, 4, 8


def render_md(text: str) -> str:
    """极简 Markdown：转义后仅处理 **粗体** 与 `行内代码`，避免富文本注入。"""
    s = html.escape(text)
    s = re.sub(r"`([^`\n]+)`", r'<span style="font-family:Consolas,monospace;'
                             r'background:rgba(255,255,255,0.10);padding:1px 4px;'
                             r'border-radius:4px;">\1</span>', s)
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
    return s.replace("\n", "<br>")


# --------------------------------------------------------------------------
# 无边框卡片基类
# --------------------------------------------------------------------------


class FramelessCard(QWidget):
    RESIZE_MARGIN = 7

    def __init__(self, parent=None, radius: int = 16, bg: QColor | None = None,
                 resizable: bool = False, min_size: tuple[int, int] = (260, 160)):
        super().__init__(parent)
        self.setWindowFlags(BASE_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.radius = radius
        self.bg = QColor(bg) if bg else QColor(theme.BG_DARK)
        self.resizable = resizable
        self.setMinimumSize(*min_size)
        self._drag_from: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._resize_edge = 0
        self._resize_start = None
        self._moving = False
        self.on_geometry_changed = None
        self.setMouseTracking(True)

    # -- 绘制 --------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.fillPath(path, self.bg)
        pen = QPen(QColor(255, 255, 255, 24))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawPath(path)

    # -- 拖动 / 缩放 --------------------------------------------------------
    def _edge_at(self, pos: QPoint) -> int:
        if not self.resizable:
            return 0
        m, e, rect = self.RESIZE_MARGIN, 0, self.rect()
        if pos.x() <= m:
            e |= EDGE_LEFT
        elif pos.x() >= rect.width() - m:
            e |= EDGE_RIGHT
        if pos.y() <= m:
            e |= EDGE_TOP
        elif pos.y() >= rect.height() - m:
            e |= EDGE_BOTTOM
        return e

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        edge = self._edge_at(event.position().toPoint())
        if edge:
            self._resize_edge = edge
            self._resize_start = (event.globalPosition().toPoint(), QRect(self.geometry()))
            return
        self._drag_from = event.globalPosition().toPoint()
        self._drag_origin = self.pos()
        self._moving = False

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._resize_edge and self._resize_start:
            start_pt, start_geo = self._resize_start
            delta = event.globalPosition().toPoint() - start_pt
            g = QRect(start_geo)
            if self._resize_edge & EDGE_LEFT:
                g.setLeft(min(start_geo.left() + delta.x(), start_geo.right() - self.minimumWidth()))
            if self._resize_edge & EDGE_RIGHT:
                g.setRight(max(start_geo.right() + delta.x(), start_geo.left() + self.minimumWidth()))
            if self._resize_edge & EDGE_TOP:
                g.setTop(min(start_geo.top() + delta.y(), start_geo.bottom() - self.minimumHeight()))
            if self._resize_edge & EDGE_BOTTOM:
                g.setBottom(max(start_geo.bottom() + delta.y(), start_geo.top() + self.minimumHeight()))
            self.setGeometry(g)
            return
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_from
            if not self._moving and delta.manhattanLength() > 3:
                self._moving = True
            if self._moving:
                self.move(self._drag_origin + delta)
            return
        cursor = Qt.ArrowCursor
        if self.resizable:
            e = self._edge_at(event.position().toPoint())
            if e in (EDGE_LEFT, EDGE_RIGHT):
                cursor = Qt.SizeHorCursor
            elif e in (EDGE_TOP, EDGE_BOTTOM):
                cursor = Qt.SizeVerCursor
            elif e in (EDGE_LEFT | EDGE_TOP, EDGE_RIGHT | EDGE_BOTTOM):
                cursor = Qt.SizeFDiagCursor
            elif e in (EDGE_RIGHT | EDGE_TOP, EDGE_LEFT | EDGE_BOTTOM):
                cursor = Qt.SizeBDiagCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        was_resizing = bool(self._resize_edge)
        self._resize_edge = 0
        self._resize_start = None
        self._drag_from = None
        self._drag_origin = None
        if self._moving or was_resizing:
            self._moving = False
            if callable(self.on_geometry_changed):
                self.on_geometry_changed()
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------
# 悬浮球
# --------------------------------------------------------------------------


class Ball(QWidget):
    """常驻桌面、可拖动、单击唤醒；不进入任务栏、不抢焦点。"""

    clicked = Signal()
    menu_requested = Signal(QPoint)

    SIZE = 58

    def __init__(self):
        super().__init__()
        self.setWindowFlags(BASE_FLAGS | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self._press_pos: QPoint | None = None
        self._moved = False
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._pulse = (self._pulse + 0.03) % 1.0
        if self._hover:
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1, 1, self.SIZE - 2, self.SIZE - 2)

        glow = QRadialGradient(rect.center(), self.SIZE / 2)
        glow.setColorAt(0.55, QColor(76, 141, 255, 60 if self._hover else 34))
        glow.setColorAt(1.0, QColor(76, 141, 255, 0))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(rect)

        inner = rect.adjusted(6, 6, -6, -6)
        grad = QLinearGradient(inner.topLeft(), inner.bottomRight())
        if self._hover:
            grad.setColorAt(0, QColor(96, 158, 255))
            grad.setColorAt(1, QColor(58, 108, 226))
        else:
            grad.setColorAt(0, QColor(66, 84, 122, 235))
            grad.setColorAt(1, QColor(38, 46, 68, 245))
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawEllipse(inner)

        p.setPen(QColor(255, 255, 255, 235))
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(15)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(inner, Qt.AlignCenter, "AI")

        # 呼吸点：表示后台会话在线
        dot = 3.0
        p.setBrush(QColor(120, 220, 170, int(150 + 100 * self._pulse)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(inner.right() - 11, inner.top() + 7, dot, dot))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._moved = False
        elif event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if delta.manhattanLength() > 3:
            self._moved = True
        if self._moved:
            self.move(self.pos() + delta)
            self._press_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            if not self._moved:
                self.clicked.emit()
            self._press_pos = None


# --------------------------------------------------------------------------
# 消息气泡
# --------------------------------------------------------------------------


class Bubble(QWidget):
    def __init__(self, role: str, text: str = "", max_width: int = 420):
        super().__init__()
        self.role = role
        self._raw = text
        self.label = QLabel(render_md(text))
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.RichText)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(max_width)
        self.label.setStyleSheet(
            "QLabel {"
            f"background: {theme.BUBBLE_USER_BG if role == 'user' else theme.BUBBLE_AI_BG};"
            f"color: {'#FFFFFF' if role == 'user' else theme.TEXT};"
            "padding: 9px 13px; border-radius: 12px; line-height: 150%;"
            "}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if role == "user":
            lay.addStretch(1)
            lay.addWidget(self.label)
        else:
            lay.addWidget(self.label)
            lay.addStretch(1)

    def set_text(self, text: str):
        self._raw = text
        self.label.setText(render_md(text))

    def text(self) -> str:
        return self._raw

    def set_max_width(self, w: int):
        self.label.setMaximumWidth(max(180, w))


# --------------------------------------------------------------------------
# 聊天展示窗
# --------------------------------------------------------------------------


class ChatWindow(FramelessCard):
    collapse_requested = Signal()

    def __init__(self):
        super().__init__(radius=18, resizable=True, min_size=(340, 240))
        self.setWindowTitle("AI 对话")
        self.resize(430, 520)
        self._bubbles: list[Bubble] = []
        self._streaming: Bubble | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # 标题栏
        header = QWidget()
        header.setFixedHeight(44)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 10, 0)
        hl.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 10px;")
        self.title = QLabel("WorkBuddy")
        self.title.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
        self.subtitle = QLabel("常驻会话")
        self.subtitle.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:11px;")
        hl.addWidget(self.dot)
        hl.addWidget(self.title)
        hl.addWidget(self.subtitle)
        hl.addStretch(1)

        btn_collapse = QPushButton("—")
        btn_collapse.setFixedSize(24, 24)
        btn_collapse.setCursor(Qt.PointingHandCursor)
        btn_collapse.setStyleSheet(
            "QPushButton{border:none;border-radius:6px;color:#8B93A7;font-size:13px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.09);color:#E9ECF3;}"
        )
        btn_collapse.setToolTip("收起（双击 Enter）")
        btn_collapse.clicked.connect(self.collapse_requested.emit)
        hl.addWidget(btn_collapse)
        root.addWidget(header)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.07);")
        root.addWidget(line)

        # 消息区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(14, 14, 14, 14)
        self.vbox.setSpacing(10)
        self.vbox.addStretch(1)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("background: transparent;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = int(self.width() * 0.76)
        for b in self._bubbles:
            b.set_max_width(w)

    # -- 消息 --------------------------------------------------------------
    def _insert(self, bubble: Bubble):
        self.vbox.insertWidget(self.vbox.count() - 1, bubble)
        self._bubbles.append(bubble)
        bubble.set_max_width(int(self.width() * 0.76))
        QTimer.singleShot(30, self.scroll_to_bottom)

    def add_message(self, role: str, text: str) -> Bubble:
        b = Bubble(role, text, int(self.width() * 0.76))
        self._insert(b)
        return b

    def begin_stream(self) -> Bubble:
        b = Bubble("assistant", "", int(self.width() * 0.76))
        self._insert(b)
        self._streaming = b
        return b

    def update_stream(self, text: str):
        if self._streaming is None:
            self.begin_stream()
        self._streaming.set_text(text)
        self.scroll_to_bottom()

    def end_stream(self):
        self._streaming = None

    def scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        for b in self._bubbles:
            b.setParent(None)
            b.deleteLater()
        self._bubbles.clear()
        self._streaming = None

    def messages(self) -> list[dict]:
        return [{"role": b.role, "text": b.text()} for b in self._bubbles]

    def load_messages(self, msgs: list[dict]):
        self.clear()
        for m in msgs[-200:]:
            self.add_message(m.get("role", "assistant"), m.get("text", ""))

    def set_target(self, name: str, desc: str):
        self.title.setText(name)
        self.subtitle.setText(desc)

    def set_busy(self, busy: bool):
        self.dot.setStyleSheet(
            f"color: {'#F2B24C' if busy else theme.ACCENT}; font-size: 10px;"
        )
        self.subtitle.setText("生成中…" if busy else "常驻会话")


# --------------------------------------------------------------------------
# 独立输入框
# --------------------------------------------------------------------------


class _Editor(QPlainTextEdit):
    submit = Signal()
    empty_double_enter = Signal()

    DOUBLE_ENTER_WINDOW = 0.45

    def __init__(self):
        super().__init__()
        self._last_empty_enter = 0.0
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setPlaceholderText("输入消息，Enter 发送 · Shift+Enter 换行")
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if os.environ.get("OVERLAY_DEBUG"):
                print(f"[editor] Enter empty={not self.toPlainText().strip()}", flush=True)
            if event.modifiers() & Qt.ShiftModifier:
                return super().keyPressEvent(event)
            if not self.toPlainText().strip():
                now = time.monotonic()
                if now - self._last_empty_enter <= self.DOUBLE_ENTER_WINDOW:
                    self._last_empty_enter = 0.0
                    if os.environ.get("OVERLAY_DEBUG"):
                        print("[editor] double-enter -> collapse", flush=True)
                    self.empty_double_enter.emit()
                else:
                    self._last_empty_enter = now
                return
            self.submit.emit()
            return
        super().keyPressEvent(event)


class InputBox(FramelessCard):
    """默认透明静止、点击穿透；双击唤醒后获得焦点。"""

    submitted = Signal(str)
    collapsed = Signal()
    woke = Signal()

    IDLE_OPACITY = 0.17
    WIDTH = 560

    def __init__(self):
        super().__init__(radius=14, resizable=False, min_size=(320, 46))
        self.setWindowTitle("AI 输入")
        self.resize(self.WIDTH, 58)
        self._awake = False
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._maybe_sleep)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 9, 12, 9)
        root.setSpacing(9)

        self.accent = QWidget()
        self.accent.setFixedWidth(3)
        self.accent.setStyleSheet(f"background:{theme.ACCENT};border-radius:1px;")
        root.addWidget(self.accent)

        self.editor = _Editor()
        self.editor.setFixedHeight(30)
        self.editor.setStyleSheet(
            f"QPlainTextEdit{{color:{theme.TEXT};font-size:13px;background:transparent;}}"
            f"QPlainTextEdit::placeholder{{color:{theme.TEXT_FAINT};}}"
        )
        self.editor.textChanged.connect(self._autosize)
        self.editor.submit.connect(self._emit_submit)
        self.editor.empty_double_enter.connect(self.collapsed.emit)
        root.addWidget(self.editor, 1)

        self.send = QPushButton("↵")
        self.send.setFixedSize(30, 30)
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.setStyleSheet(
            f"QPushButton{{border:none;border-radius:8px;background:{theme.ACCENT};"
            "color:#fff;font-size:14px;}"
            "QPushButton:hover{background:#5E9BFF;}"
            "QPushButton:pressed{background:#3D78E0;}"
        )
        self.send.clicked.connect(self._emit_submit)
        root.addWidget(self.send)

        self.hint = QLabel("双击唤醒")
        self.hint.setStyleSheet(f"color:{theme.TEXT_FAINT};font-size:11px;")
        root.addWidget(self.hint)

        self._apply_idle(True)

    # -- 尺寸 --------------------------------------------------------------
    def _autosize(self):
        doc_h = self.editor.document().size().height()
        h = int(min(max(30, doc_h + 6), 108))
        if h != self.editor.height():
            self.editor.setFixedHeight(h)
            self.setFixedHeight(h + 20)

    # -- 空闲 / 唤醒 --------------------------------------------------------
    def _apply_idle(self, idle: bool):
        self._awake = not idle
        self.setWindowOpacity(self.IDLE_OPACITY if idle else 1.0)
        self.send.setVisible(not idle)
        self.hint.setVisible(idle)
        self.accent.setStyleSheet(
            f"background:{theme.TEXT_FAINT if idle else theme.ACCENT};border-radius:1px;"
        )
        if idle:
            self.editor.clearFocus()
        self.show()
        # 用原生扩展样式位热切换穿透/可交互，避免 hide/show 抖动和焦点丢失
        set_click_through(int(self.winId()), idle)

    def sleep(self):
        if not self._awake:
            return
        self.editor.clear()
        self._apply_idle(True)

    def wake(self):
        if self._awake:
            self.editor.setFocus()
            return
        self._apply_idle(False)
        self.raise_()
        self.activateWindow()
        # 激活是异步的：等窗口真正成为前台后再抢焦点，否则 setFocus 会被忽略
        QTimer.singleShot(90, self._focus_editor)
        self.woke.emit()

    def _focus_editor(self):
        self.activateWindow()
        self.editor.setFocus(Qt.OtherFocusReason)
        if os.environ.get("OVERLAY_DEBUG"):
            print(f"[wake] active={self.isActiveWindow()} "
                  f"focus={type(self.focusWidget()).__name__ if self.focusWidget() else None}",
                  flush=True)

    @property
    def awake(self) -> bool:
        return self._awake

    def _maybe_sleep(self):
        if self._awake and not self.editor.toPlainText().strip():
            self.sleep()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._sleep_timer.start(2600)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._sleep_timer.stop()

    # -- 提交 --------------------------------------------------------------
    def _emit_submit(self):
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self.editor.clear()
        self.submitted.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.sleep()
            return
        super().keyPressEvent(event)


# --------------------------------------------------------------------------
# 设置面板
# --------------------------------------------------------------------------


class SettingsDialog(QDialog):
    """三个目标的连接体检 + 凭据录入。不弹网页、不跳转，就地解决。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        from .adapters import probe_target
        from .core import TARGETS

        self.store = store
        self.setWindowTitle("连接设置")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setFixedWidth(480)
        self.setStyleSheet(
            "QDialog{background:#14161C;}"
            f"QLabel{{color:{theme.TEXT};}}"
            "QLineEdit{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);"
            "border-radius:8px;padding:7px 10px;color:#E9ECF3;}"
            "QPushButton{background:rgba(255,255,255,0.08);border:none;border-radius:8px;"
            "padding:7px 16px;color:#E9ECF3;}"
            "QPushButton:hover{background:rgba(255,255,255,0.14);}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel("三个通道的连接状态")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:15px;font-weight:600;")
        root.addWidget(title)

        for t in TARGETS:
            if t["key"] == "demo":
                continue
            info = probe_target(t["key"])
            ready = info["ok"]
            detail_text = info["detail"]
            if t["key"] == "dsh":
                ready = bool(store.data.get("settings", {}).get("deepseek_api_key"))
                detail_text += " · 已就绪" if ready else " · 需填密钥"
            elif t["key"] == "workbuddy":
                ready = False
                detail_text += " · 需登录一次"
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{'#5ED6A0' if ready else '#E0A34C'};font-size:11px;")
            name = QLabel(t["name"])
            name.setFixedWidth(96)
            name.setStyleSheet(f"color:{theme.TEXT};font-size:13px;")
            detail = QLabel(detail_text)
            detail.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:12px;")
            row.addWidget(dot)
            row.addWidget(name)
            row.addWidget(detail, 1)
            root.addLayout(row)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background:rgba(255,255,255,0.08);")
        root.addWidget(line)

        tip = QLabel("DSH 需要一次模型密钥；WorkBuddy 需要在终端里登录一次。填好保存即可，之后一直有效。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:12px;line-height:160%;")
        root.addWidget(tip)

        key_row = QHBoxLayout()
        key_label = QLabel("DSH Key")
        key_label.setFixedWidth(70)
        key_label.setStyleSheet(f"color:{theme.TEXT};font-size:13px;")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…（DeepSeek API Key）")
        self.key_edit.setText(store.data.get("settings", {}).get("deepseek_api_key", ""))
        key_row.addWidget(key_label)
        key_row.addWidget(self.key_edit, 1)
        root.addLayout(key_row)

        wb_tip = QLabel("WorkBuddy 登录：在终端执行  codebuddy  然后输入 /login")
        wb_tip.setStyleSheet(f"color:{theme.TEXT_FAINT};font-size:11px;")
        root.addWidget(wb_tip)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("关闭")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setStyleSheet(
            f"QPushButton{{background:{theme.ACCENT};border:none;border-radius:8px;"
            "padding:7px 18px;color:#fff;font-size:13px;}"
            "QPushButton:hover{background:#5E9BFF;}"
        )
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    def _save(self):
        settings = self.store.data.setdefault("settings", {})
        settings["deepseek_api_key"] = self.key_edit.text().strip()
        self.store.save()
        self.accept()
