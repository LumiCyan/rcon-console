# -*- coding: utf-8 -*-
"""校验 PE 文件（exe）内是否含图标资源：RT_ICON(3) 与 RT_GROUP_ICON(14)。"""
import ctypes
import sys
from ctypes import wintypes

path = sys.argv[1]
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR,
                                   ctypes.c_void_p, wintypes.LPARAM]
k32.EnumResourceNamesW.restype = wintypes.BOOL
k32.FreeLibrary.argtypes = [wintypes.HMODULE]

LOAD_LIBRARY_AS_DATAFILE = 0x00000002
h = k32.LoadLibraryExW(path, None, LOAD_LIBRARY_AS_DATAFILE)
if not h:
    print("LOAD_FAIL", ctypes.get_last_error())
    sys.exit(1)

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID,
                              wintypes.LPWSTR, ctypes.c_void_p)
counts = {}


def make_cb(rt):
    def cb(hmod, typ, name, lp):
        counts[rt] = counts.get(rt, 0) + 1
        return True
    return EnumProc(cb)


for rt in (3, 14):
    counts[rt] = 0
    ok = k32.EnumResourceNamesW(h, ctypes.cast(rt, wintypes.LPCWSTR), make_cb(rt), 0)
    if not ok:
        err = ctypes.get_last_error()
        if err != 0:
            print("RT %d 枚举失败 err=%d" % (rt, err))

k32.FreeLibrary(h)
print("RT_ICON(3) = %d 个，RT_GROUP_ICON(14) = %d 组" % (counts[3], counts[14]))
print("结果:", "含图标资源 ✅" if counts[3] > 0 and counts[14] > 0 else "无图标资源 ❌")
