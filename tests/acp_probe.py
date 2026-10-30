"""ACP 握手探针：验证 codebuddy --acp 是否能作为常驻会话后端被外部程序驱动。"""
import json
import os
import subprocess
import sys
import threading
import time

EXE = r"C:\Program Files\WorkBuddyAI\WorkBuddyAI.exe"
CLI = r"C:\Program Files\WorkBuddyAI\resources\app.asar.unpacked\cli\bin\codebuddy"


def main():
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    env.pop("ELECTRON_NO_ATTACH_CONSOLE", None)

    p = subprocess.Popen(
        [EXE, CLI, "--acp", "--port", "0", "--permission-mode", "default"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, bufsize=1, text=True, encoding="utf-8", errors="replace",
    )

    out_lines = []
    err_chunks = []

    def reader():
        for line in p.stdout:
            line = line.strip()
            if line:
                out_lines.append(line)

    def ereader():
        for line in p.stderr:
            err_chunks.append(line)

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=ereader, daemon=True).start()

    def send(obj):
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        "clientInfo": {"name": "overlay-probe", "version": "0.1.0"},
    }})

    deadline = time.time() + 25
    init_resp = None
    while time.time() < deadline and init_resp is None:
        for line in list(out_lines):
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == 1:
                init_resp = msg
        time.sleep(0.2)

    print("=== INITIALIZE ===")
    print(json.dumps(init_resp, ensure_ascii=False, indent=2)[:2500] if init_resp else "NO RESPONSE")

    if init_resp and "result" in init_resp:
        send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
            "cwd": os.getcwd(), "mcpServers": []}})
        t2 = time.time() + 25
        sess = None
        while time.time() < t2 and sess is None:
            for line in list(out_lines):
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("id") == 2:
                    sess = msg
            time.sleep(0.2)
        print("\n=== SESSION/NEW ===")
        print(json.dumps(sess, ensure_ascii=False, indent=2)[:1500] if sess else "NO RESPONSE")

    print("\n=== STDERR (tail) ===")
    print("".join(err_chunks[-15:])[:1200])

    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        p.kill()


if __name__ == "__main__":
    sys.exit(main())
