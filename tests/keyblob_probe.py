"""从 WorkBuddy 的 keyblob 解出登录凭据（DPAPI，当前用户可解）。"""
import ctypes
import json
from ctypes import wintypes

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def blob(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def unprotect(data: bytes) -> bytes:
    inb = blob(data)
    outb = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(ctypes.byref(inb), None, None, None, None, 0,
                                    ctypes.byref(outb))
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    return ctypes.string_at(outb.pbData, outb.cbData)


if __name__ == "__main__":
    p = r"C:\Users\user\.workbuddy-ai\keyblob"
    raw = open(p, "rb").read()
    print("blob size", len(raw))
    try:
        plain = unprotect(raw)
    except OSError as e:
        print("decrypt failed:", e)
        raise SystemExit(1)
    print("plain size", len(plain))
    try:
        d = json.loads(plain.decode("utf-8"))
        print("keys:", list(d.keys()))
        for k, v in d.items():
            if isinstance(v, str):
                print(f"  {k} = {v[:10]}…({len(v)})" if len(v) > 14 else f"  {k} = {v}")
            else:
                print(f"  {k} : {type(v).__name__}")
    except Exception:
        print("not json, head:", plain[:80])
