"""适配层：把"界面"和"各家 AI 引擎"解耦。

统一契约 Adapter：start / send / cancel / shutdown，向上只抛 chunk/done/failed。
三个目标三种接法：
  workbuddy → ACP（JSON-RPC over stdio，会话归属进程）
  dsh       → headless 的 NDJSON 事件流（--session-id 续接）
  豆包       → UI Automation 读 + 剪贴板/键盘写（不碰豆包本体）
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .credbridge import find_workbuddy_pid, read_env

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

WB_HOME = Path(r"C:\Program Files\WorkBuddyAI")
WB_EXE = WB_HOME / "WorkBuddyAI.exe"
WB_CLI = WB_HOME / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"

DSH_HOME = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness"
DSH_EXE = DSH_HOME / "DeepSeek Harness.exe"
DSH_CLI_JS = DSH_HOME / "resources" / "app.asar" / "dsh" / "node_modules" / \
    "@deepseek-ai" / "dsh-desktop-host" / "lib" / "cli.js"


class Adapter(QObject):
    chunk = Signal(str)
    done = Signal()
    failed = Signal(str)

    name = "adapter"

    def start(self) -> None:
        pass

    def send(self, text: str) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


# --------------------------------------------------------------------------
# 豆包：UIA 镜像 + 模拟输入
# --------------------------------------------------------------------------


class MirrorAdapter(Adapter):
    """接管已经在跑的客户端窗口：不新建会话、不要登录、不改动原程序。"""

    name = "mirror"

    POLL_MS = 700
    QUIET_DONE_S = 2.8
    TIMEOUT_S = 120

    def __init__(self, target: str):
        super().__init__()
        from .windowbridge import WindowBridge
        self.target = target
        self.bridge = WindowBridge(target)
        self._baseline = ""
        self._acc = ""
        self._last_change = 0.0
        self._started = 0.0
        self._waiting = False
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)

    def start(self) -> None:
        # 不做任何事：客户端没开也允许先看历史，发送时再提示
        self._waiting = False

    def send(self, text: str) -> None:
        if not self.bridge.is_running():
            self.failed.emit(f"{self.bridge.cfg['label']} 没有在运行。先打开它，再回来发送。")
            return
        self._baseline = self.bridge.read_text()
        self._acc = ""
        ok, err = self.bridge.send(text)
        if not ok:
            self.failed.emit(err)
            return
        self._waiting = True
        self._started = time.monotonic()
        self._last_change = self._started
        self._poll.start()

    def _tick(self):
        if not self._waiting:
            return
        now = time.monotonic()
        delta = _delta(self._baseline, self.bridge.read_text())
        if len(delta) > len(self._acc):
            self._acc = delta
            self._last_change = now
            self.chunk.emit(delta)
        if self._acc and (now - self._last_change) > self.QUIET_DONE_S:
            self._poll.stop()
            self._waiting = False
            self.done.emit()
        elif now - self._started > self.TIMEOUT_S:
            self._poll.stop()
            self._waiting = False
            if self._acc:
                self.done.emit()
            else:
                self.failed.emit("等待回复超时。可能窗口被遮挡，或对方没有响应。")

    def cancel(self):
        self._poll.stop()
        self._waiting = False


def _delta(before: str, after: str) -> str:
    """取新增部分：先求公共前缀，再返回余下内容。"""
    if not before:
        return after
    n = min(len(before), len(after))
    i = 0
    while i < n and before[i] == after[i]:
        i += 1
    # 前缀匹配过短说明整页重绘，退回取尾部
    if i < len(before) * 0.5:
        return after[-1200:]
    return after[i:]


# --------------------------------------------------------------------------
# DSH：headless 事件流
# --------------------------------------------------------------------------


class DshAdapter(Adapter):
    name = "dsh"

    def __init__(self, cwd: str, session_id: str | None = None):
        super().__init__()
        self.cwd = cwd
        self.session_id = session_id
        self._proc: QProcess | None = None
        self._buf = ""
        self._acc = ""

    def start(self) -> None:
        if not DSH_EXE.exists() or not DSH_CLI_JS.exists():
            self.failed.emit("未找到 DSH 安装目录，请确认 DeepSeek Harness 已安装。")

    def send(self, text: str) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc = None
        proc = QProcess(self)
        proc.setProgram(str(DSH_EXE))
        args = ["--expose-internals", str(DSH_CLI_JS), "--profile", "headless", "--json"]
        if self.session_id:
            args += ["--session-id", self.session_id]
        args.append(text)
        proc.setArguments(args)
        proc.setWorkingDirectory(self.cwd)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("ELECTRON_RUN_AS_NODE", "1")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._on_out)
        proc.readyReadStandardError.connect(self._on_err)
        proc.finished.connect(self._on_finished)
        proc.start()
        self._proc = proc
        self._buf = ""
        self._acc = ""

    def _on_out(self):
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            self._handle(ev)

    def _handle(self, ev: dict):
        t = ev.get("type")
        if t == "session":
            sid = ev.get("sessionId")
            if sid:
                self.session_id = sid
        elif t in ("delta", "assistant", "message"):
            piece = ev.get("text") or (ev.get("message") or {}).get("text") or ""
            if piece:
                self._acc += piece
                self.chunk.emit(self._acc)
        elif t == "final":
            text = ev.get("text") or ""
            if text and not self._acc:
                self._acc = text
                self.chunk.emit(text)
            self.done.emit()
        elif t == "error":
            msg = (ev.get("error") or {}).get("message", "未知错误")
            self.failed.emit(msg)

    def _on_err(self):
        if self._proc is None:
            return
        err = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        if "MISSING_CREDENTIAL" in err:
            self.failed.emit("DSH 还没有配置模型密钥。点右上角「设置」填入 DeepSeek API Key 后即可使用。")
        elif err.strip() and not self._acc:
            self.failed.emit(err.strip().splitlines()[-1][:200])

    def _on_finished(self, code, status):
        if self._acc:
            self.done.emit()

    def cancel(self):
        if self._proc is not None:
            self._proc.kill()

    def shutdown(self):
        self.cancel()


# --------------------------------------------------------------------------
# WorkBuddy：ACP 常驻会话
# --------------------------------------------------------------------------


class AcpAdapter(Adapter):
    name = "workbuddy"

    def __init__(self, cwd: str, session_id: str | None = None):
        super().__init__()
        self.cwd = cwd
        self.session_id = session_id or str(uuid.uuid4())
        self._proc: QProcess | None = None
        self._buf = ""
        self._next_id = 1
        self._ready = False
        self._queue: list[str] = []
        self._acc = ""
        self._callbacks: dict[int, object] = {}

    # -- 环境：复用 GUI 的登录态 -------------------------------------------
    @staticmethod
    def _env() -> dict:
        env = {"ELECTRON_RUN_AS_NODE": "1"}
        pid = find_workbuddy_pid()
        if pid:
            gui = read_env(pid)
            for key in ("CODEBUDDY_CONFIG_DIR", "CODEBUDDY_HOST",
                        "CODEBUDDY_GATEWAY_PASSWORD", "CODEBUDDY_GATEWAY_AUTH"):
                if gui.get(key):
                    env[key] = gui[key]
        return env

    def start(self) -> None:
        # 只做静态检查，真正拉起进程推迟到第一次发送，避免空转
        if not WB_EXE.exists() or not WB_CLI.exists():
            self.failed.emit("未找到 WorkBuddy 安装目录。")

    def _ensure_proc(self) -> None:
        if self._proc is not None:
            return
        proc = QProcess(self)
        proc.setProgram(str(WB_EXE))
        proc.setArguments([str(WB_CLI), "--acp", "--port", "0",
                           "--session-id", self.session_id])
        proc.setWorkingDirectory(self.cwd)
        qenv = QProcessEnvironment.systemEnvironment()
        for k, v in self._env().items():
            qenv.insert(k, v)
        proc.setProcessEnvironment(qenv)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.start()
        self._proc = proc
        self._rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "desktop-ai-overlay", "version": "0.1.0"},
        }, callback=self._on_initialized)

    def _on_initialized(self, msg: dict) -> None:
        if "error" in msg:
            self.failed.emit(msg["error"].get("message", "初始化失败"))
            return
        self._rpc("session/new", {"cwd": self.cwd, "mcpServers": []}, callback=self._on_session)

    def _on_session(self, msg: dict) -> None:
        sid = (msg.get("result") or {}).get("sessionId")
        if sid:
            self.session_id = sid
        self._ready = True
        for t in self._queue:
            self._prompt(t)
        self._queue.clear()

    def send(self, text: str) -> None:
        self._ensure_proc()
        if not self._ready:
            self._queue.append(text)
            return
        self._prompt(text)

    def _prompt(self, text: str):
        self._acc = ""
        self._rpc("session/prompt", {"sessionId": self.session_id,
                                     "prompt": [{"type": "text", "text": text}]},
                  callback=self._on_prompt_done)

    def _on_prompt_done(self, msg: dict):
        if "error" in msg:
            err = msg["error"]
            m = err.get("message", "")
            if "auth" in m.lower():
                self.failed.emit("WorkBuddy 需要一次登录。点右上角「设置」→「登录 WorkBuddy」。")
            else:
                self.failed.emit(m)
            return
        if self._acc:
            self.chunk.emit(self._acc)
        self.done.emit()

    def _on_stderr(self):
        if self._proc is None:
            return
        err = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        if "Authentication required" in err:
            self.failed.emit("WorkBuddy 需要一次登录。点右上角「设置」→「登录 WorkBuddy」。")

    def cancel(self):
        if self._ready:
            self._notify("session/cancel", {"sessionId": self.session_id})

    def shutdown(self):
        if self._proc is not None:
            self._proc.terminate()
            self._proc.waitForFinished(1500)
            self._proc = None

    # -- JSON-RPC ----------------------------------------------------------
    def _rpc(self, method: str, params: dict, callback=None) -> int:
        rid = self._next_id
        self._next_id += 1
        if callback:
            self._callbacks[rid] = callback
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def _notify(self, method: str, params: dict):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict):
        if self._proc is None:
            return
        self._proc.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _on_stdout(self):
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict):
        if "id" in msg and "method" not in msg:
            cb = self._callbacks.pop(msg["id"], None)
            if cb:
                cb(msg)
            return
        method = msg.get("method", "")
        if method == "session/update":
            up = (msg.get("params") or {}).get("update") or {}
            if up.get("sessionUpdate") == "agent_message_chunk":
                c = up.get("content") or {}
                if c.get("type") == "text":
                    self._acc += c.get("text", "")
                    self.chunk.emit(self._acc)
        elif method == "session/request_permission":
            opts = (msg.get("params") or {}).get("options") or []
            allow = next((o for o in opts if str(o.get("kind", "")).startswith("allow")), None)
            self._write({"jsonrpc": "2.0", "id": msg.get("id"),
                         "result": {"optionId": allow.get("optionId") if allow else None}})


# --------------------------------------------------------------------------
# 演示通道：不依赖任何外部程序，用于验证界面与交互链路
# --------------------------------------------------------------------------


class MockAdapter(Adapter):
    name = "demo"

    def __init__(self, target: str = "demo"):
        super().__init__()
        self.target = target
        self._buf = ""
        self._pos = 0
        self._reply = ""
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def send(self, text: str) -> None:
        self._reply = (f"已收到：「{text}」\n\n这是演示通道的流式回复。"
                       "界面、会话与后台进程已经解耦——双击 Enter 收起窗口，"
                       "再点悬浮球回来，对话会原样保留。")
        self._buf, self._pos = "", 0
        self._timer.start()

    def _tick(self):
        if self._pos >= len(self._reply):
            self._timer.stop()
            self.done.emit()
            return
        step = 2 if len(self._reply) - self._pos > 40 else 1
        self._buf += self._reply[self._pos:self._pos + step]
        self._pos += step
        self.chunk.emit(self._buf)

    def cancel(self):
        self._timer.stop()


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------


def build_adapter(target: str, cwd: str, session_id: str | None = None) -> Adapter:
    # 默认全部走"接管已登录窗口"，不需要任何登录或密钥
    if target == "workbuddy" and os.environ.get("OVERLAY_USE_ACP") == "1":
        return AcpAdapter(cwd, session_id)
    if target in ("workbuddy", "doubao", "dsh"):
        return MirrorAdapter(target)
    return MockAdapter(target)


def probe_target(target: str) -> dict:
    """给界面用的连接体检，纯本地检查，不触发任何 AI 调用。"""
    if target in ("workbuddy", "doubao", "dsh"):
        from .windowbridge import WindowBridge
        st = WindowBridge(target).status()
        label = st["label"]
        return {"ok": st["running"],
                "detail": f"{label} 窗口已接管" if st["running"] else f"{label} 未启动"}
    return {"ok": True, "detail": "演示通道"}
