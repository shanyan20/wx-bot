"""模型密钥：优先读取环境变量，其次解密绑定当前 Windows 用户的 DPAPI 文件。

密文文件不进入版本库；不在配置、进程命令行或异常中回显令牌。
其他系统可用环境变量；Windows 用户/机器改变后应重新配置密钥。
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def unprotect(path: Path) -> str:
    if os.name != "nt":
        raise ValueError("DPAPI 密钥文件仅支持 Windows；其他系统请使用环境变量")
    encrypted = path.read_bytes()
    buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
    source = DataBlob(len(encrypted), buffer)
    target = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
    ):
        raise ValueError("无法解密模型密钥；请使用创建密钥时的 Windows 用户")
    try:
        return ctypes.string_at(target.data, target.size).decode("utf-8")
    finally:
        kernel32.LocalFree(target.data)


def load_api_key(environment_name: str, encrypted_file: Path | None) -> str:
    token = os.environ.get(environment_name, "").strip()
    if not token and encrypted_file is not None:
        try:
            token = unprotect(encrypted_file).strip()
        except OSError:
            raise ValueError("模型密钥文件无法读取；请重新配置凭证") from None
    if not token:
        raise ValueError(f"缺少模型凭证，请设置 {environment_name} 或 api_key_file")
    return token

