# 桌面 AI 悬浮助手

一个悬浮球，三个 AI 后台：WorkBuddy / DSH / 豆包。界面完全共用，差异只在适配层。

点悬浮球展开，聊天内容和输入框是两个独立窗口；输入框平时透明地贴在桌面角落、点击穿透，双击才醒；空输入连按两次 Enter 收回小球，AI 继续在后台跑。

---

## 跑起来

```bash
cd desktop-ai-overlay
python run.py
```

首次运行会在屏幕左侧出现一个圆形悬浮球。右键 → 目标软件 切换后台。

---

## 三个通道怎么接上的

| 目标 | 接法 | 状态 |
|---|---|---|
| **豆包** | UI Automation 读对话 + 剪贴板/键盘写入 | **开箱可用**（豆包需在运行） |
| **WorkBuddy** | ACP 协议驱动 `codebuddy --acp` 常驻会话 | 需登录一次 |
| **DSH** | `dsh --profile headless --json` 事件流 | 需填一次 API Key |

### 豆包

不碰豆包本体，从外部读、从外部敲：

- 读：UIA `TextPattern` 拿整页文本，比对前后差异得到新回复，实时流进展示窗
- 写：剪贴板 + `Ctrl+V` + `Enter` 送进豆包输入框
- 安全阀：**发送前强制校验前台窗口是豆包**，不是就拒绝并提示，绝不会把点击打到别的程序上

### WorkBuddy

WorkBuddy 的 AI 引擎是独立进程（`codebuddy --serve --session-id ...`），会话不属于窗口。悬浮层用 ACP 协议直接驱动它，所以收起窗口不影响生成。

**需要一次登录**：在终端执行

```
"C:\Program Files\WorkBuddyAI\WorkBuddyAI.exe" "C:\Program Files\WorkBuddyAI\resources\app.asar.unpacked\cli\bin\codebuddy"
```

然后在里面输入 `/login` 完成授权。登录态会写进 `C:\Users\<你>\.workbuddy-ai`，之后悬浮层直接复用。

> 为什么不能自动复用 GUI 的登录态：GUI 是把 CLI 当"访客"托管的（`CODEBUDDY_HOST=workbuddy-desktop` + 网关密码），令牌不下发给 CLI 进程，所以独立启动的 CLI 必须自己登录一次。

### DSH

```bash
dsh --profile headless --json --session-id <id> "问题"
```

标准 NDJSON 事件流，`--session-id` 续接上下文。需要模型密钥：右键 → 连接设置 → 填 `DeepSeek API Key`，会以环境变量注入子进程。

---

## 操作

| 操作 | 效果 |
|---|---|
| 单击悬浮球 | 展开 / 收起 |
| 拖动悬浮球 | 换位置（自动记忆） |
| 右键悬浮球 | 切换目标 / 连接设置 / 重置位置 / 清空对话 / 退出 |
| 双击输入框 | 唤醒并获得焦点 |
| Enter | 发送（Shift+Enter 换行） |
| 空输入连按两次 Enter | 收起 |
| Esc | 输入框回到透明待命 |
| `Ctrl+Alt+Space` | 全局唤起 / 收起 |
| `Ctrl+Alt+H` | 全局收起 |

---

## 结构

```
desktop-ai-overlay/
├─ run.py                  入口（--demo / --selftest / --expand / --settings 为自检用）
├─ overlay/
│  ├─ theme.py             配色与样式
│  ├─ core.py              状态持久化 + 全局钩子 + 窗口穿透控制
│  ├─ ui.py                悬浮球 / 展示窗 / 输入框 / 设置面板
│  ├─ adapters.py          三通道适配器 + 统一契约
│  ├─ doubao.py            豆包 UIA 桥接
│  ├─ credbridge.py        从运行中的 WorkBuddy 读取启动环境
│  └─ app.py               控制器：状态机、持久化、托盘、菜单
├─ docs/可行性调研报告.md
└─ tests/                  端到端验证脚本
```

状态文件：`%APPDATA%\DesktopAIOverlay\state.json`（窗口几何、会话 ID、消息、设置）

---

## 已验证 / 待验证

**已验证（真机）**
- 三窗口分离、独立几何、位置记忆、多屏回退
- 输入框透明穿透 + 全局双击唤醒 + 自动聚焦（真实 `SendInput` 注入）
- 空输入双击 Enter 收起（真实按键注入）
- 收起后会话存活、展开还原消息与窗口
- 豆包对话读取（UIA TextPattern，870 字符实测）
- 豆包发送的前台安全校验（非豆包前台时拒绝，不误点）
- 三通道连接体检

**待验证**
- WorkBuddy ACP 端到端回复（等一次登录）
- DSH headless 端到端回复（等一次密钥）
- 豆包完整发送往返（发送会抢前台，需在非全屏游戏场景下测）
