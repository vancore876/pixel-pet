"""Groq credentials kept outside portable settings; Windows user DPAPI storage."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import sys


def redact(value):
    return re.sub(r'gsk_[A-Za-z0-9_-]+', '[key hidden]', str(value))


def _dpapi(payload, decrypt=False):
    if sys.platform != "win32":
        raise OSError("Protected key storage is available on Windows.")
    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(payload)
    incoming = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = "Jeffery Groq connection"
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), description, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError("Windows could not access the protected Groq key. Save the key again.")
    try:
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


class CredentialStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "PixelSystemBuddy" / "groq.key"
        self.session_key = ""

    def get(self):
        if self.session_key:
            return self.session_key
        environment = os.environ.get("GROQ_API_KEY", "").strip()
        if environment:
            return environment
        if sys.platform == "win32" and self.path.is_file():
            return _dpapi(self.path.read_bytes(), decrypt=True).decode("utf-8")
        return ""

    def save(self, key, remember=True):
        key = key.strip()
        if not re.fullmatch(r'gsk_[A-Za-z0-9_-]{16,252}', key):
            raise ValueError("Paste a complete Groq API key beginning with gsk_.")
        if remember and sys.platform == "win32":
            encrypted = _dpapi(key.encode("utf-8"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_bytes(encrypted)
            os.replace(temp, self.path)
        else:
            if self.path.exists():
                self.path.unlink()
        self.session_key = key
        return "Key protected for your Windows account." if remember and sys.platform == "win32" else "Key available for this session."

    def forget(self):
        if self.path.exists():
            self.path.unlink()
        self.session_key = ""

