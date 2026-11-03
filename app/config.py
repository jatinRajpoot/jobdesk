"""Local credentials stay server-side, encrypted with Windows DPAPI when saved."""
import ctypes
import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("JOBDESK_DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
SECRET_LOCK = threading.RLock()


def _crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise ValueError("Set credentials through environment variables on this operating system.")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    dst = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "Jobdesk credentials", None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise ValueError("Windows could not access the encrypted credential store.")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def stored_secrets():
    p = DATA / "credentials.dpapi"
    if not p.exists():
        return {}
    try:
        return json.loads(_crypt(p.read_bytes(), decrypt=True))
    except Exception:
        return {}


def save_parallel(key: str):
    save_secret_values({"PARALLEL_API_KEY": key.strip()})


def save_secret_values(updates):
    with SECRET_LOCK:
        values = stored_secrets()
        for key, value in updates.items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        encrypted = _crypt(json.dumps(values).encode())
        tmp = DATA / "credentials.tmp"
        tmp.write_bytes(encrypted)
        tmp.replace(DATA / "credentials.dpapi")


def _environment(name):
    value = os.environ.get(name)
    if value or os.name != "nt":
        return value
    import winreg
    for hive, path in [(winreg.HKEY_CURRENT_USER, "Environment"), (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]:
        try:
            with winreg.OpenKey(hive, path) as reg:
                value = winreg.QueryValueEx(reg, name)[0]
            if value:
                return value
        except OSError:
            pass
    return None


def river_credentials():
    key = _environment("RIVER_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.river.ai/v1")
    model = os.environ.get("LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017")
    origin = "Environment"
    if not key and os.name == "nt":
        p = Path.home() / ".river-claude-code" / "litellm.yaml"
        if p.exists():
            text = p.read_text(encoding="utf-8-sig")
            def field(name):
                m = re.search(r"^\s*" + name + r"\s*:\s*(.+?)\s*$", text, re.M)
                return m.group(1).strip().strip("\"'") if m else ""
            ref = field("api_key")
            if ref.startswith("os.environ/"):
                key = _environment(ref.removeprefix("os.environ/"))
            elif ref.startswith("${") and ref.endswith("}"):
                key = _environment(ref[2:-1])
            else:
                key = ref
            origin = "Claude Code / River configuration"
    return {"key": key, "base": base.rstrip("/"), "model": model, "origin": origin}


def parallel_key():
    return _environment("PARALLEL_API_KEY") or stored_secrets().get("PARALLEL_API_KEY")
