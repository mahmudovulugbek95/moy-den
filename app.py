"""Приложение «Мой день»: своё окно (движок Edge WebView2), значок в трее, сервер дашборда внутри.

Запуск: «Мой день.exe» (или .venv\\Scripts\\pythonw.exe app.py).
  --autostart   запуск при входе в Windows (то же окно; обои — если включены в settings.json)
  --hidden      только трей и сервер, окно не показывать
Второй запуск не создаёт второй экземпляр, а выводит окно на передний план.
Закрытие окна (×) прячет его в трей; «Выход» в меню значка останавливает приложение и сервер.
"""
import base64
import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import winsound
from argparse import Namespace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

VERSION = "1.0.0"
FROZEN = getattr(sys, "frozen", False)
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def install_cfg():
    """«настройки-установки.json» рядом с программой: папка данных (dataDir); для проверки в проверочном экземпляре — port и instance."""
    try:
        c = json.loads((BASE / "настройки-установки.json").read_text("utf-8-sig"))
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


CFG = install_cfg()
DATA = Path(CFG["dataDir"]) if isinstance(CFG.get("dataDir"), str) and Path(CFG["dataDir"]).is_dir() else BASE
INSTANCE = str(CFG.get("instance") or "")
import server as S  # noqa: E402
import wallpaper as WPM  # noqa: E402

S.HERE = DATA  # данные и журнал — в папке данных (дашборд)
S.PAGE_DIR = Path(getattr(sys, "_MEIPASS", BASE)) if FROZEN else BASE  # страница и значок — из самой программы, не из папки данных 
PORT = int(CFG.get("port") or S.DEFAULT_PORT)
URL = f"http://127.0.0.1:{PORT}/"
STATE = DATA / "окно.json"
TITLE = "Мой день" + (f" ({INSTANCE})" if INSTANCE else "")
WP_TITLE = TITLE + " — обои"
PIN_HINT = ("Чтобы «Мой день» всегда был под рукой, щёлкните правой кнопкой мыши по его значку на панели задач "
            "и выберите «Закрепить на панели задач».")

# ---------- уведомление Windows «Время вышло», которое не исчезает само ----------
# ID уведомлений — свой, в HKCU\Software\Classes\AppUserModelId (так регистрирует уведомления программа без пакета MSIX). ID окна и ярлыка
# «Пуска» не меняется: закреплённая пользователем кнопка на панели задач остаётся одной (у окна и ярлыка ID из пути exe).
TOAST_ID = "MoyDen.App" + ("." + INSTANCE if INSTANCE else "")
TOAST_KEY = "Software\\Classes\\AppUserModelId\\" + TOAST_ID
TOAST_PNG = BASE / "значок-уведомлений.png"
PS_EXE = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
TOAST_MODES = ("show", "dry", "silent", "remove")
_CTRL = {i: None for i in range(32) if i not in (9, 10, 13)}
# Скрипт — только ASCII (PowerShell читает его со стандартного ввода); ID и текст — base64, поэтому кавычки и кириллица в названии задачи
# в команду не попадают. dry — всё, кроме показа (проверка); silent — сразу в центр уведомлений без всплывания (проверка в проверочном экземпляре).
# Setting у программы без пакета Windows часто не отдаёт (0x80070490, проверено 28.10 и для ID самого PowerShell): отказ — только при явном Disabled*.
_PS_TOAST = r"""$ErrorActionPreference='Stop'
$null=[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null=[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
$id=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%ID%')); $mode='%MODE%'; $m=[Windows.UI.Notifications.ToastNotificationManager]
if($mode -eq 'remove'){ $m::History.Remove('pomodoro','moyden',$id); 'ok'; exit 0 }
$x=New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%XML%')))
$n=$m::CreateToastNotifier($id); $s='unknown'; try { $s=[string]$n.get_Setting() } catch { }
$t=New-Object Windows.UI.Notifications.ToastNotification $x; $t.Tag='pomodoro'; $t.Group='moyden'
if($mode -eq 'dry'){ 'dry setting='+$s; exit 0 }
if($s -like 'Disabled*'){ 'setting='+$s; exit 3 }
if($mode -eq 'silent'){ $t.SuppressPopup=$true }
$n.Show($t)
if($mode -eq 'silent'){ Start-Sleep -Milliseconds 700; $a=@($m::History.GetHistory($id) | Where-Object { $_.Tag -eq 'pomodoro' }).Count; $m::History.Remove('pomodoro','moyden',$id); Start-Sleep -Milliseconds 400; $b=@($m::History.GetHistory($id) | Where-Object { $_.Tag -eq 'pomodoro' }).Count; 'silent found='+$a+' after='+$b; exit 0 }
'ok'
"""


def toast_xml(title, text):
    """reminder держится на экране, пока его не закроют; без кнопки Windows считает его обычным (исчезает через 5 с) — поэтому кнопка
    «Закрыть» системная (программе её обрабатывать не нужно). Звук тоста выключен: звук свой, три раза, по выключателю таймера."""
    c = lambda v: xml_escape(str(v).translate(_CTRL), {'"': "&quot;"})
    return ('<toast scenario="reminder"><visual><binding template="ToastGeneric">'
            f"<text>{c(title)}</text><text>{c(text)}</text></binding></visual>"
            '<actions><action activationType="system" arguments="dismiss" content=""/></actions><audio silent="true"/></toast>')


def toast_run(title="", text="", mode="show", run=subprocess.run):
    """Тост через Windows PowerShell 5.1: WinRT-уведомления есть в каждой Windows 10/11, лишних пакетов в программе нет.
    Возвращает «ok» (dry/silent — строку проверки) или причину, по которой тост не показан."""
    if mode not in TOAST_MODES:
        raise ValueError(mode)
    if mode in ("show", "remove") and os.environ.get("MOYDEN_NO_TOAST") == "1":  # проверки (unit, круг, проверочный экземпляр) уведомлений на экран не выводят
        return "не показан: MOYDEN_NO_TOAST (проверка)"
    b = lambda v: base64.b64encode(v.encode("utf-8", "replace")).decode("ascii")
    script = _PS_TOAST.replace("%ID%", b(TOAST_ID)).replace("%MODE%", mode).replace("%XML%", b(toast_xml(title, text)))
    try:
        p = run([str(PS_EXE), "-NoProfile", "-NonInteractive", "-Command", "-"], input=script.encode("ascii"), capture_output=True,
                timeout=10, creationflags=0x08000000)  # завис — уведомление трея без долгой паузы
    except (OSError, subprocess.SubprocessError) as e:
        return f"не показан: {type(e).__name__}"
    out = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    last = out[-1].strip() if out else ""
    if p.returncode == 0 and (last == "ok" or last.startswith(("dry setting=", "silent found="))):
        return last
    err = " ".join((p.stderr or b"").decode("utf-8", "replace").split())
    return f"не показан (код {p.returncode}): {last or err[:240]}"


def toast_register():
    """Имя «Мой день» и значок уведомлений в Windows (HKCU, без прав администратора). Значок — PNG из мой_день.ico самой программы:
    новый значок меняется заменой одного файла ico и пересборкой. Только у установленной программы: из исходников ключ не пишется,
    чтобы проверка не трогала ключ программы пользователя."""
    import io
    import winreg
    from PIL import Image
    ico = Path(S.PAGE_DIR) / "мой_день.ico"
    with Image.open(ico if ico.exists() else BASE / "мой_день.ico") as im:
        sizes = im.info.get("sizes")
        if sizes:
            im.size = max(sizes)
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, "PNG")
    png = buf.getvalue()
    if not TOAST_PNG.is_file() or TOAST_PNG.read_bytes() != png:
        tmp = TOAST_PNG.with_name(TOAST_PNG.name + ".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, TOAST_PNG)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TOAST_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, TITLE)
        winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(TOAST_PNG))
    return "ok"


def aumid_for(frozen, instance):
    """собранная программа явный AppUserModelID не задаёт — Windows выводит его из пути exe, и он одинаков у окна, ярлыка «Пуска»
    и уже закреплённого в панели задач ярлыка: в панели одна кнопка. Явный ID — только при
    запуске из исходников, чтобы окно не слилось с другими программами на pythonw.exe."""
    return None if frozen else "MoyDen.App" + ("." + instance if instance else "")


AUMID = aumid_for(FROZEN, INSTANCE)


def explicit_aumid():
    """Явный AppUserModelID процесса, как его видит Windows (None — ID выводится из пути exe)."""
    p = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(p))
    except OSError:
        return None
    v = p.value
    if v is not None:
        ctypes.windll.ole32.CoTaskMemFree(p)
    return v if hr == 0 and v else None
k32, u32 = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.windll.user32  # код ошибки мьютекса не теряется
log = S.log


def call(path, timeout=2.0):
    req = urllib.request.Request(URL + path.lstrip("/"), data=b"{}" if path.startswith("api/app") else None,
                                 method="POST" if path.startswith("api/app") else "GET", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except ValueError:
            return None
    except (OSError, ValueError):
        return None


def port_busy():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def message(text):
    u32.MessageBoxW(0, text, TITLE, 0x40)


STATE_LOCK = threading.Lock()  # окно.json пишут поток окна (размеры) и таймер подсказки — по одному


def load_state():
    try:
        st = json.loads(STATE.read_text("utf-8"))
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    with STATE_LOCK:
        st = load_state()
        st.update({k: int(v) for k, v in kw.items() if isinstance(v, (int, float))})
        try:
            STATE.write_text(json.dumps(st), "utf-8")
        except OSError:
            pass


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint), ("showCmd", ctypes.c_uint), ("ptMin", ctypes.c_long * 2),
                ("ptMax", ctypes.c_long * 2), ("rcNormal", RECT)]


_U32W = ctypes.WinDLL("user32", use_last_error=True)  # свой экземпляр с типами: общий windll.user32 используют pystray и pywebview
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
_U32W.EnumWindows.argtypes = [_ENUM, ctypes.c_void_p]
_U32W.EnumWindows.restype = ctypes.c_bool
_U32W.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_U32W.GetWindowThreadProcessId.restype = ctypes.c_ulong
_U32W.InternalGetWindowText.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_U32W.InternalGetWindowText.restype = ctypes.c_int


def find_own(title):
    """Окно ЭТОГО процесса с таким заголовком (0 — нет). FindWindow вернул бы первое окно с этим заголовком во всей системе, а у другой
    сборки «Мой день» на том же компьютере заголовок тот же: программа спрятала бы, мигнула или переподвесила бы чужое окно.
    Окна верхнего уровня перебираются одним проходом EnumWindows; заголовок читается без отправки сообщений окну
    (InternalGetWindowText), принадлежность — по номеру процесса. Дескриптор — в том же виде, что у остальных вызовов user32 здесь."""
    pid, found = os.getpid(), []

    def cb(h, _):
        owner = ctypes.c_ulong()
        _U32W.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(512)
            _U32W.InternalGetWindowText(h, buf, 512)
            if buf.value == title:
                found.append(h)
                return False
        return True
    _U32W.EnumWindows(_ENUM(cb), None)
    return ctypes.c_int(found[0]).value if found else 0


def minimized():
    h = find_own(TITLE)
    return bool(h and u32.IsIconic(h))


def save_pos(x, y):
    # свёрнутое окно Windows уносит на −32000 — такие координаты не запоминаем
    if minimized() or not isinstance(x, int) or not isinstance(y, int) or x <= -10000 or y <= -10000:
        return
    save_state(x=x, y=y)


def save_size(w, h):
    if minimized() or not isinstance(w, int) or not isinstance(h, int) or w < 400 or h < 300:
        return
    save_state(width=w, height=h)


def on_screen(x, y, w):
    """полоса заголовка окна видна хотя бы на одном мониторе (монитор могли отключить)."""
    if not all(isinstance(v, int) for v in (x, y, w)):
        return False
    r = RECT(x + 40, y, x + max(80, min(w, 400) - 40), y + 30)
    return bool(u32.MonitorFromRect(ctypes.byref(r), 0))  # MONITOR_DEFAULTTONULL: 0 — ни на одном мониторе


def settings():
    try:
        s = json.loads((DATA / "settings.json").read_text("utf-8-sig"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def start_server():
    S.ensure_data(DATA / "data.json")  # первый запуск: пустой список задач
    handler = RotatingFileHandler(DATA / "server.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    args = Namespace(data=str(DATA / "data.json"), calendars=str(DATA / "calendars.local.json"), cache=str(DATA / "кэш-календаря"),
                     refresh=300, settings=str(DATA / "settings.json"), weather_url=S.WEATHER_URL,
                     weather_cache=str(DATA / "кэш-погоды.json"), weather_refresh=1800, weather_retry=120, weather_max_age=3600)
    app = S.App(args)
    for attempt in range(6):  # порт мог на миг занять сервер без окна, который поднял клиент API, — повторяем
        try:
            srv = S.Server(("127.0.0.1", PORT), S.Handler)
            break
        except OSError:
            if attempt == 5:
                raise
            st = call("api/ping")
            if st and st.get("app") == S.APP and not st.get("ui"):
                call("api/app/quit")
            time.sleep(0.5)
    srv.app = app
    try:
        app.store.migrate_file()
    except (S.ApiError, OSError) as e:
        log.warning("migration skipped: %s", getattr(e, "message", type(e).__name__))
    threading.Thread(target=app.calendars.run, name="calendars", daemon=True).start()
    threading.Thread(target=app.weather.run, name="weather", daemon=True).start()
    threading.Thread(target=app.pomodoro.run, args=(app.pomo_notify,), name="pomodoro", daemon=True).start()
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    log.info("app started, server on port %s", PORT)
    return app, srv


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]


class UI:
    def __init__(self, srv):
        self.srv, self.win, self.wp, self.icon, self.quitting = srv, None, None, None, False
        self._wp_pin = None  # (окно обоев, хозяин Progman) — одной парой: сторож пересоздаёт обои из своего потока
        self.guard = None
        self.wp_gen = 0  # номер включения обоев: проверка значков старого включения не работает с новым
        self.icons_lock = threading.Lock()  # быстрое выкл/вкл — две проверки значков не переносят одновременно
        self._icons_saved = None  # последний записанный резерв позиций: тот же расклад второй раз не пишется
        self.wp_prev = None  # окно, активное до щелчка по обоям, — ему клавиатура возвращается после Enter/Esc
        self.focus_lock = threading.Lock()  # запросы фокуса приходят из потоков сервера — смена активного окна по одному
        self.toast_shown = False  # тост «Время вышло» на экране — после подтверждения в окне или на обоях его убрать
        self.stop_ev = threading.Event()  # «Выход»: повтор уведомления не ждёт
        self.repeat_every, self.repeat_times = 120, 3  # повтор уведомления трея, если тост не показался (Windows без WinRT, уведомления выключены)

    @property
    def wp_hwnd(self):
        p = self._wp_pin
        return p[0] if p else None

    @wp_hwnd.setter
    def wp_hwnd(self, v):
        p = self._wp_pin
        self._wp_pin = (v, p[1] if p else None) if v else None

    @property
    def wp_host(self):
        p = self._wp_pin
        return p[1] if p else None

    @wp_host.setter
    def wp_host(self, v):
        p = self._wp_pin
        self._wp_pin = (p[0], v) if p else None

    def show(self):
        if not self.win:
            return
        h0 = find_own(TITLE)
        if not h0 or not u32.IsWindowVisible(h0) or u32.IsIconic(h0):  # окно открывают снова (трей, повторный запуск) — «День» на сегодня
            threading.Thread(target=self.cal_home, name="cal-home", daemon=True).start()
        self.win.show()
        h = find_own(TITLE)
        if h and u32.IsZoomed(h) and not u32.IsIconic(h):  # развёрнутое окно не уменьшаем (SW_RESTORE снял бы «развернуть»)
            u32.SetForegroundWindow(h)
            return
        if h and u32.IsIconic(h):  # свёрнуто из развёрнутого — вернуть развёрнутым (restore() pywebview ставит обычный размер)
            wp = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
            if u32.GetWindowPlacement(h, ctypes.byref(wp)) and wp.flags & 0x2:  # WPF_RESTORETOMAXIMIZED
                u32.ShowWindow(h, 3)  # SW_SHOWMAXIMIZED
                u32.SetForegroundWindow(h)
                return
        self.win.restore()
        if h:
            u32.ShowWindow(h, 9)  # SW_RESTORE
            u32.SetForegroundWindow(h)

    def on_closing(self):
        if self.quitting:
            return True
        self.win.hide()  # крестик прячет окно в трей, сервер и клиент API продолжают работать
        threading.Thread(target=self.cal_home, name="cal-home", daemon=True).start()  # не ждём страницу в обработчике закрытия
        return False

    def cal_home(self):
        """окно открывают снова той же страницей (она не перезагружается) — календарь к «День» на сегодня, как при запуске."""
        try:
            self.win.evaluate_js("window.calHome&&window.calHome()")
        except Exception as e:  # окно закрыли совсем или страница не готова — при следующем запуске вид и так «День»
            log.warning("cal home: %s", e)

    def on_minimized(self):
        # окно свернули — при следующем открытии (панель задач, трей) календарь «День» на сегодня
        self.was_min = True
        threading.Thread(target=self.cal_home, name="cal-home", daemon=True).start()

    def on_restored(self):
        # свернули вечером, развернули утром — «День» на новое «сегодня», а не на дату сворачивания
        if getattr(self, "was_min", False):
            self.was_min = False
            threading.Thread(target=self.cal_home, name="cal-home", daemon=True).start()

    def wallpaper_on(self):
        """обои, на которых работает всё — клики, ввод текста, календарь. Окно над слоем значков, полоса слева — под значки."""
        import webview
        if self.wp:
            return
        wp = settings().get("wallpaper") if isinstance(settings().get("wallpaper"), dict) else {}
        self.wp_left = int(wp.get("iconsStrip", 120)) if str(wp.get("iconsStrip", 120)).isdigit() else 120
        l, t, r, b = WPM.work_area()
        self.wp_gen += 1
        gen = self.wp_gen
        w = self.wp = webview.create_window(WP_TITLE, URL + "?wallpaper=1&left=0&bottom=0", frameless=True, x=l + self.wp_left, y=t,
                                            width=r - l - self.wp_left, height=b - t, focus=False, resizable=False, background_color="#F3F0EA")
        w.events.shown += lambda: threading.Thread(target=self._pin, args=(gen,), daemon=True).start()
        w.events.closed += lambda: self._wp_closed(w)

    def _wp_closed(self, w=None):
        if w is not None and w is not self.wp:  # L8: позднее «закрыто» старого окна (события pywebview — в своём потоке) не стирает новое
            return
        self.wp = None
        self.wp_hwnd = None

    def _tray(self, text):
        """поле текста уведомления pystray — 256 символов, длиннее — ValueError и уведомления нет."""
        if not self.icon:
            return "нет значка в трее"
        if FROZEN and os.environ.get("MOYDEN_NO_TOAST") == "1":  # собранная программа на проверке (проверочный экземпляр) — и уведомление трея не на экран
            return "не показано: MOYDEN_NO_TOAST (проверка)"
        try:
            self.icon.notify(text if len(text) <= 255 else text[:254] + "…", TITLE)
            return "ok"
        except Exception as e:  # уведомление не должно обрывать проверку значков; причина — в журнал
            log.warning("tray notify: %s", e)
            return f"ошибка: {e}"

    def _pin(self, gen=None):
        for _ in range(100):
            h = find_own(WP_TITLE)
            if h:
                try:
                    info = WPM.pin_interactive(h, self.wp_left)
                    self._wp_pin = (h, info["host"])
                    log.info("wallpaper pinned (interactive): scheme %s, rect %s", info["scheme"], info["rect"])
                    threading.Thread(target=self._icons_check, args=(info["rect"], gen), name="desktop-icons", daemon=True).start()
                    if not self.guard:
                        self.guard = threading.Thread(target=self._watch, name="wallpaper-guard", daemon=True)
                        self.guard.start()
                except Exception as e:
                    log.warning("wallpaper pin failed: %s", e)
                return
            time.sleep(0.1)
        log.warning("wallpaper window not found")

    def _icons_check(self, rect, gen=None):
        """значки рабочего стола, оказавшиеся под окном обоев (Корзина бывает справа внизу), — в полосу слева.
        Прежние позиции — в резерв\значки-рабочего-стола-*.json; мест в полосе нет или автоупорядочение — сообщение в трее."""
        gen = self.wp_gen if gen is None else gen  # L9: номер включения — из wallpaper_on
        time.sleep(5)  # после входа в Windows Проводник расставляет значки не сразу
        if not self.icons_lock.acquire(timeout=30):  # L4: прошлая проверка висит на Проводнике — эту пропускаем, не копим потоки
            log.warning("desktop icons check skipped: previous check still running")
            return
        try:
            if self.quitting or not self.wp or gen != self.wp_gen:  # за паузу обои выключили или пересоздали
                log.info("desktop icons check skipped: wallpaper is off or re-created")
                return
            self._icons_move(rect)
        finally:
            self.icons_lock.release()

    def _icons_move(self, rect):
        try:
            x, y, w, h = rect
            win = (x, y, x + w, y + h)
            plan = WPM.icons_to_strip(win, self.wp_left, apply=False)
            if plan["moved"]:
                snap = json.dumps(WPM.desktop_icons(), ensure_ascii=True, indent=1)  # L6: ASCII — любое имя файла записывается
                if snap != self._icons_saved:
                    rez = DATA / "резерв"
                    rez.mkdir(exist_ok=True)
                    stamp = time.strftime('%Y%m%d-%H%M%S')
                    for k in range(100):  # L5: прежний резерв не перезаписывается, даже в ту же секунду
                        try:
                            with open(rez / f"значки-рабочего-стола-{stamp}{f'-{k}' if k else ''}.json", "x", encoding="utf-8") as fh:
                                fh.write(snap)
                            break
                        except FileExistsError:
                            continue
                    self._icons_saved = snap
                plan = WPM.icons_to_strip(win, self.wp_left, apply=True)
                log.info("desktop icons moved to the strip: %s", [(m["name"], m["from"], m["to"], m.get("ok")) for m in plan["moved"]])
                failed = [m["name"] for m in plan["moved"] if not m.get("ok")]
                if failed:  # неудачный перенос виден пользователю, а не только журналу
                    self._tray("Не удалось переставить значки рабочего стола из-под обоев: " + ", ".join(failed[:5]) + ". Перетащите их в полосу слева.")
            if plan["left_over"]:
                log.warning("desktop icons under the wallpaper: %s (auto-arrange %s)", plan["left_over"], plan["auto_arrange"])
                self._tray("Под обоями остались значки рабочего стола: " + ", ".join(plan["left_over"][:5]) + ". Освободите место в полосе слева"
                           + (" или выключите «Упорядочить значки автоматически»" if plan["auto_arrange"] else "") + ".")
        except Exception as e:
            log.warning("desktop icons check: %s", e)

    def _watch(self):
        """Сторож обоев: Проводник перезапустился или переставил окна — закрепить заново."""
        while not self.quitting:
            time.sleep(3)
            pin = self._wp_pin
            if not self.wp or not pin:
                continue
            try:
                if not WPM.pinned_ok(*pin):
                    host = WPM.icons_host()[0]
                    if u32.IsWindow(pin[0]) and host == pin[1]:
                        WPM.raise_top(pin[0])
                    else:
                        log.info("wallpaper lost (explorer restarted?) — re-creating")
                        old, self.wp, self.wp_hwnd = self.wp, None, None
                        try:
                            old.destroy()
                        except Exception:
                            pass
                        self.wallpaper_on()
            except Exception as e:
                log.warning("wallpaper guard: %s", e)

    def wallpaper_focus(self, fid="", hash_=""):
        """страница обоев сообщила о нажатии мыши — отдать обоям фокус клавиатуры (WPM.take_focus).
        Не вышло — основное окно на том же экране с тем же полем в фокусе: набор не теряется."""
        pin = self._wp_pin  # окно и хозяин — из одного включения обоев
        if not (self.wp and pin and pin[0] and pin[1]):
            return "off"
        h, host = pin
        if not self.focus_lock.acquire(timeout=2):  # прошлый запрос ещё меняет фокус (или Проводник занят) — не копим потоки
            return "busy"
        try:
            ok, prev, switched = WPM.take_focus(h, host)
            if switched:  # прежнее окно — только при настоящем переключении; был активен рабочий стол — возвращать некуда (под замком)
                self.wp_prev = prev
        finally:
            self.focus_lock.release()
        if ok:
            return "wallpaper"
        if not fid:  # щелчок не по полю — основное окно поверх программы пользователя не открываем
            log.info("wallpaper keyboard focus failed, no field — window not shown")
            return "failed"
        log.warning("wallpaper keyboard focus failed — showing the window with the same field")
        self.show_field(fid, hash_)
        return "window"

    def show_field(self, fid, hash_):
        if not self.win:
            return
        self.show()
        if self.focus_lock.acquire(timeout=2):
            try:
                WPM.activate(find_own(TITLE))
            finally:
                self.focus_lock.release()
        js = ("(()=>{const h=%s,f=%s;const typed=['atTitle','atWho','atDue'].some(i=>{const x=document.getElementById(i);return x&&x.value});"  # 
              "if(location.hash!==h&&!typed)location.hash=h;setTimeout(()=>{const e=f&&(document.querySelector("
              "'[data-fid=\"'+CSS.escape(f)+'\"]')||document.getElementById(f));if(e)e.focus()},400)})()" % (json.dumps(hash_), json.dumps(fid)))
        def run():
            try:
                self.win.evaluate_js(js)
            except Exception as e:  # окно закрыли или страница не готова — причина в журнал, набор уже не потерян (окно показано)
                log.warning("show field: %s", e)
        threading.Thread(target=run, name="show-field", daemon=True).start()

    def wallpaper_release(self):
        """правка на обоях закончена (Enter/Esc) — клавиатура обратно окну, которое было активно до щелчка по обоям."""
        if not self.focus_lock.acquire(timeout=2):
            return False  # замок занят — окно для возврата не забываем, его вернёт следующий Enter/Esc
        try:
            prev, self.wp_prev = self.wp_prev, None
            pin = self._wp_pin
            if not (pin and pin[0] and pin[1] and prev):
                return False
            ok = WPM.activate(prev, only_from=pin[1])
            if not ok and self.wp_prev is None:  # возврат не удался — окно для возврата не теряем
                self.wp_prev = prev
            return ok
        finally:
            self.focus_lock.release()

    def pomodoro_alert(self, text, sound=True, still=None, title="Время вышло"):
        """конец отрезка таймера — сигнал подаёт программа (один раз), а не окна. звук три раза (если не выключен
        в настройках таймера); тост Windows «Время вышло», который держится до «Закрыть» (виден и при свёрнутом окне, окне в трее,
        на обоях); не показался — уведомление трея и повтор каждые 2 минуты, пока «Время вышло» не подтверждено (still), не больше 3 раз;
        кнопка «Мой день» на панели задач мигает до открытия окна (видна и при «Не беспокоить»); каждая подача — строкой в журнал."""
        if sound:
            threading.Thread(target=self._pomo_sound, name="pomo-sound", daemon=True).start()
        toast = toast_run(title, text)
        ok = toast == "ok"
        self.toast_shown = ok
        if ok and still and not still():  # подтвердили, пока PowerShell показывал тост, — убрать его сразу
            self.pomodoro_clear()
        tray = "не нужно (тост)" if ok else self._tray(text)
        flash = self._flash() if not (still and not still()) else "не нужно (уже подтверждено)"  # подтвердили во время тоста
        if still and not still() and getattr(self, "flash_shown", False):  # подтвердили между проверкой и показом — окно обратно в трей
            flash += "; " + str(self.flash_restore())
        log.info("pomodoro alert: %s | тост: %s | уведомление трея: %s | мигание: %s | звук: %s", text.replace("\n", " / ")[:160], toast, tray, flash,
                 "3 раза" if sound else "выключен")
        if not ok and still:
            threading.Thread(target=self._repeat, args=(text, still), name="pomo-repeat", daemon=True).start()

    def _repeat(self, text, still):
        for i in range(self.repeat_times):
            if self.stop_ev.wait(self.repeat_every):
                return
            if not still():
                log.info("pomodoro repeat: «Время вышло» подтверждено — повтора нет")
                return
            log.info("pomodoro repeat %s из %s: уведомление трея: %s | мигание: %s", i + 1, self.repeat_times, self._tray(text), self._flash())
            if not still():  # подтвердили во время повтора — окно обратно в трей
                self.flash_restore()

    def pomodoro_clear(self):
        """«Время вышло» подтвердили в окне или на обоях — тост убрать (иначе висит до «Закрыть»)."""
        if not self.toast_shown:
            return None
        self.toast_shown = False
        r = toast_run(mode="remove")
        log.info("pomodoro toast removed: %s", r)
        return r

    def _pomo_sound(self, times=3, gap=0.9):
        ok = 0
        for i in range(times):
            try:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)  # синхронно: повторы не перебивают друг друга
                ok += 1
            except RuntimeError as e:
                log.warning("pomodoro sound: %s", e)
            if i < times - 1:
                time.sleep(gap)
        log.info("pomodoro sound: %s из %s", ok, times)

    def _flash(self):
        """FlashWindowEx: FLASHW_ALL | FLASHW_TIMERNOFG — мигает, пока окно не станет активным. у окна в трее (закрыто
        крестиком или запуск --hidden) кнопки на панели нет и мигать нечему — окно показывается свёрнутым без активации (SW_SHOWMINNOACTIVE:
        фокус не забирает, поверх обоев и окон не выходит, кнопка — та же, закреплённая); подтверждение «Время вышло» прячет его обратно
        (flash_restore). В журнал — что было на самом деле."""
        try:
            h = find_own(TITLE)
            if not h:
                return "окна нет"
            how = ""
            if not u32.IsWindowVisible(h):
                u32.ShowWindow(h, 7)  # SW_SHOWMINNOACTIVE
                if not u32.IsWindowVisible(h):
                    return "окно в трее, показать свёрнутым не вышло — кнопки нет, мигать нечему"
                self.flash_shown = True
                how = " (окно было в трее — показано свёрнутым без активации)"
            fi = FLASHWINFO(cbSize=ctypes.sizeof(FLASHWINFO), hwnd=h, dwFlags=0x3 | 0xC, uCount=0, dwTimeout=0)
            u32.FlashWindowEx(ctypes.byref(fi))
            return "ok" + how
        except Exception as e:  # мигание — подсказка, не повод обрывать сигнал
            log.warning("pomodoro flash: %s", e)
            return f"ошибка: {e}"

    def flash_restore(self):
        """«Время вышло» подтверждено — окно, показанное свёрнутым только ради мигания, снова уходит в трей, если пользователь
        его так и не открыл (свёрнуто и не активно); открытое окно не трогаем. Результат — в журнал."""
        if not getattr(self, "flash_shown", False):
            return None
        self.flash_shown = False
        try:
            h = find_own(TITLE)
            if not h or not u32.IsWindowVisible(h):
                r = "окна нет или оно уже скрыто"
            elif u32.IsIconic(h) and u32.GetForegroundWindow() != h:
                fi = FLASHWINFO(cbSize=ctypes.sizeof(FLASHWINFO), hwnd=h, dwFlags=0, uCount=0, dwTimeout=0)  # FLASHW_STOP
                u32.FlashWindowEx(ctypes.byref(fi))
                u32.ShowWindow(h, 0)  # SW_HIDE — обратно в трей, как было до сигнала
                r = "окно снова в трее"
            else:
                r = "окно открыто пользователем — не трогаю"
        except Exception as e:  # возврат в трей — удобство, не повод обрывать команду таймера
            r = f"ошибка: {e}"
        log.info("pomodoro flash restore: %s", r)
        return r

    def pin_hint(self):
        """подсказка «закрепите в панели задач» — один раз и только если программа ещё не закреплена (закреплять самим
        нечем: в Windows 11 нет разрешённого способа, а недокументированные и политики не применяем)."""
        if not FROZEN or not self.icon or load_state().get("pinHint"):
            return False
        try:
            pinned = WPM.taskbar_pinned(sys.executable)
        except Exception as e:  # не прочитали закреплённые ярлыки — подсказка лишней не будет
            log.warning("taskbar pinned check: %s", e)
            pinned = False
        save_state(pinHint=1)
        if pinned:
            log.info("taskbar: already pinned, no hint")
            return False
        self._tray(PIN_HINT)
        return True

    def wallpaper_off(self):
        if self.wp:
            wp, self.wp, self.wp_hwnd = self.wp, None, None
            wp.destroy()
            WPM.refresh_desktop()

    def toggle_wallpaper(self, *_):
        self.wallpaper_off() if self.wp else self.wallpaper_on()

    def about(self, *_):
        threading.Thread(target=lambda: u32.MessageBoxW(0, f"«{TITLE}», версия {VERSION}\n© 2026 Ulugbek Makhmudov, лицензия MIT\n\n"
                                                           f"Папка данных:\n{DATA}\n\nПрограмма:\n{BASE}", "О программе «Мой день»", 0x40),
                         daemon=True).start()

    def quit(self, *_):
        self.quitting = True
        self.stop_ev.set()
        self.wallpaper_off()
        if self.icon:
            self.icon.stop()
        threading.Thread(target=self.srv.shutdown, daemon=True).start()
        if self.win:
            self.win.destroy()


def tray(ui):
    import pystray
    from PIL import Image
    ico = Path(S.PAGE_DIR) / "мой_день.ico"
    img = Image.open(ico if ico.exists() else BASE / "мой_день.ico")
    menu = pystray.Menu(
        pystray.MenuItem("Открыть «Мой день»", lambda *_: ui.show(), default=True),
        pystray.MenuItem("Показывать как обои рабочего стола", ui.toggle_wallpaper, checked=lambda item: ui.wp is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("О программе", ui.about),
        pystray.MenuItem("Выйти", ui.quit))
    ui.icon = pystray.Icon("moy-den", img, TITLE, menu)
    ui.icon.run_detached()


def main(argv):
    if AUMID:  # из исходников — своя кнопка на панели задач; собранная программа — ID из пути exe (см. aumid_for)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    k32.CreateMutexW(None, False, "Local\\MoyDen.App" + INSTANCE)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: приложение уже запущено
        u32.AllowSetForegroundWindow(-1)  # уже открытое окно может выйти на передний план
        call("api/app/show")
        return 0
    st = call("api/ping")
    if st and st.get("app") == S.APP and not st.get("ui"):
        call("api/app/quit")  # сервер без окна (поднял клиент API) — приложение забирает работу себе
        for _ in range(50):
            if not port_busy():
                break
            time.sleep(0.1)
    if port_busy():
        message(f"«Мой день» не может запуститься: мешает другая программа на компьютере.\n"
                f"Закройте программу, которая занимает порт {PORT} на этом компьютере, или перезагрузите компьютер.")
        return 1
    app, srv = start_server()
    app.aumid = explicit_aumid()  # для проверки: у собранной программы — None (ID выводит Windows из пути exe)
    if FROZEN:  # имя и значок уведомлений «Время вышло»; сбой — в журнал, программа работает (сработает уведомление трея)
        try:
            log.info("toast id %s: %s", TOAST_ID, toast_register())
        except Exception as e:
            log.warning("toast id %s not registered: %s", TOAST_ID, e)
    ui = UI(srv)
    app.ui = ui
    import webview
    s = load_state()
    w, h, x, y = s.get("width", 1600), s.get("height", 900), s.get("x"), s.get("y")
    if not (isinstance(w, int) and 1100 <= w <= 10000 and isinstance(h, int) and 700 <= h <= 10000):
        w, h = 1600, 900
    if x is not None and not on_screen(x, y, w):
        log.info("saved window position off-screen (%s, %s) — window centered", x, y)
        x = y = None
    ui.win = webview.create_window(TITLE, URL, width=w, height=h, x=x, y=y,
                                   min_size=(1100, 700), background_color="#F3F0EA", hidden="--hidden" in argv)
    ui.win.events.closing += ui.on_closing
    ui.win.events.restored += ui.on_restored  # развернули после сворачивания (обычное окно)
    ui.win.events.maximized += ui.on_restored  # развернули после сворачивания (окно было развёрнуто на весь экран)
    ui.win.events.minimized += ui.on_minimized  # свёрнутое окно с панели задач Windows восстанавливает само — сброс при сворачивании
    ui.win.events.moved += lambda x, y: save_pos(x, y)
    ui.win.events.resized += lambda width, height: save_size(width, height)
    tray(ui)

    def after_start():
        wp = settings().get("wallpaper")
        if isinstance(wp, dict) and wp.get("window") is True:
            ui.wallpaper_on()
        if "--hidden" not in argv:  # окно на экране — значок в панели задач есть, подсказка к месту
            t = threading.Timer(8, ui.pin_hint)
            t.daemon = True
            t.start()
    webview.start(after_start, gui="edgechromium", private_mode=False, storage_path=str(DATA / "профиль-окна"))
    ui.quitting = True
    if ui.icon:
        ui.icon.stop()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        log.exception("app crashed")
        message("«Мой день» не запустился. Подробности записаны в файл server.log в папке\n" + str(DATA))
        sys.exit(1)
