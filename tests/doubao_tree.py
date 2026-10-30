"""豆包窗口控件树测绘：定位对话区、输入框、发送按钮。"""
import sys

import uiautomation as auto

auto.SetGlobalSearchTimeout(3)


def dump(ctrl, depth=0, max_depth=7, limit=140, out=None):
    if out is None:
        out = []
    if len(out) >= limit or depth > max_depth:
        return out
    try:
        name = (ctrl.Name or "").replace("\n", " ")[:40]
        ct = ctrl.ControlTypeName
        aid = ctrl.AutomationId or ""
        cls = ctrl.ClassName or ""
        if name or ct in ("EditControl", "DocumentControl", "ButtonControl"):
            out.append("  " * depth + f"{ct} name={name!r} id={aid!r} cls={cls!r}")
        for child in ctrl.GetChildren():
            dump(child, depth + 1, max_depth, limit, out)
    except Exception:
        pass
    return out


def main():
    win = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1", Name="豆包")
    if not win.Exists(2):
        print("窗口未找到")
        return 1
    print("rect", win.BoundingRectangle)
    for line in dump(win):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
