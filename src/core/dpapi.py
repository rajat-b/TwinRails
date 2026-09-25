"""
Windows DPAPI (CryptProtectData) helpers for encrypting secrets at rest.

The Claude sessionKey is a full login credential, so it should never sit on
disk as plain text. DPAPI ties the ciphertext to the current Windows user
account: only a process running as that same user can decrypt it again, and
copying config.json to another machine or account yields nothing usable.
"""

import base64
import ctypes
from ctypes import wintypes


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)
]
_crypt32.CryptProtectData.restype = wintypes.BOOL

_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL

_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _make_blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))


def encrypt(plaintext: str) -> str:
    """Returns a base64 DPAPI blob decryptable only by this Windows user account."""
    if not plaintext:
        return ""
    in_blob = _make_blob(plaintext.encode("utf-8"))
    out_blob = _DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def decrypt(blob_b64: str) -> str:
    """Reverses encrypt(). Raises if the blob is corrupt or belongs to a different user/machine."""
    if not blob_b64:
        return ""
    in_blob = _make_blob(base64.b64decode(blob_b64))
    out_blob = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(out_blob.pbData)
