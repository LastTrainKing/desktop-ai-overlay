"""桌面 AI 悬浮助手 —— 启动入口。

用法：
    python run.py            # 正常启动（演示通道）
    set OVERLAY_USE_ACP=1    # 切换到 WorkBuddy ACP 真实通道
    python run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from overlay import theme
from overlay.app import Controller


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("DesktopAIAssistant")
    app.setQuitOnLastWindowClosed(False)   # 窗口全关也不退出，托盘/悬浮球常驻
    app.setStyleSheet(theme.QSS)

    controller = Controller(app)  # noqa: F841  生命周期由 Qt 父子关系与闭包维持
    if "--demo" in sys.argv:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(900, controller.demo)
    if "--expand" in sys.argv:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(700, controller.expand)
    if "--settings" in sys.argv:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(900, controller.open_settings)
    if "--selftest" in sys.argv:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(900, controller.selftest)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
