"""Окно как обои рабочего стола.

Два способа:
- pin() — окно ПОД значками (слой WorkerW): только показ, мышь забирают значки (заход 2);
- pin_interactive() — окно дочернее у слоя значков и ВЫШЕ них, полоса слева под значки не закрыта: клики и клавиатура
  идут прямо в окно, значки в полосе видны и кликаются (заход 3, ).
Окно как обои рабочего стола: под всеми окнами и под значками (слой WorkerW Проводника).

Тот же приём, что у Lively и Wallpaper Engine: сообщение 0x052C просит Проводник создать слой WorkerW
между картинкой рабочего стола и значками; окно встраивается в этот слой как дочернее.
Только стандартная библиотека (ctypes), сторонние программы не нужны.
"""
import ctypes
import ctypes.wintypes as W
import os
import struct
import time
import zlib

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32")
HWND = W.HWND
for name, res, args in (
    ("FindWindowW", HWND, [W.LPCWSTR, W.LPCWSTR]),
    ("FindWindowExW", HWND, [HWND, HWND, W.LPCWSTR, W.LPCWSTR]),
    ("SendMessageTimeoutW", W.LPARAM, [HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]),
    ("SetParent", HWND, [HWND, HWND]),
    ("GetParent", HWND, [HWND]),
    ("GetAncestor", HWND, [HWND, W.UINT]),
    ("GetWindow", HWND, [HWND, W.UINT]),
    ("GetWindowLongPtrW", ctypes.c_ssize_t, [HWND, ctypes.c_int]),
    ("SetWindowLongPtrW", ctypes.c_ssize_t, [HWND, ctypes.c_int, ctypes.c_ssize_t]),
    ("SetWindowPos", W.BOOL, [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]),
    ("GetClassNameW", ctypes.c_int, [HWND, W.LPWSTR, ctypes.c_int]),
    ("IsWindowVisible", W.BOOL, [HWND]),
    ("GetWindowRect", W.BOOL, [HWND, ctypes.POINTER(W.RECT)]),
    ("ScreenToClient", W.BOOL, [HWND, ctypes.POINTER(W.POINT)]),
    ("GetDC", W.HDC, [HWND]),
    ("ReleaseDC", ctypes.c_int, [HWND, W.HDC]),
    ("PrintWindow", W.BOOL, [HWND, W.HDC, W.UINT]),
    ("SystemParametersInfoW", W.BOOL, [W.UINT, W.UINT, ctypes.c_void_p, W.UINT]),
    ("IsWindow", W.BOOL, [HWND]),
    ("WindowFromPoint", HWND, [W.POINT]),
):
    f = getattr(u, name)
    f.restype, f.argtypes = res, args

for name, res, args in (  # прототипы GDI: без них 64-битные дескрипторы обрезаются (OverflowError)
    ("CreateCompatibleDC", W.HDC, [W.HDC]),
    ("CreateCompatibleBitmap", W.HBITMAP, [W.HDC, ctypes.c_int, ctypes.c_int]),
    ("SelectObject", W.HGDIOBJ, [W.HDC, W.HGDIOBJ]),
    ("GetDIBits", ctypes.c_int, [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]),
    ("DeleteObject", W.BOOL, [W.HGDIOBJ]),
    ("DeleteDC", W.BOOL, [W.HDC]),
):
    f = getattr(g, name)
    f.restype, f.argtypes = res, args

GWL_STYLE = -16
WS_CHILD, WS_VISIBLE, WS_POPUP = 0x40000000, 0x10000000, 0x80000000
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x4, 0x10, 0x20, 0x40
GA_PARENT, GA_ROOT = 1, 2
GW_HWNDNEXT, GW_CHILD = 2, 5
EnumProc = ctypes.WINFUNCTYPE(W.BOOL, HWND, W.LPARAM)


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, b, 256)
    return b.value


def find_layer():
    """(Progman, слой WorkerW, схема): «24H2» — слой внутри Progman, «classic» — отдельное окно верхнего уровня."""
    progman = u.FindWindowW("Progman", None)
    if not progman:
        return None, None, None
    res = ctypes.c_size_t()
    u.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    layer = u.FindWindowExW(progman, None, "WorkerW", None)  # Windows 11 24H2 и новее
    if layer:
        return progman, layer, "24H2"
    found = []

    def cb(h, _):
        if u.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            nxt = u.FindWindowExW(None, h, "WorkerW", None)
            if nxt:
                found.append(nxt)
        return True
    u.EnumWindows(EnumProc(cb), 0)
    return progman, (found[0] if found else None), "classic"


def screen_size():
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def pin(hwnd, rect=None):
    """Встроить окно в слой обоев. rect — (x, y, w, h) в пикселях экрана; по умолчанию основной монитор."""
    progman, layer, kind = find_layer()
    if not layer:
        raise RuntimeError("слой обоев WorkerW не найден")
    style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style = (style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU)) | WS_CHILD | WS_VISIBLE
    u.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    u.SetParent(hwnd, layer)
    x, y, w, h = rect or (0, 0, *screen_size())
    pt = W.POINT(x, y)
    u.ScreenToClient(layer, ctypes.byref(pt))
    u.SetWindowPos(hwnd, None, pt.x, pt.y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
    return {"layer": layer, "scheme": kind}


def icons_host():
    """(окно-хозяин значков, SHELLDLL_DefView, схема): 24H2 — Progman; старая схема — WorkerW со значками."""
    progman = u.FindWindowW("Progman", None)
    dv = u.FindWindowExW(progman, None, "SHELLDLL_DefView", None) if progman else None
    if dv:
        return progman, dv, "24H2"
    found = []

    def cb(h, _):
        d = u.FindWindowExW(h, None, "SHELLDLL_DefView", None)
        if d:
            found.append((h, d))
        return True
    u.EnumWindows(EnumProc(cb), 0)
    return (found[0][0], found[0][1], "classic") if found else (None, None, None)


def work_area():
    """Рабочая область основного монитора без панели задач: (left, top, right, bottom) в пикселях экрана."""
    r = W.RECT()
    u.SystemParametersInfoW(0x0030, 0, ctypes.addressof(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def pin_interactive(hwnd, left=0):
    """окно — дочернее у хозяина значков и выше них по порядку окон: мышь и клавиатура приходят прямо в окно.
    left — ширина полосы слева (в пикселях экрана), которую окно не закрывает: там остаются значки рабочего стола."""
    host, dv, kind = icons_host()
    if not host:
        raise RuntimeError("слой значков рабочего стола не найден")
    style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
    style = (style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU)) | WS_CHILD | WS_VISIBLE
    u.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
    u.SetParent(hwnd, host)
    l, t, r, b = work_area()
    x = l + max(0, int(left))
    pt = W.POINT(x, t)
    u.ScreenToClient(host, ctypes.byref(pt))
    u.SetWindowPos(hwnd, None, pt.x, pt.y, r - x, b - t, SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)  # hWndInsertAfter=0 — наверх
    return {"host": host, "scheme": kind, "rect": (x, t, r - x, b - t)}


def raise_top(hwnd):
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x1 | 0x2 | SWP_NOACTIVATE)  # SWP_NOSIZE | SWP_NOMOVE, наверх среди детей слоя


def pinned_ok(hwnd, host):
    """Сторож: окно живо, у прежнего хозяина и выше значков. False — Проводник перезапускался, закрепить заново."""
    if not (host and u.IsWindow(host) and u.IsWindow(hwnd) and u.GetParent(hwnd) == host):
        return False
    kids = children(host)
    return bool(kids) and kids[0] == hwnd


def children(h):
    out, c = [], u.GetWindow(h, GW_CHILD)
    while c:
        out.append(c)
        c = u.GetWindow(c, GW_HWNDNEXT)
    return out  # первый — верхний по Z


def check(hwnd):
    """Проверка: окно не верхнего уровня (значит, под всеми окнами программ) и ниже слоя значков."""
    progman, layer, kind = find_layer()
    parent = u.GetParent(hwnd)
    root = u.GetAncestor(hwnd, GA_ROOT)
    out = {"scheme": kind, "parent_is_layer": bool(parent) and parent == layer,
           "top_level": u.GetAncestor(hwnd, GA_PARENT) == ctypes.windll.user32.GetDesktopWindow(),
           "visible": bool(u.IsWindowVisible(hwnd))}
    if kind == "24H2":
        order = [cls(c) for c in children(progman)]
        out["progman_children"] = order
        out["icons_above"] = "SHELLDLL_DefView" in order and "WorkerW" in order and order.index("SHELLDLL_DefView") < order.index("WorkerW")
        out["root_is_progman"] = root == progman
    else:
        out["icons_above"] = True  # в старой схеме значки живут в другом окне, которое Проводник держит выше слоя
        out["root_is_progman"] = False
    r = W.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    out["rect"] = (r.left, r.top, r.right, r.bottom)
    return out


def refresh_desktop():
    """После снятия обоев Проводник перерисовывает обычную картинку рабочего стола."""
    buf = ctypes.create_unicode_buffer(520)
    u.SystemParametersInfoW(0x0073, 520, buf, 0)  # SPI_GETDESKWALLPAPER
    u.SystemParametersInfoW(0x0014, 0, buf, 0)  # SPI_SETDESKWALLPAPER тем же файлом, без записи в реестр


# ---------- значки рабочего стола: не оставлять их под окном обоев ----------
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
for name, res, args in (
    ("OpenProcess", W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
    ("VirtualAllocEx", ctypes.c_void_p, [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]),
    ("VirtualFreeEx", W.BOOL, [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD]),
    ("ReadProcessMemory", W.BOOL, [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]),
    ("WriteProcessMemory", W.BOOL, [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]),
    ("CloseHandle", W.BOOL, [W.HANDLE]),
):
    f = getattr(k32, name)
    f.restype, f.argtypes = res, args
for name, res, args in (
    ("SendMessageW", ctypes.c_ssize_t, [HWND, W.UINT, W.WPARAM, W.LPARAM]),
    ("GetWindowThreadProcessId", W.DWORD, [HWND, ctypes.POINTER(W.DWORD)]),
    ("ClientToScreen", W.BOOL, [HWND, ctypes.POINTER(W.POINT)]),
    ("GetShellWindow", HWND, []),
):
    f = getattr(u, name)
    f.restype, f.argtypes = res, args
LVM_GETITEMCOUNT, LVM_GETITEMRECT, LVM_SETITEMPOSITION, LVM_GETITEMPOSITION, LVM_GETITEMSPACING, LVM_GETITEMTEXTW = 0x1004, 0x100E, 0x100F, 0x1010, 0x1033, 0x1073
SMTO_BLOCK, SMTO_ABORTIFHUNG = 0x1, 0x2


def _sm(hwnd, msg, wparam=0, lparam=0, ms=2000):
    """сообщение списку значков Проводника с таймаутом — зависший Проводник не держит поток программы бесконечно."""
    res = ctypes.c_size_t()
    if not u.SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_BLOCK | SMTO_ABORTIFHUNG, ms, ctypes.byref(res)):
        raise TimeoutError(f"Проводник не ответил на сообщение 0x{msg:04X} за {ms} мс")
    return res.value
LVS_AUTOARRANGE = 0x0100


class _LVITEMW(ctypes.Structure):
    _fields_ = [("mask", W.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int), ("state", W.UINT), ("stateMask", W.UINT),
                ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int), ("lParam", W.LPARAM),
                ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int), ("cColumns", W.UINT), ("puColumns", ctypes.c_void_p),
                ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int)]


def icons_list():
    """Список значков рабочего стола (SysListView32 у SHELLDLL_DefView) или None."""
    _, dv, _ = icons_host()
    return u.FindWindowExW(dv, None, "SysListView32", None) if dv else None


def desktop_icons(lv=None):
    """Значки: [{"i", "name", "pos": [x, y], "rect": [l, t, r, b]}] в экранных пикселях. Список живёт в Проводнике, поэтому
    позиции и рамки читаются через буфер в его памяти (LVM_GET* пишут результат по указателю)."""
    lv = lv or icons_list()
    if not lv:
        return []
    pid, shell = W.DWORD(), W.DWORD()
    u.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    u.GetWindowThreadProcessId(u.GetShellWindow(), ctypes.byref(shell))
    if not pid.value or pid.value != shell.value:  # L1: список значков только у процесса оболочки (Проводника), не у подставного окна
        return []
    hp = k32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)  # VM_OPERATION | VM_READ | VM_WRITE
    if not hp:
        return []
    mem = k32.VirtualAllocEx(hp, None, 4096, 0x3000, 0x04)
    if not mem:  # буфер в Проводнике не выделен — читать некуда
        k32.CloseHandle(hp)
        return []
    out, rd, hung = [], ctypes.c_size_t(), False
    try:
        org = W.POINT(0, 0)
        u.ClientToScreen(lv, ctypes.byref(org))
        zero = ctypes.create_string_buffer(4)
        sz = lambda o: (ctypes.byref(o), ctypes.sizeof(o), ctypes.byref(rd))
        for i in range(min(_sm(lv, LVM_GETITEMCOUNT), 2000)):  # L1: потолок — подставной список не крутит поток вечно
            pt, rc = W.POINT(), W.RECT(0, 0, 0, 0)  # rc.left = LVIR_BOUNDS: значок вместе с подписью
            _sm(lv, LVM_GETITEMPOSITION, i, mem)
            if not (k32.ReadProcessMemory(hp, mem, *sz(pt)) and k32.WriteProcessMemory(hp, mem, *sz(rc))):
                continue  # L2: без своего RECT в буфере LVM_GETITEMRECT не шлём
            _sm(lv, LVM_GETITEMRECT, i, mem)
            it = _LVITEMW(mask=1, iItem=i, pszText=mem + 1024, cchTextMax=260)
            if not (k32.ReadProcessMemory(hp, mem, *sz(rc)) and k32.WriteProcessMemory(hp, mem, *sz(it))
                    and k32.WriteProcessMemory(hp, mem + 1024, zero, 4, ctypes.byref(rd))):
                continue  # L2: без своего LVITEMW Проводник взял бы адрес текста из мусора; имя прошлого значка обнулено
            _sm(lv, LVM_GETITEMTEXTW, i, mem)
            buf = ctypes.create_unicode_buffer(260)
            if not k32.ReadProcessMemory(hp, mem + 1024, buf, 520, ctypes.byref(rd)):
                continue
            out.append({"i": i, "name": buf.value, "pos": [pt.x + org.x, pt.y + org.y],
                        "rect": [rc.left + org.x, rc.top + org.y, rc.right + org.x, rc.bottom + org.y]})
    except TimeoutError:
        hung = True  # начатую обработку Проводник доведёт и допишет в буфер — 4 КБ оставляем, не освобождаем
        raise
    finally:
        if not hung:
            k32.VirtualFreeEx(hp, mem, 0, 0x8000)
        k32.CloseHandle(hp)
    return out


def _cross(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def plan_icons(icons, window, strip, spacing):
    """Чистая логика (тестируется без Проводника): значки, чья рамка задевает окно обоев, — на свободные места сетки в полосе.
    window — (l, t, r, b) окна обоев; strip — (l, t, r, b) полосы слева; spacing — шаг сетки значков (x, y).
    Итог: (moves [(значок, новая позиция)], left_over [значки, которым места не нашлось])."""
    under = [ic for ic in icons if _cross(ic["rect"], window)]
    taken = [ic["rect"] for ic in icons if ic not in under]
    moves, left_over = [], []
    sx, sy = max(1, spacing[0]), max(1, spacing[1])
    ref = next((ic for ic in icons if ic not in under), under[0] if under else None)  # сетка соседей (выравнивание по сетке сохраняется)
    for ic in under:
        (px, py), (l, t, r, b) = ic["pos"], ic["rect"]
        ox, oy = (ref["pos"][0] - strip[0]) % sx, (ref["pos"][1] - strip[1]) % sy
        spot = None
        for col in range(0, max(1, (strip[2] - strip[0]) // sx + 1)):
            for row in range(0, max(1, (strip[3] - strip[1]) // sy + 1)):
                x, y = strip[0] + ox + col * sx, strip[1] + oy + row * sy
                nr = [l + x - px, t + y - py, r + x - px, b + y - py]
                if nr[0] >= strip[0] and nr[2] <= strip[2] and nr[1] >= strip[1] and nr[3] <= strip[3] and not any(_cross(nr, o) for o in taken):
                    spot = (x, y, nr)
                    break
            if spot:
                break
        if spot:
            moves.append((ic, [spot[0], spot[1]]))
            taken.append(spot[2])
        else:
            left_over.append(ic)
    return moves, left_over


def icons_to_strip(window, strip_px, apply=True):
    """Значки под окном обоев → в полосу слева. Итог: {"moved": [...], "left_over": [...], "auto_arrange": bool}.
    При автоупорядочении Проводник сам ставит значки, переносить нельзя — только сообщить."""
    lv = icons_list()
    if not lv:
        return {"moved": [], "left_over": [], "auto_arrange": False, "error": "список значков не найден"}
    icons = desktop_icons(lv)
    l, t, r, b = work_area()
    sp = _sm(lv, LVM_GETITEMSPACING)
    auto = bool(u.GetWindowLongPtrW(lv, GWL_STYLE) & LVS_AUTOARRANGE)
    moves, left_over = plan_icons(icons, window, (l, t, l + int(strip_px), b), (sp & 0xFFFF, (sp >> 16) & 0xFFFF))
    if auto:
        return {"moved": [], "left_over": [ic["name"] for ic, _ in moves] + [ic["name"] for ic in left_over], "auto_arrange": True}
    org = W.POINT(0, 0)
    u.ClientToScreen(lv, ctypes.byref(org))
    done = [{"name": ic["name"], "from": ic["pos"], "to": [x, y]} for ic, (x, y) in moves]
    if apply and moves:
        # LVM_SETITEMPOSITION Проводник забывает при «Обновить» — ставим штатно, через IFolderView
        placed = position_items([((ic["pos"][0] - org.x, ic["pos"][1] - org.y), (x - org.x, y - org.y), ic["name"]) for ic, (x, y) in moves])
        for d in done:
            d["ok"] = d["name"] in placed
    return {"moved": done, "left_over": [ic["name"] for ic in left_over], "auto_arrange": False}


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _guid(s):
    g_ = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g_))
    return g_


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("v1", ctypes.c_longlong), ("v2", ctypes.c_longlong)]


def _call(obj, idx, *args, types=()):
    """Метод COM по номеру в таблице (vtable); HRESULT < 0 — исключение OSError."""
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    hr = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(vt[idx])(obj, *args)
    if hr < 0:
        raise OSError(f"COM {idx}: 0x{hr & 0xFFFFFFFF:08X}")
    return hr


def _release(obj):
    if obj:
        _call(obj, 2)


def position_items(items):
    """Штатная перестановка значков рабочего стола (IShellWindows → FindWindowSW(SWC_DESKTOP) → IShellBrowser →
    IShellView → IFolderView::SelectAndPositionItems). items — [((x, y) сейчас, (x, y) новая, имя)] в координатах списка значков.
    Значок опознаётся по текущей позиции и имени. Итог — множество имён, которые встали на новое место."""
    P, VP = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    ole = ctypes.oledll.ole32
    co = ctypes.windll.ole32.CoInitializeEx(None, 2)  # STA: S_OK или S_FALSE — парный CoUninitialize обязателен
    sw = disp = sp = sb = sv = fv = sf = None
    placed = set()
    try:
        sw = P()
        ole.CoCreateInstance(ctypes.byref(_guid("{9BA05972-F6A8-11CF-A442-00A0C90A8F39}")), None, 0x17,
                             ctypes.byref(_guid("{85CB6900-4D95-11CF-960C-0080C7F4EE85}")), ctypes.byref(sw))
        loc, root, hw, disp = _VARIANT(3, 0, 0, 0, 0, 0), _VARIANT(), ctypes.c_long(), P()  # VT_I4 CSIDL_DESKTOP
        _call(sw, 15, ctypes.byref(loc), ctypes.byref(root), 8, ctypes.byref(hw), 1, ctypes.byref(disp),
              types=(ctypes.POINTER(_VARIANT), ctypes.POINTER(_VARIANT), ctypes.c_int, ctypes.POINTER(ctypes.c_long), ctypes.c_int, VP))
        if not disp:
            return placed
        sp, sb, sv, fv, sf = P(), P(), P(), P(), P()
        _call(disp, 0, ctypes.byref(_guid("{6D5140C1-7436-11CE-8034-00AA006009FA}")), ctypes.byref(sp), types=(ctypes.POINTER(_GUID), VP))
        _call(sp, 3, ctypes.byref(_guid("{4C96BE40-915C-11CF-99D3-00AA004AE837}")), ctypes.byref(_guid("{000214E2-0000-0000-C000-000000000046}")),
              ctypes.byref(sb), types=(ctypes.POINTER(_GUID), ctypes.POINTER(_GUID), VP))  # SID_STopLevelBrowser → IShellBrowser
        _call(sb, 15, ctypes.byref(sv), types=(VP,))  # QueryActiveShellView
        _call(sv, 0, ctypes.byref(_guid("{CDE725B0-CCC9-4519-917E-325D72FAB4CE}")), ctypes.byref(fv), types=(ctypes.POINTER(_GUID), VP))
        _call(fv, 5, ctypes.byref(_guid("{000214E6-0000-0000-C000-000000000046}")), ctypes.byref(sf), types=(ctypes.POINTER(_GUID), VP))  # GetFolder → IShellFolder
        n = ctypes.c_int()
        _call(fv, 7, 2, ctypes.byref(n), types=(ctypes.c_uint, ctypes.POINTER(ctypes.c_int)))  # ItemCount(SVGIO_ALLVIEW)
        want = {(tuple(a), nm): tuple(b) for a, b, nm in items}
        for i in range(n.value):
            pidl = P()
            _call(fv, 6, i, ctypes.byref(pidl), types=(ctypes.c_int, VP))  # Item
            try:
                pt = W.POINT()
                _call(fv, 11, pidl, ctypes.byref(pt), types=(P, ctypes.POINTER(W.POINT)))  # GetItemPosition
                strret = (ctypes.c_ubyte * 272)()
                _call(sf, 11, pidl, 0, ctypes.byref(strret), types=(P, ctypes.c_uint, P))  # GetDisplayNameOf(SHGDN_NORMAL)
                buf = ctypes.create_unicode_buffer(260)
                ctypes.oledll.shlwapi.StrRetToBufW(ctypes.byref(strret), pidl, buf, 260)
                to = want.get(((pt.x, pt.y), buf.value))
                if to:
                    arr, npt = (P * 1)(pidl), (W.POINT * 1)(W.POINT(*to))
                    _call(fv, 16, 1, arr, npt, 0x80, types=(ctypes.c_uint, P, P, ctypes.c_uint))  # SelectAndPositionItems(SVSI_POSITIONITEM)
                    chk = W.POINT()
                    _call(fv, 11, pidl, ctypes.byref(chk), types=(P, ctypes.POINTER(W.POINT)))
                    if (chk.x, chk.y) == to:
                        placed.add(buf.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(pidl)
    finally:
        for o in (sf, fv, sv, sb, sp, disp, sw):
            _release(o)
        if co >= 0:  # RPC_E_CHANGED_MODE (поток уже в другом режиме) — инициализировали не мы, не снимаем
            ctypes.windll.ole32.CoUninitialize()
    return placed


def snapshot_png(hwnd, path):
    """Снимок окна (для слоя Progman — картинка рабочего стола со значками) в PNG, без сторонних библиотек."""
    r = W.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD), ("biBitCount", W.WORD),
                    ("biCompression", W.DWORD), ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG),
                    ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    raw = bytearray()
    px = buf.raw
    for y in range(h):
        row = px[y * w * 4:(y + 1) * w * 4]
        raw.append(0)
        raw += bytes(b for i in range(0, len(row), 4) for b in (row[i + 2], row[i + 1], row[i]))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return w, h


# ---------- фокус клавиатуры на обоях ----------
# Обои — дочернее окно слоя рабочего стола (Progman, процесс Проводника). Щелчок по ним не делает рабочий стол активным:
# набор с клавиатуры уходил в программу, которая была активна до щелчка (или в значки рабочего стола), а поле на обоях
# при этом выглядело выбранным. Проверено живым набором.
class _GTI(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", HWND), ("hwndFocus", HWND), ("hwndCapture", HWND),
                ("hwndMenuOwner", HWND), ("hwndMoveSize", HWND), ("hwndCaret", HWND), ("rcCaret", W.RECT)]


for name, res, args in (
    ("GetForegroundWindow", HWND, []),
    ("GetGUIThreadInfo", W.BOOL, [W.DWORD, ctypes.POINTER(_GTI)]),
    ("AttachThreadInput", W.BOOL, [W.DWORD, W.DWORD, W.BOOL]),
    ("SetForegroundWindow", W.BOOL, [HWND]),
    ("SetFocus", HWND, [HWND]),
    ("IsChild", W.BOOL, [HWND, HWND]),
    ("IsIconic", W.BOOL, [HWND]),
    ("IsHungAppWindow", W.BOOL, [HWND]),
):
    f = getattr(u, name)
    f.restype, f.argtypes = res, args
k32.GetCurrentThreadId.restype, k32.GetCurrentThreadId.argtypes = W.DWORD, []


def _tid(h):
    return u.GetWindowThreadProcessId(h, None) if h else 0


def _root(h):
    return u.GetAncestor(h, GA_ROOT) if h else None


def focus_inside(hwnd):
    """Фокус клавиатуры Windows — в окне hwnd или в его дочернем (страницу WebView2 рисует дочернее окно другого процесса)."""
    fg = u.GetForegroundWindow()
    gi = _GTI(cbSize=ctypes.sizeof(_GTI))
    if not (fg and hwnd and u.GetGUIThreadInfo(_tid(fg), ctypes.byref(gi))):
        return False
    f = gi.hwndFocus
    return bool(f) and (f == hwnd or bool(u.IsChild(hwnd, f)))


WM_NULL, SMTO_BLOCK, SMTO_ABORTIFHUNG, PROBE_MS = 0, 0x0001, 0x0002, 150
_dwm = ctypes.WinDLL("dwmapi")
_dwm.DwmGetWindowAttribute.restype, _dwm.DwmGetWindowAttribute.argtypes = ctypes.c_long, [HWND, W.DWORD, ctypes.c_void_p, W.DWORD]


def _responsive(h):
    """окно отвечает на WM_NULL за 150 мс. Программу, занятую 1–4 с, IsHungAppWindow не видит (порог ~5 с), а поток,
    присоединённый к её вводу, ждал бы её в SetForegroundWindow/SetFocus."""
    return _probe(h) != "busy"


def _probe(h):
    """«ok» — ответила; «busy» — не ответила за 150 мс; «denied» — окно с повышенными правами (UIPI): не занята, но ввод не присоединить."""
    if not h:
        return "ok"
    res = ctypes.c_size_t()
    ctypes.set_last_error(0)
    if u.SendMessageTimeoutW(h, WM_NULL, 0, 0, SMTO_ABORTIFHUNG | SMTO_BLOCK, PROBE_MS, ctypes.byref(res)):
        return "ok"
    return "denied" if ctypes.get_last_error() == 5 else "busy"  # ERROR_ACCESS_DENIED


def _ready(h):
    """К вводу окна можно присоединиться: не зависло и ответило."""
    return bool(h) and not u.IsHungAppWindow(h) and _probe(h) == "ok"


def _cloaked(h):
    """окно скрыто Windows (другой виртуальный стол) — DWMWA_CLOAKED."""
    v = W.DWORD()
    try:
        return _dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0
    except (OSError, ctypes.ArgumentError):
        return False


def _attach(threads):
    """Присоединить ввод своего потока к потокам threads: так Windows разрешает сменить активное окно и фокус (снимать — _detach)."""
    me = k32.GetCurrentThreadId()
    return me, [t for t in dict.fromkeys(threads) if t and t != me and u.AttachThreadInput(me, t, True)]


def _detach(me, att):
    for t in att:
        u.AttachThreadInput(me, t, False)


def take_focus(hwnd, host, wait=0.6, tries=2):
    """Сделать хозяина обоев (Progman) активным окном и отдать фокус клавиатуры окну обоев hwnd.
    Итог: (фокус у обоев, прежнее активное окно для возврата после Enter/Esc — None, если активен был сам рабочий стол,
    переключали ли фокус сейчас — False, если клавиатура уже была у обоев)."""
    if focus_inside(hwnd):
        return True, None, False
    prev = u.GetForegroundWindow()
    prev = prev if prev and _root(prev) != host else None
    target = u.GetWindow(hwnd, GW_CHILD) or hwnd  # элемент WebView2: получив фокус, передаёт его странице
    fg0 = u.GetForegroundWindow()
    if fg0 and _root(fg0) != host and (u.IsHungAppWindow(fg0) or _probe(fg0) == "busy"):  # программа занята — не ждём, сразу запасной путь
        return False, prev, True
    for _ in range(tries):
        fg = u.GetForegroundWindow()
        me, att = _attach([_tid(fg) if _ready(fg) else 0, _tid(hwnd)])  # к занятой или зависшей программе не присоединяемся
        try:
            if _root(u.GetForegroundWindow()) != host:
                u.SetForegroundWindow(host)
                t0 = time.time()
                while _root(u.GetForegroundWindow()) != host and time.time() - t0 < wait:
                    time.sleep(0.01)
                time.sleep(0.05)  # Проводник, став активным, отдаёт фокус значкам — наш SetFocus должен прийти после
            u.SetFocus(target)
        finally:
            _detach(me, att)
        time.sleep(0.1)
        if focus_inside(hwnd):
            return True, prev, True
    return False, prev, True


def activate(target, only_from=None):
    """Сделать окно target активным. only_from — только если сейчас активно это окно верхнего уровня (пользователь не ушёл сам)."""
    if not (target and u.IsWindow(target) and u.IsWindowVisible(target) and not u.IsIconic(target)) or _cloaked(target):
        return False  # окно другого виртуального стола — не переключаем
    fg = u.GetForegroundWindow()
    if only_from is not None and _root(fg) != only_from:
        return False
    me, att = _attach([_tid(fg) if _ready(fg) else 0, _tid(target) if _ready(target) else 0])  # и к занятой цели не присоединяемся
    try:
        return bool(u.SetForegroundWindow(target))
    finally:
        _detach(me, att)


# ---------- ярлыки: только чтение ----------
def shortcut_target(path):
    """Цель ярлыка .lnk (IShellLinkW::GetPath через IPersistFile::Load)."""
    co = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    sl, pf = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        hr = ctypes.windll.ole32.CoCreateInstance(ctypes.byref(_guid("{00021401-0000-0000-C000-000000000046}")), None, 1,
                                                  ctypes.byref(_guid("{000214F9-0000-0000-C000-000000000046}")), ctypes.byref(sl))
        if hr < 0:
            raise OSError(f"ShellLink: 0x{hr & 0xFFFFFFFF:08X}")
        _call(sl, 0, ctypes.byref(_guid("{0000010b-0000-0000-C000-000000000046}")), ctypes.byref(pf), types=(ctypes.c_void_p, ctypes.c_void_p))
        _call(pf, 5, str(path), 0, types=(ctypes.c_wchar_p, W.DWORD))
        buf = ctypes.create_unicode_buffer(32768)
        _call(sl, 3, buf, len(buf), None, 0, types=(ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, W.DWORD))
        return buf.value
    finally:
        _release(pf)
        _release(sl)
        if co >= 0:
            ctypes.windll.ole32.CoUninitialize()


def taskbar_pinned(exe, folder=None):
    """Закреплена ли программа в панели задач: есть ли в «User Pinned\\TaskBar» ярлык на этот exe (папку только читаем)."""
    if folder is None:
        appdata = os.environ.get("APPDATA", "")
        if not os.path.isabs(appdata):  # пустой APPDATA — путь от текущей папки, там не панель задач
            return False
        folder = os.path.join(appdata, "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar")
    folder = str(folder)
    want = os.path.normcase(os.path.abspath(str(exe)))
    if not os.path.isdir(folder):
        return False
    for name in os.listdir(folder):
        if not name.lower().endswith(".lnk"):
            continue
        try:
            t = shortcut_target(os.path.join(folder, name))
        except OSError:
            continue
        if t and os.path.normcase(os.path.abspath(t)) == want:
            return True
    return False
