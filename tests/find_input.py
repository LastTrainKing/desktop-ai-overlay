"""定位聊天类窗口的输入框：按控件矩形找，不靠比例猜坐标。"""
import sys

import uiautomation as auto

auto.SetGlobalSearchTimeout(3)


def bottom_controls(hwnd, ratio=0.55):
    root = auto.ControlFromHandle(hwnd)
    if not root.Exists(2):
        return []
    r = root.BoundingRectangle
    top_limit = r.top + int((r.bottom - r.top) * ratio)
    hits = []

    def walk(c, d=0):
        if d > 16:
            return
        try:
            b = c.BoundingRectangle
            ct = c.ControlTypeName
            if ct in ("EditControl", "DocumentControl") and b.bottom > top_limit and b.height() > 10:
                hits.append((ct, c.Name or "", b, c))
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    walk(root)
    return hits


def describe(hwnd, label):
    r = auto.ControlFromHandle(hwnd).BoundingRectangle
    print(f"=== {label} rect={r} ===")
    for ct, name, b, _ in bottom_controls(hwnd):
        print(f"  {ct:16s} name={name[:20]!r:24s} rect=({b.left},{b.top},{b.right},{b.bottom})")
        try:
            tp = _.GetTextPattern()
            t = tp.DocumentRange.GetText(60)
            print(f"       text={t!r}")
        except Exception:
            pass


if __name__ == "__main__":
    targets = []
    for arg in sys.argv[1:]:
        h, label = arg.split(":", 1)
        targets.append((int(h), label))
    for h, label in targets:
        describe(h, label)
