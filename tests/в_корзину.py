"""Тестовые данные — в Корзину, не насовсем. Только перечисленные пути и только из временной папки Windows.
Запуск: python в_корзину.py ПУТЬ [ПУТЬ ...]"""
import ctypes
import ctypes.wintypes as W
import os
import sys
from pathlib import Path


class OP(ctypes.Structure):
    _fields_ = [("hwnd", W.HWND), ("wFunc", W.UINT), ("pFrom", W.LPCWSTR), ("pTo", W.LPCWSTR), ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", W.BOOL), ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", W.LPCWSTR)]


def main(paths):
    temp = Path(os.environ["TEMP"]).resolve()
    rc = 0
    for p in paths:
        p = Path(p).resolve()
        if not p.exists():
            continue
        if not p.is_relative_to(temp) or p == temp:  # только своё временное — не папки пользователя
            print("не в TEMP, не трогаю:", p)
            rc = 1
            continue
        op = OP(None, 3, str(p) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)  # FO_DELETE, FOF_ALLOWUNDO — в Корзину
        r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        print("в Корзину:" if r == 0 and not p.exists() else f"не вышло ({r}):", p)
        rc |= int(p.exists())
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
