"""Установщик «Мой день» (без прав администратора). Собирается в «Установить Мой день.exe» скриптом build_installer.py.

Что делает установка:
- копирует программу в %LOCALAPPDATA%\\Programs\\MoyDen (или --target);
- пишет «настройки-установки.json» с папкой данных (по умолчанию %LOCALAPPDATA%\\MoyDen): данные, настройки и резервы — там;
  при первом запуске программа сама создаёт пустой список задач;
- регистрирует программу в «Параметры → Приложения → Установленные приложения» (HKCU, значок, версия, издатель, удаление);
- создаёт ярлыки: меню «Пуск» и автозагрузка (--autostart); ярлык прежней установки «Мой день» заменяется. Ярлыка на рабочем столе нет;
  прежний ярлык «Мой день» этой же программы с рабочего стола уходит в Корзину, копия — в резерв;
- рядом с другой сборкой «Мой день» (ключ MoyDen в «Установленных приложениях») не ставится: у неё свои ярлыки, окно и данные;
- удаление (--uninstall) убирает программу, ярлыки и запись в реестре; ПАПКУ ДАННЫХ НЕ ТРОГАЕТ.

Ключи: --target ПАПКА  --data ПАПКА  --name ИМЯ  --regkey КЛЮЧ (не MoyDen)  --port N  --instance ИМЯ  (отдельный экземпляр: свои окно, порт, ID)
       --desktop-dir ПАПКА  --startup-dir ПАПКА  --start-dir ПАПКА  (куда класть ярлыки; по умолчанию — настоящие папки)
       --replace-old   остановить работающую прежнюю версию перед установкой: только «Мой день.exe» из папки программы
                       (сверяется полный путь процесса, чужие процессы не трогаются)
       --wallpaper-on  включить «Мой день» как обои (settings.json → wallpaper.window = true)
       --launch        запустить после установки        --uninstall   удалить        --quiet  без окон сообщений
"""
import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import winreg
from datetime import date
from pathlib import Path

VERSION = "1.0.0"
PUBLISHER = "Ulugbek Makhmudov"
DEFAULT_PORT = 8648
DEFAULT_DIR = "MoyDen"  # %LOCALAPPDATA%\Programs\MoyDen — программа, %LOCALAPPDATA%\MoyDen — данные
OTHER_BUILD_KEY = "MoyDen"  # ключ другой сборки «Мой день»: рядом с ней не ставимся
FROZEN = getattr(sys, "frozen", False)
PAYLOAD = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload"
ME = Path(sys.executable if FROZEN else __file__).resolve()
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
NOWIN = 0x08000000
# Системные программы — только по полному пути: установщик запускают из «Загрузок», а Windows ищет exe сначала в папке процесса
# и в текущей папке (подложенный туда powershell.exe выполнился бы).
SYS32 = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32"
POWERSHELL = SYS32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
TASKKILL = SYS32 / "taskkill.exe"
CMD = Path(os.environ.get("ComSpec") or SYS32 / "cmd.exe")


def ps(cmd):
    cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + cmd  # пути с кириллицей (рабочий стол в OneDrive) — без искажений
    return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NOWIN).stdout.strip()


def folder(name):
    s = ps(f"[Environment]::GetFolderPath('{name}')")
    if not s or not Path(s).is_absolute():  # пустой ответ дал бы текущую папку — ярлыки ушли бы не туда
        raise OSError(f"Не удалось узнать папку Windows «{name}»")
    return Path(s)


def lnk(path, target, args="", icon=None, desc="Мой день — дашборд дня, проекты и календарь"):
    q = lambda s: str(s).translate(PSQ)
    ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(path)}');$s.TargetPath='{q(target)}';$s.Arguments='{q(args)}';"
       f"$s.WorkingDirectory='{q(Path(target).parent)}';$s.IconLocation='{q(icon or target)},0';$s.Description='{q(desc)}';$s.Save()")


def lnk_target(path):
    q = str(path).translate(PSQ)
    return ps(f"(New-Object -ComObject WScript.Shell).CreateShortcut('{q}').TargetPath")


class _FileOp(ctypes.Structure):  # SHFILEOPSTRUCTW
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


def recycle(path):
    """В Корзину, не насовсем (FO_DELETE + FOF_ALLOWUNDO, без окон). Итог — файла больше нет на месте."""
    p = os.path.abspath(str(path))  # без resolve(): у ссылки в Корзину ушла бы её цель
    if os.path.islink(p) or not os.path.isfile(p):  # только файл; символьная ссылка — не трогаем (файлы OneDrive тоже точки повторного разбора, но не ссылки — их можно)
        return False
    op = _FileOp(None, 3, p + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, 0, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted and not Path(p).exists()


def msg(text, quiet, err=False):
    print(text)
    if not quiet:
        ctypes.windll.user32.MessageBoxW(0, text, "Установка «Мой день»", 0x10 if err else 0x40)


PSQ = str.maketrans({"'": "''", "\u2019": "\u2019\u2019", "\u2018": "\u2018\u2018", "\u201a": "\u201a\u201a", "\u201b": "\u201b\u201b"})  # R2: все виды одинарных кавычек PowerShell
SAFE_NAME = re.compile(r'[^\\/:*?"<>|\x00-\x1f]{1,60}')
SAFE_KEY = re.compile(r"MoyDen[\w.\-]{0,40}")


def proc_path(pid):
    """Полный путь к файлу процесса (пусто, если процесса нет или доступа нет)."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.c_ulong(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k.CloseHandle(h)


def norm_path(p):
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(p).lower()


def stop_exact(path):
    """Остановить процессы, запущенные ровно из этого файла: сравнивается полный путь, без шаблонов."""
    q = str(path).translate(PSQ)
    ps(f"Get-Process | Where-Object {{ $_.Path -and ([IO.Path]::GetFullPath($_.Path) -ieq [IO.Path]::GetFullPath('{q}')) }} | Stop-Process -Force")


def stop_old(port, allowed):
    """остановить «Мой день» на этом порту, только если процесс запущен из разрешённого файла
    (старая версия в папке данных или прежняя установка). Чужой процесс на порту не трогаем."""
    allowed = [Path(p) for p in allowed]
    ok = {norm_path(p) for p in allowed}
    killed = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            j = json.load(r)
            pid, st_app = j.get("pid"), j.get("app")
    except (OSError, ValueError):
        pid = st_app = None
    if isinstance(pid, int) and not isinstance(pid, bool) and st_app == "moy-den-app":
        path = proc_path(pid)
        if path and norm_path(path) in ok:
            subprocess.run([str(TASKKILL), "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
            killed = pid
            for _ in range(40):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.5)
                    time.sleep(0.25)
                except OSError:
                    break
        else:
            print(f"порт {port}: процесс {pid} ({path or 'путь неизвестен'}) — не «Мой день» из папки программы или данных; не останавливаю")
    for p in allowed:  # загрузчик однофайлового exe (родительский процесс) — по точному пути
        if p.suffix.lower() == ".exe":
            stop_exact(p)
    return killed


def other_build():
    """Папка другой сборки «Мой день», если она установлена (ключ OTHER_BUILD_KEY; есть ключ без InstallLocation — тоже она), иначе None. У той сборки свои ярлыки с тем же
    именем, окно с тем же заголовком и свой порт: две программы рядом мешали бы друг другу, поэтому публичная рядом не ставится."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{OTHER_BUILD_KEY}") as k:
            try:
                return str(winreg.QueryValueEx(k, "InstallLocation")[0] or "(папка не указана)")
            except OSError:
                return "(папка не указана)"
    except OSError:
        return None


def plain(p):
    """Обычный абсолютный путь с буквой диска (после resolve). Пути с \\\\?\\, \\\\.\\, сетевые и пустые — ValueError: такие пути
    обходили бы сравнение с настоящими папками Windows."""
    s = str(p or "")
    if not s or s.startswith(("\\\\", "//")):
        raise ValueError(s)
    rp = Path(s).resolve()
    if not re.fullmatch(r"[A-Za-z]:\\", rp.anchor):
        raise ValueError(s)
    return rp


def other_data(other):
    """Папка данных другой сборки (из её «настройки-установки.json»), если её удаётся прочитать."""
    try:
        d = json.loads((Path(other) / "настройки-установки.json").read_text("utf-8-sig")).get("dataDir")
        return d if isinstance(d, str) and d else None
    except (OSError, ValueError, AttributeError):
        return None


def side_by_side_ok(a, other):
    """Проверочная установка рядом с другой сборкой (--side-by-side-test). Разрешена только отдельным экземпляром со своим портом,
    когда ВСЕ места записи заданы явно обычными путями и лежат внутри папки проверки (папки, где лежит папка программы), а сама папка
    проверки не содержит и не лежит внутри защищённых мест: папок Windows («Главное меню», «Программы», «Автозагрузка», «Рабочий
    стол»), папки другой сборки и её данных. Если что-то проверить не удалось — отказ."""
    if not (getattr(a, "side_by_side_test", False) and a.instance and a.port and a.target and a.data and a.start_dir and a.startup_dir
            and a.desktop_dir):
        return False
    if not other or not Path(other).is_absolute():  # папка другой сборки неизвестна — защищать нечем, не разрешаем
        return False
    try:
        root = plain(a.target).parent
        paths = [plain(p) for p in (a.target, a.data, a.start_dir, a.startup_dir, a.desktop_dir)]
        prot = [plain(folder(n)) for n in ("StartMenu", "Programs", "Startup", "Desktop")] + [plain(other)]
        d = other_data(other)
        if d:
            prot.append(plain(d))
    except (OSError, ValueError):
        return False
    if any(x == root or x.is_relative_to(root) or root.is_relative_to(x) for x in prot):
        return False
    return all(p.is_relative_to(root) and p != root for p in paths)


def install(a):
    local = Path(os.environ["LOCALAPPDATA"])
    target = Path(a.target or local / "Programs" / DEFAULT_DIR).resolve()
    data = Path(a.data or local / DEFAULT_DIR).resolve()
    if not isinstance(a.regkey, str) or not SAFE_KEY.fullmatch(a.regkey):
        raise SystemExit(msg("Недопустимое имя ключа реестра.", a.quiet, True) or 7)
    if a.regkey.casefold() == OTHER_BUILD_KEY.casefold():  # ключ другой сборки: запись перезаписала бы её, удаление — стёрло бы
        raise SystemExit(msg(f"Ключ {OTHER_BUILD_KEY} принадлежит другой сборке «Мой день». Укажите другой ключ.", a.quiet, True) or 7)
    other = other_build()
    if other and not side_by_side_ok(a, other):
        raise SystemExit(msg(f"На компьютере уже установлена другая сборка «Мой день»:\n{other}\n\nЧтобы они не мешали друг другу, "
                             "сначала удалите её (Параметры → Приложения), затем запустите установку снова.", a.quiet, True) or 8)
    # папка программы не может совпадать с папкой данных, содержать её или быть чужой непустой папкой
    if target == data or data.is_relative_to(target) or target.is_relative_to(data):
        raise SystemExit(msg(f"Папка программы и папка данных не должны пересекаться:\n{target}\n{data}", a.quiet, True) or 4)
    if target.exists() and any(target.iterdir()) and not (target / "установка.json").exists():
        raise SystemExit(msg(f"Папка {target} не пуста и это не прежняя установка «Мой день». Укажите другую папку.", a.quiet, True) or 5)
    if not SAFE_NAME.fullmatch(a.name) or not SAFE_KEY.fullmatch(a.regkey):
        raise SystemExit(msg("Недопустимое имя программы или ключа реестра.", a.quiet, True) or 7)
    data.mkdir(parents=True, exist_ok=True)  # data.json программа создаст сама при первом запуске (пустой)
    if a.replace_old:  # прежняя установка — только по точному пути файла
        stop_old(a.port or DEFAULT_PORT, [target / f"{a.name}.exe"])
    target.mkdir(parents=True, exist_ok=True)
    exe = target / f"{a.name}.exe"
    for attempt in range(20):  # прежняя установленная версия могла ещё завершаться
        try:
            shutil.copyfile(PAYLOAD / "MoyDen.exe", exe)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        raise SystemExit(msg(f"Не удалось заменить {exe}: программа запущена. Закройте её (значок у часов → «Выйти») и повторите.", a.quiet, True) or 3)
    shutil.copyfile(PAYLOAD / "мой_день.ico", target / "мой_день.ico")
    uninst = target / f"Удалить {a.name}.exe"
    if FROZEN:
        shutil.copyfile(ME, uninst)
    cfg = {"dataDir": str(data), "version": VERSION}
    if a.port:
        cfg["port"] = a.port
    if a.instance:
        cfg["instance"] = a.instance
    (target / "настройки-установки.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    dirs = {"start": Path(a.start_dir) if a.start_dir else folder("Programs"),
            "startup": Path(a.startup_dir) if a.startup_dir else folder("Startup")}  # ярлыка на рабочем столе нет
    links = []
    # откат: ярлыки прежней версии «Мой день» сохраняются в <данные>\резерв\ярлыки-до-установки\ — удаление вернёт их на место
    kind_ru = {"start": "Пуск", "desktop": "Рабочий стол", "startup": "Автозагрузка"}
    bdir = data / "резерв" / "ярлыки-до-установки"
    try:
        prev = json.loads((target / "установка.json").read_text("utf-8"))
    except (OSError, ValueError):
        prev = None
    prev = prev if isinstance(prev, dict) else {}
    lb = prev.get("linkBackups")
    backups = [b for b in lb if isinstance(b, dict)] if isinstance(lb, list) else []  # битый манифест прежней установки не роняет установку
    try:  # обои до установки: удаление вернёт именно это состояние (при переустановке — состояние до первой установки)
        cur = json.loads((data / "settings.json").read_text("utf-8-sig"))
        cur_wp = isinstance(cur, dict) and isinstance(cur.get("wallpaper"), dict) and cur["wallpaper"].get("window") is True
    except (OSError, ValueError):
        cur_wp = False
    wp_before = prev["wallpaperBefore"] if isinstance(prev.get("wallpaperBefore"), bool) else cur_wp
    for kind, d in dirs.items():
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{a.name}.lnk"
        if p.exists():
            old_t = lnk_target(p)
            try:
                ours = bool(old_t) and Path(old_t).parent.resolve() == target
            except OSError:
                ours = False
            if not ours:  # ярлык прежней версии (или чужой) — сохранить для отката
                bk = bdir / f"{kind_ru[kind]} — {a.name}.lnk"
                try:
                    bdir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, bk)
                    backups = [b for b in backups if b.get("original") != str(p)] + [{"kind": kind, "original": str(p), "backup": str(bk), "target": old_t}]
                except OSError as e:
                    print(f"ярлык {p} не сохранён в резерв: {e}")
        lnk(p, exe, "--autostart" if kind == "startup" else "", icon=target / "мой_день.ico")
        links.append(str(p))
    # прежний ярлык «Мой день» на рабочем столе — копия в резерв и в Корзину; чужой ярлык с тем же именем не трогаем
    desk = (Path(a.desktop_dir) if a.desktop_dir else folder("Desktop")) / f"{a.name}.lnk"
    if desk.exists():
        old_t = lnk_target(desk)
        try:
            ours = bool(old_t) and norm_path(old_t) == norm_path(exe)  # «наш» — ярлык на программу, не любой в её папке
        except OSError:
            ours = False
        if ours:
            bk = bdir / f"{kind_ru['desktop']} — {a.name} (до {VERSION}).lnk"  # своё имя: резерв первой установки не затирается
            copied = False
            try:
                bdir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(desk, bk)
                copied = True
                backups = [b for b in backups if b.get("original") != str(desk)] + [{"kind": "desktop", "original": str(desk), "backup": str(bk), "target": old_t}]
            except OSError as e:
                print(f"ярлык {desk} не сохранён в резерв: {e}")
            if copied:  # без копии этого запуска ярлык не трогаем: на диске без Корзины он удалился бы насовсем
                print(("ярлык с рабочего стола — в Корзину: " if recycle(desk) else "не удалось убрать в Корзину ярлык с рабочего стола: ") + str(desk))
            else:
                print(f"копия ярлыка не создана — ярлык на рабочем столе оставлен: {desk}")
        else:
            print(f"на рабочем столе ярлык «{a.name}» ведёт не к «Мой день» ({old_t or 'цель неизвестна'}) — не трогаю")
    files = [str(target / f) for f in (exe.name, uninst.name, "мой_день.ico", "настройки-установки.json", "установка.json")]
    manifest = {"name": a.name, "regkey": a.regkey, "target": str(target), "data": str(data), "links": links, "files": files, "version": VERSION,
                "linkBackups": backups, "wallpaperBefore": wp_before}
    (target / "установка.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    size_kb = sum(f.stat().st_size for f in target.iterdir() if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{a.regkey}") as k:
        for n, v in (("DisplayName", a.name), ("DisplayVersion", VERSION), ("Publisher", PUBLISHER), ("DisplayIcon", str(target / "мой_день.ico")),
                     ("InstallLocation", str(target)), ("UninstallString", f'"{uninst}" --uninstall --manifest "{target / "установка.json"}"'),
                     ("QuietUninstallString", f'"{uninst}" --uninstall --quiet --manifest "{target / "установка.json"}"'),
                     ("InstallDate", date.today().strftime("%Y%m%d")), ("Comments", f"Данные: {data}")):
            winreg.SetValueEx(k, n, 0, winreg.REG_SZ, v)
        for n, v in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(k, n, 0, winreg.REG_DWORD, v)
    if a.wallpaper_on:
        sp = data / "settings.json"
        s = json.loads(sp.read_text("utf-8-sig")) if sp.exists() else {}
        s.setdefault("wallpaper", {})["window"] = True
        tmp = sp.with_name("settings.json.tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.replace(tmp, sp)
    if a.launch:
        subprocess.Popen([str(exe)], cwd=str(target), creationflags=0x00000008 | 0x00000200)
    msg(f"«Мой день» {VERSION} установлен.\nПрограмма: {target}\nДанные: {data}" + ("\nОбои включены." if a.wallpaper_on else ""), a.quiet)
    print(json.dumps(manifest, ensure_ascii=False))


def uninstall(a):
    """манифесту не верим. Папка программы — та, где лежит сам удалитель (при проверке из исходников — папка манифеста);
    удаляются только файлы с фиксированными именами, ключ реестра — только если указывает на эту папку; папка данных не трогается."""
    mf = Path(a.manifest).resolve() if a.manifest else ME.parent / "установка.json"
    target = mf.parent.resolve()
    if FROZEN and target != ME.parent.resolve():
        raise SystemExit(msg("Удаление запускается из папки программы «Мой день» (Параметры → Приложения → Удалить).", a.quiet, True) or 6)
    try:
        m = json.loads(mf.read_text("utf-8"))
    except (OSError, ValueError):
        m = None
    # без настоящего маркера установки в этой папке ничего не трогаем
    try:
        ok = isinstance(m, dict) and Path(str(m.get("target"))).resolve() == target and (target / "настройки-установки.json").is_file()
    except OSError:
        ok = False
    if not ok:
        raise SystemExit(msg(f"В папке {target} нет установленной программы «Мой день» — удалять нечего.", a.quiet, True) or 6)
    other = other_build()  # папку и ключ другой сборки удаляет только её собственный удалитель
    try:
        theirs = bool(other) and Path(other).is_absolute() and Path(other).resolve() == target
    except OSError:
        theirs = True
    if str(m.get("regkey") or "").casefold() == OTHER_BUILD_KEY.casefold() or theirs:
        raise SystemExit(msg("Это папка другой сборки «Мой день» — её удаляют её собственным удалителем.", a.quiet, True) or 8)
    name = m.get("name") if isinstance(m.get("name"), str) and SAFE_NAME.fullmatch(m["name"]) else "Мой день"
    regkey = m.get("regkey") if isinstance(m.get("regkey"), str) and SAFE_KEY.fullmatch(m["regkey"]) and m["regkey"].casefold() != OTHER_BUILD_KEY.casefold() else None
    try:
        cfg = json.loads((target / "настройки-установки.json").read_text("utf-8"))
    except (OSError, ValueError):
        cfg = {}
    port = cfg.get("port") if isinstance(cfg, dict) and isinstance(cfg.get("port"), int) else DEFAULT_PORT
    data_txt = cfg.get("dataDir") if isinstance(cfg, dict) and isinstance(cfg.get("dataDir"), str) else "(папка данных не изменялась)"
    stop_old(port, [target / f"{name}.exe"])
    for p in m.get("links") if isinstance(m.get("links"), list) else []:
        p = Path(str(p))
        try:
            if p.suffix.lower() == ".lnk" and p.exists() and Path(lnk_target(p)).parent.resolve() == target:  # чужие ярлыки не трогаем
                p.unlink()
        except OSError:
            pass
    # откат: вернуть ярлыки прежней версии на те места, где стояли наши, и выключить обои (старая версия читает этот флаг)
    restored = []
    ours = {str(Path(str(x))) for x in (m.get("links") if isinstance(m.get("links"), list) else [])}
    for b in m.get("linkBackups") if isinstance(m.get("linkBackups"), list) else []:
        try:
            orig, bk = Path(str(b.get("original"))), Path(str(b.get("backup")))
            if (str(orig) in ours and orig.suffix.lower() == ".lnk" and bk.suffix.lower() == ".lnk" and bk.parent.name == "ярлыки-до-установки"
                    and bk.is_file() and not orig.exists() and orig.parent.is_dir()):
                shutil.copyfile(bk, orig)
                restored.append(str(orig))
        except (OSError, AttributeError):
            pass
    wallpaper_off = False
    want = m.get("wallpaperBefore") is True  # обои — как были до установки
    try:
        sp = Path(data_txt) / "settings.json"
        st = json.loads(sp.read_text("utf-8-sig")) if sp.is_file() else None
        if isinstance(st, dict) and isinstance(st.get("wallpaper"), dict) and st["wallpaper"].get("window") is True and not want:
            st["wallpaper"]["window"] = False
            tmp = sp.with_name("settings.json.tmp")
            tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", "utf-8")
            os.replace(tmp, sp)
            wallpaper_off = True
    except (OSError, ValueError):
        pass
    if regkey:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{regkey}") as k:
                loc = winreg.QueryValueEx(k, "InstallLocation")[0]
            if Path(loc).resolve() == target:  # ключ чужой программы не удаляем
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{regkey}")
        except OSError:
            pass
    # ключ уведомлений «Время вышло» — только свой (значок указывает в эту папку)
    toast_key = "Software\\Classes\\AppUserModelId\\MoyDen.App" + ("." + cfg["instance"] if isinstance(cfg, dict) and isinstance(cfg.get("instance"), str)
                                                                         and re.fullmatch(r"[\w.\-]{1,40}", cfg["instance"]) else "")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, toast_key) as k:
            icon = winreg.QueryValueEx(k, "IconUri")[0]
        if Path(icon).resolve().parent == target:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, toast_key)
    except OSError:
        pass
    for f in (f"{name}.exe", "мой_день.ico", "настройки-установки.json", "установка.json", "значок-уведомлений.png"):
        try:
            (target / f).unlink(missing_ok=True)
        except OSError:
            pass
    msg(f"«{name}» удалён. Данные остались на месте:\n{data_txt}" + (f"\nЯрлыки прежней версии возвращены: {len(restored)}." if restored else "")
        + ("\nОбои выключены." if wallpaper_off else ""), a.quiet)
    # сам удалитель занят, пока работает: его и пустую папку убирает cmd после выхода; пути — через переменные, без склейки строки
    env = dict(os.environ, MOYDEN_UNINST=str(target / f"Удалить {name}.exe"), MOYDEN_DIR=str(target))
    subprocess.Popen(f'"{CMD}" /v:on /c "!SystemRoot!\\System32\\ping.exe -n 3 127.0.0.1 >nul & del /f /q "!MOYDEN_UNINST!" & rmdir "!MOYDEN_DIR!""',
                     env=env, creationflags=NOWIN, close_fds=True)
    print(json.dumps({"removed": str(target), "data_kept": data_txt, "links_restored": restored, "wallpaper_off": wallpaper_off}, ensure_ascii=False))


def parser():
    ap = argparse.ArgumentParser(description="Установка «Мой день»")
    ap.add_argument("--target"), ap.add_argument("--data"), ap.add_argument("--name", default="Мой день"), ap.add_argument("--regkey", default="MoyDenApp")
    ap.add_argument("--port", type=int), ap.add_argument("--instance"), ap.add_argument("--manifest")
    ap.add_argument("--desktop-dir"), ap.add_argument("--startup-dir"), ap.add_argument("--start-dir")
    for f in ("--replace-old", "--wallpaper-on", "--launch", "--uninstall", "--quiet", "--side-by-side-test"):
        ap.add_argument(f, action="store_true")
    return ap


def main():
    a = parser().parse_args()
    try:
        uninstall(a) if a.uninstall else install(a)
    except OSError as e:  # папка Windows не определилась, нет доступа и т. п. — сообщение вместо падения
        raise SystemExit(msg(f"Установка «Мой день» не выполнена: {e}", a.quiet, True) or 9)


if __name__ == "__main__":
    main()
