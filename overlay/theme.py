"""视觉主题：深色玻璃质感，低干扰。"""

from PySide6.QtGui import QColor

BG_DARK = QColor(20, 22, 28, 242)
PANEL = QColor(27, 30, 39, 246)
PANEL_ALT = QColor(35, 39, 52, 255)
BORDER = QColor(255, 255, 255, 26)
BORDER_STRONG = QColor(255, 255, 255, 46)

ACCENT = "#4C8DFF"
ACCENT_DIM = "#2B4E8F"
TEXT = "#E9ECF3"
TEXT_MUTED = "#8B93A7"
TEXT_FAINT = "#5C6377"

FONT_FAMILY = "Microsoft YaHei UI"

BUBBLE_USER_BG = "rgba(76,141,255,0.92)"
BUBBLE_AI_BG = "rgba(255,255,255,0.055)"

QSS = f"""
QWidget {{
    font-family: "{FONT_FAMILY}";
    color: {TEXT};
    font-size: 13px;
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.16); border-radius: 3px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.30); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QPlainTextEdit {{
    background: transparent;
    border: none;
    color: {TEXT};
    selection-background-color: {ACCENT_DIM};
    padding: 0;
}}
QMenu {{
    background: #1B1E27;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 6px;
    color: {TEXT};
}}
QMenu::item:selected {{ background: rgba(76,141,255,0.22); }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.08); margin: 5px 8px; }}
QToolTip {{
    background: #1B1E27; color: {TEXT}; border: 1px solid rgba(255,255,255,0.12);
    padding: 5px 8px; border-radius: 6px;
}}
"""
