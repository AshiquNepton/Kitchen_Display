"""Encrypts what the KDS stores on disk (database connection, signing key).

Windows: DPAPI in machine scope. The key is held by Windows, not by this program, so the file cannot be
read in an editor and is useless if copied to another PC. Elsewhere (development) or if DPAPI fails: a
keyed XOR + base64 scrambling, so nothing is ever written in plain text.
"""
import base64
import hashlib
import sys

_ENTROPY = b"NeptonKDS/v1"
_APPKEY = b"nepton-kds-local-obfuscation-key"
_UI_FORBIDDEN, _LOCAL_MACHINE = 0x1, 0x4


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf  # keep buf alive

    src, k1 = blob(data)
    ent, k2 = blob(_ENTROPY)
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if protect:
        ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN | _LOCAL_MACHINE, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _xor(data):
    stream, n, i = b"", 0, 0
    while len(stream) < len(data):
        stream += hashlib.sha256(_APPKEY + n.to_bytes(4, "big")).digest()
        n += 1
    return bytes(a ^ b for a, b in zip(data, stream))


def seal(text):
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            return "dpapi1:" + base64.b64encode(_dpapi(raw, True)).decode()
        except Exception:
            pass
    return "obf1:" + base64.b64encode(_xor(raw)).decode()


def unseal(token):
    """Returns the original text; raises ValueError when the data is unreadable (e.g. copied from another PC)."""
    token = token.strip()
    try:
        kind, _, body = token.partition(":")
        raw = base64.b64decode(body)
        if kind == "dpapi1":
            return _dpapi(raw, False).decode("utf-8")
        if kind == "obf1":
            return _xor(raw).decode("utf-8")
    except Exception as e:
        raise ValueError("cannot decrypt") from e
    raise ValueError("unknown format")
