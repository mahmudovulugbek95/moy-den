"""Локальный сервер дашборда «Мой день».

Слушает только 127.0.0.1. Отдаёт страницу, JSON API задач и проектов (данные в data.json),
события календарей из секретных адресов iCal (calendars.local.json) и погоду (Open-Meteo, settings.json).
Обычно его запускает программа (app.py); можно и отдельно: python server.py. Второй экземпляр на тот же порт не стартует.
"""
import argparse
import hashlib
import html
import json
import math
import logging
import os
from stat import S_ISDIR, S_ISREG  # причина 409 замка
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone, tzinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
PAGE_DIR = None  # папка страницы и значка; установленная программа ставит СВОЮ папку (app.py), а не папку данных
APP = "moy-den-app"  # метка в /api/ping: у другой сборки «Мой день» своя, чужой сервер не трогаем
DEFAULT_PORT = 8648


class LocalZone(tzinfo):
    """Часовой пояс этого компьютера, с переходом на летнее время, если он есть (по образцу документации Python, модуль datetime)."""

    def _offsets(self):
        std = timedelta(seconds=-time.timezone)
        return std, (timedelta(seconds=-time.altzone) if time.daylight else std)

    def _isdst(self, dt):
        tt = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.weekday(), 0, -1)
        try:
            return time.localtime(time.mktime(tt)).tm_isdst > 0
        except (OverflowError, ValueError, OSError):  # дата вне диапазона часов Windows — без летнего времени
            return False

    def utcoffset(self, dt):
        std, dst = self._offsets()
        return dst if dt is not None and self._isdst(dt) else std

    def dst(self, dt):
        std, dst = self._offsets()
        return dst - std if dt is not None and self._isdst(dt) else timedelta(0)

    def tzname(self, dt):
        return time.tzname[1 if dt is not None and self._isdst(dt) else 0]

    def fromutc(self, dt):
        stamp = (dt.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds()
        try:
            lt = time.localtime(stamp)
        except (OverflowError, ValueError, OSError):
            return (dt + self._offsets()[0]).replace(tzinfo=self)
        return datetime(*lt[:6], microsecond=dt.microsecond, tzinfo=self)

    def __repr__(self):
        return "LocalZone()"


def _zone():
    """Пояс программы: MOYDEN_TZ (имя IANA, например Europe/Berlin — для проверок и особых случаев) или пояс компьютера."""
    name = os.environ.get("MOYDEN_TZ", "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name), name
        except Exception:  # неизвестное имя — работаем по поясу компьютера
            pass
    return LocalZone(), ""


TZ, TZ_NAME = _zone()
MAX_BODY = 64 * 1024
MAX_RESTORE = 4 * 1024 * 1024  # «Вернуть» задачу с длинной лентой комментариев (до 500 по 2000 знаков)
LINGER_SEC, LINGER_MAX = 2.0, 64 * 1024 * 1024  # «мягкое» закрытие после ошибки — дочитать тело не дольше и не больше


def drain_after_error(sock):
    """ответ с ошибкой уже отправлен, а тело запроса не прочитано (413, 403, неверная длина). Сначала FIN —
    клиент дочитает ответ до конца; затем остаток тела дочитывается и выбрасывается, пока клиент не закроет свою сторону, но не дольше
    LINGER_SEC и не больше LINGER_MAX. Иначе Windows закрывает сокет с непрочитанными данными сбросом (RST) и ответ иногда теряется."""
    try:
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        return 0
    end, got = time.monotonic() + LINGER_SEC, 0
    while got < LINGER_MAX:
        left = end - time.monotonic()
        if left <= 0:
            break
        try:
            sock.settimeout(min(left, 0.5))
            chunk = sock.recv(65536)
        except socket.timeout:  # клиент молчит — ждём дальше, но не дольше LINGER_SEC 
            continue
        except OSError:  # клиент оборвал соединение сам — дочитывать нечего
            break
        if not chunk:
            break
        got += len(chunk)
    return got
MAX_ICS = 20 * 1024 * 1024
KEEP_DAILY = 14
SCHEMA = 3  # plan, dueHistory, doneLate у задач
STATES = {"work", "wait", "done"}
STATUSES = {"ok", "risk", "late"}  # пауза — отдельное поле pause, а не статус
# причины переноса срока (окно «Почему переносим?» и клиент API через dueReason)
DUE_REASONS = {"staff": "сотрудник не успел", "client": "ждём данные или решение заказчика", "scope": "заказчик изменил задание",
               "subs": "ждём смежников", "priority": "сменился приоритет", "other": "другое"}
PALETTE = ["#4a6cf7", "#0b8f7f", "#b0568a", "#c07a12", "#5b6b7f", "#7a5cc7"]
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CITIES = []  # город для погоды пользователь задаёт сам (settings.json → weather.cities); без города погода не показывается
DEFAULT_CALENDARS = ("Рабочий", "Личный")  # места под календари до первого подключения (calendars.local.json ещё нет)

log = logging.getLogger(APP)


def today():
    return datetime.now(TZ).date().isoformat()


def etag_of(raw: bytes) -> str:
    return '"' + hashlib.sha1(raw).hexdigest()[:20] + '"'


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- данные: data.json ----------

def replace_with_retry(src: Path, dst: Path):
    # Windows: замена падает, если файл в этот миг открыт антивирусом или редактором
    for attempt in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def encode(doc) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def parse_doc(raw: bytes, name: str):
    hint = f"Исправьте его или верните копию: {name}.bak (до последней правки) или дневной резерв из папки «резерв»"
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ApiError(500, f"Файл {name} повреждён (строка {getattr(e, 'lineno', '?')}). {hint}")
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), list) for k in ("projects", "tasks", "employees")):
        raise ApiError(500, f"В {name} нет списков projects, tasks, employees. {hint}")
    return doc


def empty_doc():
    """Данные нового пользователя: пусто — ни задач, ни проектов, ни сотрудников."""
    return {"schema": SCHEMA, "projects": [], "tasks": [], "employees": [], "reminders": [], "eventLinks": {},
            "nextId": 1, "nextEmployeeId": 1, "updated": today()}


def ensure_data(path: Path):
    """Первый запуск: data.json нет — создаётся пустой. Существующий файл не трогается. True — файл создан."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # создаёт только тот, кто первым; чужой файл не затирается
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as f:
        f.write(encode(empty_doc()))
    log.info("data: new empty %s", path.name)
    return True


def migrate(doc) -> bool:
    """Схема 2: пауза — поле pause (а не status "pause"), счётчик номеров nextId. Ничего не удаляет."""
    changed = False
    for p in doc["projects"]:
        if isinstance(p, dict) and p.get("status") == "pause":
            p["status"] = "ok"
            if not isinstance(p.get("pause"), dict):
                p["pause"] = {"since": today(), "reason": "", "until": ""}
            changed = True
    need = max((t["id"] for t in doc["tasks"] if isinstance(t, dict) and is_int(t.get("id"))), default=0) + 1
    if not is_int(doc.get("nextId")) or doc["nextId"] < need:
        doc["nextId"] = need  # номера задач не переиспользуются: клиент API, помнящий «задачу 92», не закроет чужую
        changed = True
    emps = [e for e in doc["employees"] if isinstance(e, dict)]
    used = {e["id"] for e in emps if is_int(e.get("id"))}
    nxt = max(doc.get("nextEmployeeId") if is_int(doc.get("nextEmployeeId")) else 0, max(used, default=0) + 1)
    for e in emps:
        if not is_int(e.get("id")):
            e["id"] = nxt
            nxt += 1
            changed = True
        if "firstName" not in e:  # было {name, role}: имя переносится, фамилию пользователь впишет
            e["firstName"] = str(e.get("name") or "").strip()
            e["lastName"] = ""
            changed = True
        if e.get("name") != full_name(e):
            e["name"] = full_name(e)
            changed = True
    if doc.get("nextEmployeeId") != nxt:
        doc["nextEmployeeId"] = nxt
        changed = True
    for t in doc["tasks"]:
        if isinstance(t, dict) and t.get("who") and not is_int(t.get("whoId")):
            e = resolve_employee(doc, t["who"])
            if e:
                t["whoId"] = e["id"]
                changed = True
    for t in doc["tasks"]:  # первый срок — плановый; у старых задач — текущий срок на момент перехода на схему 3
        if not isinstance(t, dict):
            continue
        if "plan" not in t and is_day(t.get("due")):
            t["plan"] = t["due"]
            changed = True
        if t.get("state") != "done":  # L5: откат на старую версию вернул задачу в работу, а поля выполнения остались
            for k in ("doneFrom", "doneLate"):
                if k in t:
                    t.pop(k)
                    changed = True
        elif t.get("doneLate") != done_late(t):  # выполнена до схемы 3 или старой версией после отката — всегда по плану и дате
            if done_late(t) is None:
                t.pop("doneLate", None)
            else:
                t["doneLate"] = done_late(t)
            changed = True
    if doc.get("schema") != SCHEMA:
        doc["schema"] = SCHEMA
        changed = True
    return changed


def is_day(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def done_late(task):
    """на сколько дней позже планового срока выполнена задача (0 — вовремя); нет плана или даты — None."""
    if not (is_day(task.get("plan")) and is_day(task.get("done"))):
        return None
    return max(0, (date.fromisoformat(task["done"]) - date.fromisoformat(task["plan"])).days)


def mark_done(task, day):
    task["done"] = day
    n = done_late(task)
    if n is None:
        task.pop("doneLate", None)
    else:
        task["doneLate"] = n


def is_postpone(h):
    """Запись истории — перенос (на более поздний срок или снятие срока), а не приближение срока."""
    return isinstance(h, dict) and bool(h.get("from")) and (not h.get("to") or str(h.get("to")) > str(h.get("from")))


def v_due_reason(v, required, removing):
    if v in (None, "", {}):
        if required:
            what = "Срок снимается" if removing else "Срок переносится на более позднюю дату"
            raise ApiError(400, f"{what} — укажите причину переноса (dueReason): code — "
                                + ", ".join(f"{k} ({t})" for k, t in DUE_REASONS.items()) + "; для other нужен text")
        return None, ""
    if isinstance(v, str):
        v = {"code": v}
    if not isinstance(v, dict) or not isinstance(v.get("code"), str) or v["code"] not in DUE_REASONS:  # список/объект — 400, а не 500
        raise ApiError(400, "Причина переноса (dueReason.code): одна из " + ", ".join(DUE_REASONS))
    text = v_text(v.get("text"), "dueReason.text", 200)
    if v["code"] == "other" and not text:
        raise ApiError(400, "Причина переноса «другое»: напишите, почему переносим")
    return v["code"], text


def v_due_history(items):
    """«Вернуть» удалённую задачу: история переносов проверяется по записи; битая запись пропускается."""
    out = []
    for h in items[-100:]:
        if not isinstance(h, dict):
            continue
        try:
            e = {"from": v_date(h.get("from"), "from"), "to": v_date(h.get("to"), "to"), "at": v_stamp(h.get("at"), "at")}
            if isinstance(h.get("reason"), str) and h["reason"] in DUE_REASONS:
                e["reason"] = h["reason"]
            text = v_text(h.get("text"), "dueReason.text", 200)
        except ApiError:
            continue
        if text:
            e["text"] = text
        if isinstance(h.get("late"), bool):
            e["late"] = h["late"]
        out.append(e)
    return out


def full_name(e):
    return " ".join(x for x in (str(e.get("firstName") or "").strip(), str(e.get("lastName") or "").strip()) if x) or str(e.get("name") or "")


def resolve_employee(doc, who):
    """Имя из текста клиента API → сотрудник: полное имя или единственное совпадение по имени."""
    w = norm(who)
    if not w:
        return None
    emps = [e for e in doc["employees"] if isinstance(e, dict)]
    exact = [e for e in emps if norm(full_name(e)) == w]
    if len(exact) == 1:
        return exact[0]
    first = [e for e in emps if not e.get("archived") and norm(e.get("firstName")) == w]
    return first[0] if len(first) == 1 else None


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raise ApiError(500, f"Файл данных {self.path.name} не найден. Верните его из {self.path.name}.bak или из папки «резерв»")
        doc = parse_doc(raw, self.path.name)
        migrate(doc)  # клиент API мог записать файл в старом виде
        return doc, raw

    @contextmanager
    def file_lock(self, wait=3.0):
        """Замок data.json.lock на миг замены файла: клиент API, который пишет файл напрямую, может брать тот же замок (README)."""
        lock = self.path.with_name(self.path.name + ".lock")
        t0 = time.monotonic()
        stuck, saw_file = None, False  # последняя ошибка удаления старого замка; был ли файл замка хоть раз
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError) as e:  # Windows — снятый замок ещё «удаляется» → PermissionError, ждём как занятый
                if isinstance(e, FileExistsError):
                    saw_file = True
                    try:
                        age = time.time() - lock.stat().st_mtime
                    except OSError:  # исчез или недоступен — ждём как занятый (без continue: битая ссылка не крутит цикл вечно)
                        age = None
                    if age is not None and (age > 10 or age < -60):  # забыт упавшей программой; дата из будущего (переводили часы) — тоже старый
                        try:
                            lock.unlink(missing_ok=True)
                            continue
                        except OSError as ue:  # «только чтение» или открыт без FILE_SHARE_DELETE — не крутимся вечно, ждём срок
                            stuck = ue
                    else:
                        stuck = None
                if time.monotonic() - t0 > wait:
                    raise ApiError(409, self._lock_reason(lock, e, stuck, saw_file))
                time.sleep(0.05)
        mark = f"{os.getpid()}-{time.time_ns()}".encode()
        try:
            os.write(fd, mark)
            os.close(fd)
            yield
        finally:
            try:
                if lock.read_bytes() == mark:  # чужой замок не удаляем
                    lock.unlink()
            except OSError:
                pass

    @staticmethod
    def _lock_reason(lock, err, stuck, saw_file=False):
        """текст 409 по настоящей причине (замок) — с путём к замку; причина и в журнал."""
        try:
            mode = os.stat(lock).st_mode
        except FileNotFoundError:
            mode = None
        except OSError:  # есть, но недоступен: другая программа его как раз удаляет — это «занято», не «нет доступа»
            mode = 0
        if stuck is not None and mode and S_ISREG(mode):
            msg = (f"Старый замок файла данных не удаётся удалить: {lock}. Удалите этот файл вручную "
                   f"(если стоит атрибут «только чтение» — снимите его) и повторите")
        elif mode and S_ISDIR(mode):
            msg = f"На месте замка файла данных — папка: {lock}. Переименуйте или удалите её и повторите"
        elif isinstance(err, PermissionError) and mode is None and not saw_file:  # ни разу не было файла — значит, нет права создать
            msg = f"Нет доступа к папке данных: не удаётся создать замок {lock}. Проверьте права на папку {lock.parent}"
        else:
            msg = f"Файл данных сейчас меняет другая программа (замок {lock}). Повторите через секунду"
        log.warning("data lock 409: %s (%s; stale: %s)", msg, type(err).__name__, type(stuck).__name__ if stuck else "-")
        return msg

    def _write(self, data: bytes, expect=None):
        """Запись через .tmp. expect — содержимое, прочитанное до правки: сверяемся с ним прямо перед заменой
        (клиент API мог записать файл, пока шла правка). Возвращает False, если файл изменили извне."""
        p = self.path
        check = expect is not None
        if not check:
            try:
                expect = p.read_bytes()
            except FileNotFoundError:
                expect = None
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if expect is not None:
            bak_tmp = p.with_name(p.name + ".bak.tmp")
            bak_tmp.write_bytes(expect)
            replace_with_retry(bak_tmp, p.with_name(p.name + ".bak"))
        try:
            with self.file_lock():
                if check:
                    try:
                        current = p.read_bytes()
                    except FileNotFoundError:
                        current = None
                    if current != expect:
                        tmp.unlink(missing_ok=True)
                        return False
                replace_with_retry(tmp, p)
        except BaseException:  # замок занят (409) или замена не удалась — недописанная копия не остаётся
            tmp.unlink(missing_ok=True)
            raise
        if expect is not None:
            self._daily_snapshot(expect)
        return True

    def save(self, doc):
        doc["updated"] = today()
        data = encode(doc)
        self._write(data)
        return data

    def _daily_snapshot(self, prev: bytes):
        folder = self.path.parent / "резерв"
        snap = folder / f"{self.path.stem}-{today()}.json"
        if snap.exists():
            return
        folder.mkdir(exist_ok=True)
        snap.write_bytes(prev)  # состояние на начало дня, до первой правки
        old = sorted(folder.glob(f"{self.path.stem}-*.json"))[:-KEEP_DAILY]
        for f in old:
            f.unlink(missing_ok=True)

    def mutate(self, fn):
        with self.lock:
            for attempt in range(8):
                try:
                    doc, raw = self.load()
                except ApiError:
                    if attempt == 7:
                        raise
                    time.sleep(0.1)  # клиент API пишет файл прямо сейчас — недописанный файл читаем ещё раз
                    continue
                item = fn(doc)
                doc["updated"] = today()
                data = encode(doc)
                if self._write(data, expect=raw):
                    return doc, etag_of(data), item
                # файл изменили извне (клиент API) между чтением и записью — применяем правку заново к свежим данным
                log.info("data file changed during write, retry %s", attempt + 1)
            raise ApiError(409, "Файл данных сейчас меняет другая программа. Повторите через секунду")

    def migrate_file(self):
        with self.lock:
            raw = self.path.read_bytes()
            doc = parse_doc(raw, self.path.name)
            if not migrate(doc):
                return False
            folder = self.path.parent / "резерв"
            folder.mkdir(exist_ok=True)
            backup = folder / f"{self.path.name}.до-схемы-{SCHEMA}-{today()}"
            if backup.exists():  # вторая миграция за день — отдельная копия со временем, первая не затирается
                backup = folder / f"{self.path.name}.до-схемы-{SCHEMA}-{today()}-{datetime.now(TZ).strftime('%H%M%S')}"
            backup.write_bytes(raw)
            doc["updated"] = today()
            if not self._write(encode(doc), expect=raw):  # R6: клиент API записал файл во время миграции — его запись не затираем
                raise ApiError(409, "Файл данных изменился во время обновления схемы — обновление при следующем запуске")
            log.info("data migrated to schema %s, backup %s", SCHEMA, backup.name)
            return True


# подписи полей: сообщение об ошибке видит и пользователь, поэтому по-русски; имя поля — в скобках для клиента API
LABELS = {"title": "Название задачи", "name": "Название проекта", "stage": "Этап", "owner": "Ответственный", "note": "Заметка",
          "who": "Исполнитель", "due": "Срок", "deadline": "Срок проекта", "text": "Текст", "firstName": "Имя", "lastName": "Фамилия",
          "role": "Должность", "middleName": "Отчество", "office": "Офис", "aliases": "Синонимы", "pause.reason": "Причина паузы",
          "pause.until": "Дата «вернуть к работе до»", "pause.since": "Дата начала паузы", "contacts.phone": "Телефон",
          "contacts.telegram": "Telegram", "contacts.email": "Почта", "date": "Дата", "uid": "Событие", "done": "Дата выполнения",
          "from": "Начало паузы", "to": "Конец паузы", "reason": "Причина паузы", "at": "Время комментария",
          "milestones.title": "Название этапа", "milestones.date": "Дата этапа",
          "calendar": "Календарь", "url": "Адрес календаря", "doneAt": "Дата завершения", "doneNote": "Итог проекта",
          "dueReason.text": "Причина переноса", "plan": "Плановый срок"}


def label(field):
    return f"{LABELS[field]} ({field})" if field in LABELS else f"Поле {field}"


def v_text(v, field, maxlen, required=False):
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(400, f"{label(field)}: нужен текст")
    v = v.strip()
    if required and not v:
        raise ApiError(400, f"{label(field)}: пусто, напишите текст")
    if len(v) > maxlen:
        raise ApiError(400, f"{label(field)}: не длиннее {maxlen} символов")
    return v


def v_date(v, field):
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ApiError(400, f"{label(field)}: дата в виде ГГГГ-ММ-ДД")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"{label(field)}: такой даты нет")
    return v


def v_time(v):
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ApiError(400, "Время (time): в виде ЧЧ:ММ, например 15:30")
    return v


def now_min():
    return datetime.now(TZ).isoformat(timespec="minutes")[:16]  # «ГГГГ-ММ-ДДTЧЧ:ММ», местное время


def v_stamp(v, field):
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", v[:16]):
        try:
            datetime.fromisoformat(v[:16])
            return v[:16]
        except ValueError:
            pass
    raise ApiError(400, f"{label(field)}: дата и время в виде ГГГГ-ММ-ДДTЧЧ:ММ")


def norm(s: str) -> str:
    s = str(s or "").casefold().replace("ё", "е")
    return re.sub(r"[\s\-–—_/]+", " ", s).strip()


def v_aliases(v):
    if isinstance(v, str):
        v = v.split(",")
    if v is None:
        v = []
    if not isinstance(v, list):
        raise ApiError(400, "Синонимы (aliases): список слов или текст через запятую")
    out, seen = [], set()
    for a in v:
        a = v_text(a, "aliases", 60)
        if a and norm(a) not in seen:
            seen.add(norm(a))
            out.append(a)
    if len(out) > 12:
        raise ApiError(400, "Синонимы (aliases): не больше двенадцати")
    return out


def apply_pause(obj, v):
    """pause: {since, reason, until}; null/false — снять паузу (период уходит в pauseHistory)."""
    if v is None or v is False:
        cur = obj.pop("pause", None)
        if isinstance(cur, dict):
            hist = obj.get("pauseHistory") if isinstance(obj.get("pauseHistory"), list) else []
            hist.append({"from": cur.get("since") or today(), "to": today(), "reason": cur.get("reason", "")})
            obj["pauseHistory"] = hist[-50:]
        return
    if v is True:
        v = {}
    if not isinstance(v, dict):
        raise ApiError(400, "Пауза (pause): объект {reason, until} или null, чтобы снять паузу")
    cur = dict(obj["pause"]) if isinstance(obj.get("pause"), dict) else {"since": today(), "reason": "", "until": ""}
    if "since" in v:
        cur["since"] = v_date(v.get("since"), "pause.since") or today()
    if "reason" in v:
        cur["reason"] = v_text(v.get("reason"), "pause.reason", 200)
    if "until" in v:
        cur["until"] = v_date(v.get("until"), "pause.until")
    if cur.get("until") and cur["until"] < cur["since"]:
        raise ApiError(400, "Дата «вернуть к работе до» не может быть раньше начала паузы")
    obj["pause"] = cur


NOT_FOUND = {"Проект": "Проект не найден", "Задача": "Задача не найдена", "Сотрудник": "Сотрудник не найден",
             "Заметка": "Заметка не найдена", "Напоминание": "Напоминание не найдено", "Комментарий": "Комментарий не найден"}


def find(items, item_id, what):
    for it in items:
        if isinstance(it, dict) and str(it.get("id")) == str(item_id):  # мусор от клиента API (не объект) пропускается
            return it
    raise ApiError(404, f"{NOT_FOUND.get(what, what + ' не найден')}: {item_id}")


def apply_task_fields(doc, task, body, creating):
    if "title" in body or creating:
        task["title"] = v_text(body.get("title"), "title", 300, required=True)
    if "project" in body or creating:
        pid = body.get("project")
        find(doc["projects"], pid, "Проект")
        task["project"] = pid
    if "whoId" in body and body["whoId"] not in (None, ""):
        e = find(doc["employees"], body["whoId"], "Сотрудник")
        if e.get("archived"):
            raise ApiError(400, f"Сотрудник {full_name(e)} в архиве — сначала верните его")
        task["whoId"], task["who"] = e["id"], full_name(e)
    elif "who" in body or "whoId" in body or creating:
        who = v_text(body.get("who"), "who", 60)
        e = resolve_employee(doc, who)
        if e and e.get("archived"):
            raise ApiError(400, f"Сотрудник {full_name(e)} в архиве — сначала верните его")
        if e:
            task["whoId"], task["who"] = e["id"], full_name(e)
        else:
            task["who"] = who
            task.pop("whoId", None)
    if "due" in body or creating:  # перенос позже и снятие срока — только с причиной; всё — в историю
        new, old = v_date(body.get("due"), "due"), task.get("due") or ""
        if creating:
            if is_day(body.get("plan")):  # «Вернуть» удалённую задачу: её плановый срок и история возвращаются с ней
                task["plan"] = body["plan"]
            if isinstance(body.get("dueHistory"), list):
                hist = v_due_history(body["dueHistory"])
                if hist:
                    task["dueHistory"] = hist
        elif new != old and (old or "plan" in task):  # первый срок задачи — не перенос: он и есть плановый
            later = bool(old) and (not new or new > old)
            code, text = v_due_reason(body.get("dueReason"), later, not new)
            entry = {"from": old, "to": new, "at": now_min()}
            if code:
                entry["reason"] = code
                if text:
                    entry["text"] = text
            if old:
                entry["late"] = old < today()  # срок уже прошёл в момент переноса
            hist = task.get("dueHistory") if isinstance(task.get("dueHistory"), list) else []
            hist.append(entry)
            task["dueHistory"] = hist[-100:]
        task["due"] = new
        if new and "plan" not in task:
            task["plan"] = new
    if "time" in body:
        t = v_time(body.get("time"))
        if t:
            task["time"] = t
        else:
            task.pop("time", None)
    if not task.get("due"):
        task.pop("time", None)  # время без даты не имеет смысла
    if "state" in body or creating:
        state = body.get("state") or "work"
        if state not in STATES:
            raise ApiError(400, "Состояние (state): work — в работе, wait — ждём ответа, done — выполнено")
        was_done, was = task.get("state") == "done", task.get("state")
        task["state"] = state
        if state == "done" and not was_done:
            mark_done(task, v_date(body.get("done"), "done") or today())  # вовремя / с опозданием от плана
            if was == "wait" or (creating and body.get("doneFrom") == "wait"):  # «Вернуть» — в «ждём ответа»; L4: и после «Удалить → Вернуть»
                task["doneFrom"] = "wait"
            else:
                task.pop("doneFrom", None)
        elif state != "done":
            task.pop("done", None)
            task.pop("doneFrom", None)
            task.pop("doneLate", None)
    if "star" in body:
        if not isinstance(body["star"], bool):
            raise ApiError(400, "Звезда (star): true или false")
        if body["star"]:
            task["star"] = True
        else:
            task.pop("star", None)
    if "pause" in body:
        apply_pause(task, body["pause"])
    if creating and isinstance(body.get("pauseHistory"), list):  # «Вернуть» удалённую задачу вместе с историей
        task["pauseHistory"] = [{"from": v_date(h.get("from"), "from"), "to": v_date(h.get("to"), "to"),
                                 "reason": v_text(h.get("reason"), "reason", 200)}
                                for h in body["pauseHistory"][-50:] if isinstance(h, dict)]
    if creating and isinstance(body.get("comments"), list):  # «Вернуть» удалённую задачу вместе с комментариями
        restored, taken = [], set()
        for c in body["comments"][-500:]:
            if isinstance(c, dict):
                restored.append(restore_comment(doc, c, taken))
                taken.add(restored[-1]["id"])
        if restored:
            task["comments"] = restored
    if "example" in body:
        if body["example"]:
            task["example"] = True
        else:
            task.pop("example", None)


def create_task(doc, body):
    ids = {t["id"] for t in doc["tasks"] if isinstance(t, dict) and is_int(t.get("id"))}
    nid = max(doc.get("nextId") if is_int(doc.get("nextId")) else 0, max(ids, default=0) + 1)
    want = body.get("id")
    if is_int(want) and 0 < want < nid and want not in ids:
        new_id = want  # возврат удалённой задачи («Вернуть»): номер был выдан ей и больше никому
    else:
        new_id = nid
        nid += 1
    doc["nextId"] = nid
    task = {"id": new_id}
    apply_task_fields(doc, task, body, creating=True)
    doc["tasks"].append(task)
    return task


def patch_task(doc, task_id, body):
    task = find(doc["tasks"], task_id, "Задача")
    apply_task_fields(doc, task, body, creating=False)
    return task


def delete_task(doc, task_id):
    task = find(doc["tasks"], task_id, "Задача")
    doc["tasks"].remove(task)
    return task


def project_history(project, action, by, **extra):
    hist = project.get("history") if isinstance(project.get("history"), list) else []
    hist.append({"action": action, "at": now_min(), "by": by, **{k: v for k, v in extra.items() if v}})
    project["history"] = hist[-100:]


def finish_project(doc, project, body, by):
    """завершить проект — финал (пауза — временно). Открытые задачи: done | keep | move (moveTo)."""
    if project.get("status") == "done":  # уже завершён — можно поправить дату и итог
        if "doneAt" in body:
            project["doneAt"] = v_date(body.get("doneAt"), "doneAt") or project.get("doneAt") or today()
        if "doneNote" in body:
            note = v_text(body.get("doneNote"), "doneNote", 1000)
            project["doneNote"] = note
            if not note:
                project.pop("doneNote")
        return
    open_ = [t for t in doc["tasks"] if isinstance(t, dict) and str(t.get("project")) == str(project["id"]) and t.get("state") != "done"]
    how = body.get("openTasks")
    if open_ and how not in ("done", "keep", "move"):
        n = len(open_)
        raise ApiError(409, f"У проекта {n} {plural(n, 'открытая задача', 'открытые задачи', 'открытых задач')}. Укажите, что с ними сделать "
                            "(openTasks): done — отметить выполненными, keep — оставить как есть, move — перенести в другой проект (moveTo)")
    at = v_date(body.get("doneAt"), "doneAt") or today()
    note = v_text(body.get("doneNote"), "doneNote", 1000)
    if open_ and how == "move":
        target = find(doc["projects"], body.get("moveTo"), "Проект")
        if target is project or target.get("status") == "done" or isinstance(target.get("pause"), dict):
            raise ApiError(400, "Перенести задачи можно только в другой действующий проект (не завершённый и не на паузе)")
        for t in open_:
            t["project"] = target["id"]
    elif open_ and how == "done":
        # запоминаем, какие задачи закрыло завершение, — «Вернуть в работу» откроет их снова
        project["closedOnFinish"] = [{"id": t.get("id"), "state": t.get("state") if t.get("state") == "wait" else "work"} for t in open_]
        for t in open_:
            if t.get("state") == "wait":  # «Вернуть» одной задачи после «Завершить проект» — тоже «ждём ответа»
                t["doneFrom"] = "wait"
            t["state"] = "done"
            mark_done(t, at)
    if isinstance(project.get("pause"), dict):
        apply_pause(project, None)  # завершение снимает паузу, период уходит в историю пауз
    project["statusBefore"] = project.get("status") if project.get("status") in STATUSES else "ok"
    project["status"], project["doneAt"] = "done", at
    if note:
        project["doneNote"] = note
    project_history(project, "finished", by, date=at, note=note, tasks=how if open_ else "")


def reopen_tasks(doc, project):
    """задачи, которые «Завершить проект» отметил выполненными, снова открыты (если их с тех пор не открыли вручную)."""
    n = 0
    for c in project.get("closedOnFinish") if isinstance(project.get("closedOnFinish"), list) else []:
        if not isinstance(c, dict):
            continue
        t = next((x for x in doc["tasks"] if isinstance(x, dict) and x.get("id") == c.get("id")
                  and str(x.get("project")) == str(project["id"])), None)
        if t and t.get("state") == "done":
            t["state"] = "wait" if c.get("state") == "wait" else "work"
            t.pop("done", None)
            t.pop("doneFrom", None)
            t.pop("doneLate", None)
            n += 1
    return n


def plural(n, a, b, c):
    m, h = abs(n) % 10, abs(n) % 100
    return a if m == 1 and h != 11 else b if 2 <= m <= 4 and not 12 <= h <= 14 else c


def apply_project_fields(project, body, creating, doc=None, by="bot"):
    if "name" in body or creating:
        project["name"] = v_text(body.get("name"), "name", 80, required=True)
    for f, n in (("stage", 80), ("owner", 60), ("note", 300)):
        if f in body or creating:
            project[f] = v_text(body.get(f), f, n)
    if "deadline" in body or creating:
        project["deadline"] = v_date(body.get("deadline"), "deadline")
    if "status" in body or creating:
        status = body.get("status") or "ok"
        if status == "pause":  # старый способ (клиент API 22–23.10): «статус пауза» = поставить на паузу
            if project.get("status") == "done":
                raise ApiError(400, "Проект завершён — поставить его на паузу нельзя. Сначала верните его в работу")
            if creating:
                project["status"] = "ok"
            if not isinstance(project.get("pause"), dict):
                apply_pause(project, {})
        elif status == "done":
            if creating:
                raise ApiError(400, "Новый проект нельзя сразу создать завершённым")
            finish_project(doc, project, body, by)
        elif status not in STATUSES:
            raise ApiError(400, "Статус (status): ok — идёт по плану, risk — есть риск, late — срыв, done — завершить; паузу ставит поле pause")
        else:
            if project.get("status") == "done":  # вернуть в работу — только явно, а не сменой статуса
                if body.get("reopen") is not True:
                    raise ApiError(409, "Проект завершён. Чтобы вернуть его в работу, нажмите «Вернуть в работу» (для клиента API: reopen: true)")
                n = reopen_tasks(doc, project)
                project_history(project, "reopened", by, tasks=n)
                for k in ("doneAt", "doneNote", "statusBefore", "closedOnFinish"):
                    project.pop(k, None)
            project["status"] = status
    if "aliases" in body:
        al = v_aliases(body["aliases"])
        if al:
            project["aliases"] = al
        else:
            project.pop("aliases", None)
    if "milestones" in body:
        project_milestones(project, body["milestones"])
    if "pause" in body:
        if project.get("status") == "done" and body["pause"] not in (None, False):
            raise ApiError(400, "Проект завершён — поставить его на паузу нельзя. Сначала верните его в работу")
        apply_pause(project, body["pause"])


def project_milestones(project, ms):
    """Этапы и вехи проекта с датами: [{title, date, time?}] — список целиком; показываются в календаре."""
    if ms is None:
        ms = []
    if not isinstance(ms, list) or len(ms) > 50:
        raise ApiError(400, "Этапы (milestones): список до 50 этапов вида {title, date, time}")
    out = []
    for m in ms:
        if not isinstance(m, dict):
            raise ApiError(400, "Этапы (milestones): каждый этап — объект {title, date, time}")
        item = {"title": v_text(m.get("title"), "milestones.title", 120, required=True),
                "date": v_date(m.get("date"), "milestones.date")}
        if not item["date"]:
            raise ApiError(400, f"Дата этапа (milestones.date) обязательна: «{item['title']}»")
        t = v_time(m.get("time"))
        if t:
            item["time"] = t
        out.append(item)
    out.sort(key=lambda m: (m["date"], m.get("time", "")))
    if out:
        project["milestones"] = out
    else:
        project.pop("milestones", None)


def create_project(doc, body):
    used = {str(p.get("id")) for p in doc["projects"]}
    n = 1
    while f"p{n}" in used:
        n += 1
    project = {"id": f"p{n}"}
    apply_project_fields(project, body, creating=True, doc=doc)
    doc["projects"].append(project)
    return project


def patch_project(doc, project_id, body, by="bot"):
    project = find(doc["projects"], project_id, "Проект")
    apply_project_fields(project, body, creating=False, doc=doc, by=by)
    return project




def apply_employee_fields(doc, e, body, creating):
    if "firstName" in body or creating:
        e["firstName"] = v_text(body.get("firstName"), "firstName", 40, required=True)
    if "lastName" in body or creating:
        e["lastName"] = v_text(body.get("lastName"), "lastName", 40, required=creating)
    if "middleName" in body or creating:  # отчество — необязательное, пустое не хранится
        mid = v_text(body.get("middleName"), "middleName", 40)
        if mid:
            e["middleName"] = mid
        else:
            e.pop("middleName", None)
    if "role" in body or creating:
        e["role"] = v_text(body.get("role"), "role", 80)
    if "office" in body or creating:
        e["office"] = v_text(body.get("office"), "office", 40)  # офис или город — свободный текст
    if "contacts" in body:
        c = body["contacts"] if isinstance(body["contacts"], dict) else None
        if c is None:
            raise ApiError(400, "Контакты (contacts): объект {phone, telegram, email}")
        cur = dict(e.get("contacts") or {})
        for k, n in (("phone", 40), ("telegram", 60), ("email", 80)):
            if k in c:
                cur[k] = v_text(c.get(k), f"contacts.{k}", n)
        if cur.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cur["email"]):
            raise ApiError(400, "Почта: адрес вида имя@домен")
        e["contacts"] = {k: v for k, v in cur.items() if v}
    if "archived" in body:
        if not isinstance(body["archived"], bool):
            raise ApiError(400, "Архив (archived): true или false")
        if body["archived"] and open_tasks_of(doc, e):
            raise ApiError(409, "У сотрудника есть открытые задачи — удалите его через DELETE с полем reassign")
        e["archived"] = body["archived"]
    old = e.get("name")
    e["name"] = full_name(e)
    if old and old != e["name"]:
        for t in open_tasks_of(doc, e):  # выполненные хранят имя, каким оно было
            t["who"] = e["name"]


def open_tasks_of(doc, e):
    return [t for t in doc["tasks"] if isinstance(t, dict) and t.get("whoId") == e.get("id") and t.get("state") != "done"]


def create_employee(doc, body):
    nid = max(doc.get("nextEmployeeId") if is_int(doc.get("nextEmployeeId")) else 1,
              max((e["id"] for e in doc["employees"] if isinstance(e, dict) and is_int(e.get("id"))), default=0) + 1)
    e = {"id": nid}
    doc["nextEmployeeId"] = nid + 1
    apply_employee_fields(doc, e, body, creating=True)
    e["archived"] = False
    doc["employees"].append(e)
    return e


def patch_employee(doc, eid, body):
    e = find(doc["employees"], eid, "Сотрудник")
    apply_employee_fields(doc, e, body, creating=False)
    return e


def delete_employee(doc, eid, body):
    """Удаление = архив. Открытые задачи — переназначить (reassign=id) или оставить без исполнителя (reassign="none")."""
    e = find(doc["employees"], eid, "Сотрудник")
    tasks = open_tasks_of(doc, e)
    to = body.get("reassign")
    if tasks and to is None:
        raise ApiError(409, f"У сотрудника {full_name(e)} открытых задач: {len(tasks)}. Укажите reassign — id сотрудника или \"none\"")
    if tasks and to == "none":
        for t in tasks:
            t.pop("whoId", None)
            t["who"] = ""
    elif tasks:
        target = find(doc["employees"], to, "Сотрудник")
        if target is e or target.get("archived"):
            raise ApiError(400, "Переназначить можно только на другого действующего сотрудника")
        for t in tasks:
            t["whoId"], t["who"] = target["id"], full_name(target)
    e["archived"] = True
    return e


def note_op(doc, eid, nid, method, body):
    e = find(doc["employees"], eid, "Сотрудник")
    notes = [x for x in e["notes"] if isinstance(x, dict)] if isinstance(e.get("notes"), list) else []
    e["notes"] = notes
    if method == "POST":
        used = [x["id"] for emp in doc["employees"] if isinstance(emp, dict) and isinstance(emp.get("notes"), list)
                for x in emp["notes"] if isinstance(x, dict) and is_int(x.get("id"))]
        new_id = max(doc.get("nextNoteId") if is_int(doc.get("nextNoteId")) else 1, max(used, default=0) + 1)
        n = {"id": new_id, "date": datetime.now(TZ).isoformat(timespec="minutes"),
             "text": v_text(body.get("text"), "text", 2000, required=True)}
        doc["nextNoteId"] = new_id + 1  # номер удалённой заметки больше никому не достаётся
        notes.insert(0, n)
        return n
    n = find(notes, nid, "Заметка")
    if method == "PATCH":
        n["text"] = v_text(body.get("text"), "text", 2000, required=True)
        n["edited"] = datetime.now(TZ).isoformat(timespec="minutes")
        return n
    notes.remove(n)
    return n


AUTHORS = {"owner", "bot"}


def all_comment_ids(doc):
    return {c["id"] for t in doc["tasks"] if isinstance(t, dict) and isinstance(t.get("comments"), list)
            for c in t["comments"] if isinstance(c, dict) and is_int(c.get("id"))}


def next_comment_id(doc):
    return max(doc.get("nextCommentId") if is_int(doc.get("nextCommentId")) else 1, max(all_comment_ids(doc), default=0) + 1)


def restore_comment(doc, c, taken=()):
    """Комментарий из «Вернуть»: прежние номер, время и автор, если номер свободен; иначе — новый номер."""
    nid, used = next_comment_id(doc), all_comment_ids(doc) | set(taken)
    want = c.get("id")
    out = {"id": want if is_int(want) and 0 < want < nid and want not in used else nid,
           "text": v_text(c.get("text"), "text", 2000, required=True),
           "at": v_stamp(c.get("at"), "at") if c.get("at") else now_min(),
           "author": c.get("author") if c.get("author") in AUTHORS else "owner"}
    if c.get("edited"):
        out["edited"] = v_stamp(c["edited"], "at")
    if isinstance(c.get("clientId"), str) and 0 < len(c["clientId"]) <= 64:
        out["clientId"] = c["clientId"]  # возвращённый комментарий остаётся «той же записью» для поздней досылки
    doc["nextCommentId"] = max(nid, out["id"] + 1)
    return out


def comment_op(doc, tid, cid, method, body, from_page):
    """Комментарии к задаче: {id, text, at «ГГГГ-ММ-ДДTЧЧ:ММ», author "owner"|"bot", edited}. Номера не переиспользуются."""
    task = find(doc["tasks"], tid, "Задача")
    items = [c for c in task["comments"] if isinstance(c, dict)] if isinstance(task.get("comments"), list) else []
    task["comments"] = items
    try:
        if method == "POST":
            if "author" in body and body["author"] not in AUTHORS:
                raise ApiError(400, "Автор комментария (author): owner — пользователь или bot — бот")
            text = v_text(body.get("text"), "text", 2000, required=True)
            client = v_text(body.get("clientId"), "clientId", 64) if body.get("clientId") is not None else ""
            base = client.split(":", 1)[0]  # clientId страницы = «номер записи очереди:хэш текста»
            tombs = doc.get("commentTombstones") if isinstance(doc.get("commentTombstones"), list) else []
            live = next((x for x in items if base and str(x.get("clientId") or "").split(":", 1)[0] == base), None)
            if is_int(body.get("id")):  # «Вернуть» удалённый комментарий — надгробие снимается
                if live:  # повтор «Вернуть» (ответ потерялся) — не второй комментарий
                    return live
                c = restore_comment(doc, {**body, "text": text})
                if base:
                    doc["commentTombstones"] = [x for x in tombs if str(x).split(":", 1)[0] != base]
            else:
                if client and (client in tombs or base + ":*" in tombs):  # этот текст этой записи удалён — поздняя досылка не воскрешает
                    return None
                if live:  # одна запись очереди — один комментарий: дописанный текст обновляет его; ручную правку досылка не затирает
                    if live.get("clientId") != client and not str(live.get("clientId")).endswith(":edited"):
                        live["text"], live["clientId"], live["edited"] = text, client, now_min()
                    return live
                # запись удалена, а текст другой (дописан после удаления) — новое намерение пользователя: создаём, а не теряем 
                nid = next_comment_id(doc)
                # со страницы — пользователь; без адреса страницы (клиент API через curl) — клиент API, если автор не указан явно
                c = {"id": nid, "text": text, "at": now_min(), "author": body.get("author") or ("owner" if from_page else "bot")}
                doc["nextCommentId"] = nid + 1
                if client:
                    c["clientId"] = client
            items.append(c)
            items.sort(key=lambda x: (str(x.get("at") or ""), x.get("id") if is_int(x.get("id")) else 0))
            return c
        c = find(items, cid, "Комментарий")
        if method == "PATCH":
            c["text"] = v_text(body.get("text"), "text", 2000, required=True)
            c["edited"] = now_min()
            if c.get("clientId"):  # ручная правка: поздняя досылка той же записи её не затрёт
                c["clientId"] = str(c["clientId"]).split(":", 1)[0] + ":edited"
            return c
        items.remove(c)
        full = str(c.get("clientId") or "")
        if full:  # надгробие (последние 50): поздняя досылка этого же текста не воскресит комментарий; правленный вручную — любого текста
            mark = full.split(":", 1)[0] + ":*" if full.endswith(":edited") else full
            tombs = doc.get("commentTombstones") if isinstance(doc.get("commentTombstones"), list) else []
            doc["commentTombstones"] = ([x for x in tombs if x != mark] + [mark])[-50:]
        return c
    finally:
        if not items:
            task.pop("comments", None)


def apply_reminder_fields(doc, r, body, creating):
    if "text" in body or creating:
        r["text"] = v_text(body.get("text"), "text", 200, required=True)
    if "date" in body or creating:
        r["date"] = v_date(body.get("date"), "date")
        if not r["date"]:
            raise ApiError(400, "Дата напоминания (date) обязательна")
    if "time" in body or creating:
        r["time"] = v_time(body.get("time"))
        if not r["time"]:
            raise ApiError(400, "Время напоминания (time) обязательно: ЧЧ:ММ, например 15:30")
    if "project" in body:
        if body["project"]:
            find(doc["projects"], body["project"], "Проект")
            r["project"] = body["project"]
        else:
            r.pop("project", None)
    if "done" in body:
        if not isinstance(body["done"], bool):
            raise ApiError(400, "Выполнено (done): true или false")
        r["done"] = body["done"]


def reminder_op(doc, rid, method, body):
    """Свои напоминания дашборда (не запись в Google): {id, text, date, time, project?, done}."""
    items = doc.get("reminders") if isinstance(doc.get("reminders"), list) else []
    doc["reminders"] = items
    if method == "POST":
        nid = max(doc.get("nextReminderId") if is_int(doc.get("nextReminderId")) else 1,
                  max((x["id"] for x in items if isinstance(x, dict) and is_int(x.get("id"))), default=0) + 1)
        r = {"id": nid, "done": False}
        doc["nextReminderId"] = nid + 1
        apply_reminder_fields(doc, r, body, creating=True)
        items.append(r)
        return r
    r = find(items, rid, "Напоминание")
    if method == "PATCH":
        apply_reminder_fields(doc, r, body, creating=False)
        return r
    items.remove(r)
    return r


def set_event_link(doc, body):
    """Ручная привязка события к проекту по UID: project=id — привязать, "" — отвязать навсегда, null — снова авто."""
    uid = v_text(body.get("uid"), "uid", 300, required=True)
    if "project" not in body:
        raise ApiError(400, "Нужно поле project: id проекта, \"\" (отвязать) или null (авто)")
    pid = body["project"]
    links = doc.get("eventLinks") if isinstance(doc.get("eventLinks"), dict) else {}
    if pid is None:
        links.pop(uid, None)
    elif pid == "":
        links[uid] = ""
    else:
        find(doc["projects"], pid, "Проект")
        links[uid] = str(pid)
    doc["eventLinks"] = links
    return {"uid": uid, "project": links.get(uid)}


def summary(doc, day=None):
    """Одна правда для счётчиков страницы и клиента API: что просрочено, на сегодня, ждём, без срока, приоритетно, на паузе."""
    day = day or today()
    projects = {str(p.get("id")): p for p in doc["projects"] if isinstance(p, dict)}

    def project_paused(t):
        p = projects.get(str(t.get("project")))
        return bool(p and (isinstance(p.get("pause"), dict) or p.get("status") == "done"))  # завершённый проект — вне счётчиков

    def tid(t):
        return t.get("id") if is_int(t.get("id")) else 0

    by_due = lambda t: (t.get("due") or "9999-99-99", t.get("time") or "99:99", tid(t))
    open_ = sorted((t for t in doc["tasks"] if isinstance(t, dict) and t.get("state") != "done"), key=by_due)
    active = [t for t in open_ if not isinstance(t.get("pause"), dict) and not project_paused(t)]
    work = [t for t in active if t.get("state") != "wait"]  # пустое или неизвестное состояние (todo) = «в работе», как на странице
    late = [t for t in work if t.get("due") and t["due"] < day]
    due_today = [t for t in work if t.get("due") == day]
    wait = [t for t in active if t.get("state") == "wait"]
    no_due = [t for t in work if not t.get("due")]
    prio = sorted((t for t in active if t.get("star") is True),
                  key=lambda t: (0 if t.get("due") and t["due"] < day else 1, t.get("due") or "9999-99-99", t.get("time") or "99:99", tid(t)))
    done_ids = {str(p.get("id")) for p in doc["projects"] if isinstance(p, dict) and p.get("status") == "done"}
    paused_projects = [p.get("id") for p in doc["projects"] if isinstance(p, dict) and isinstance(p.get("pause"), dict) and str(p.get("id")) not in done_ids]
    paused_tasks = [t for t in open_ if isinstance(t.get("pause"), dict) and str(t.get("project")) not in done_ids]
    ids = lambda ts: [t.get("id") for t in ts]
    out = {"date": day, "late": ids(late), "today": ids(due_today), "wait": ids(wait), "noDue": ids(no_due),
           "priority": ids(prio), "paused": {"projects": paused_projects, "tasks": ids(paused_tasks)}, "finishedProjects": sorted(done_ids)}
    out["counts"] = {"late": len(late), "today": len(due_today), "wait": len(wait), "noDue": len(no_due),
                     "priority": len(prio), "pausedProjects": len(paused_projects), "pausedTasks": len(paused_tasks)}
    return out


def deadline_stats(doc, d_from, d_to):
    """«Статистика сроков» за период — по сотрудникам и проектам. Выполнено — задачи, выполненные в периоде;
    вовремя — не позже планового срока; переносы — записи истории в периоде (на более поздний срок или снятие срока),
    «после просрочки» — срок уже прошёл в момент переноса; средняя задержка — по выполненным с опозданием. Примеры не считаются."""
    f, t = d_from.isoformat(), d_to.isoformat()
    emps = {e["id"]: e for e in doc["employees"] if isinstance(e, dict) and is_int(e.get("id"))}
    projs = {str(p.get("id")): p for p in doc["projects"] if isinstance(p, dict)}
    rows, order = {}, list(DUE_REASONS)

    def row(kind, key, name):
        k = f"{kind}:{key}"
        if k not in rows:
            rows[k] = {"key": k, "name": name, "done": 0, "withPlan": 0, "onTime": 0, "lateDone": 0, "delay": 0,
                       "moves": 0, "movesEarly": 0, "movesLate": 0, "reasons": {}, "tasks": []}
        return rows[k]

    for task in doc["tasks"]:
        if not isinstance(task, dict) or task.get("example"):
            continue
        done_in = task.get("state") == "done" and is_day(task.get("done")) and f <= task["done"] <= t
        hist = task.get("dueHistory") if isinstance(task.get("dueHistory"), list) else []
        moves = [h for h in hist if is_postpone(h) and isinstance(h.get("at"), str) and f <= h["at"][:10] <= t]
        if not done_in and not moves:
            continue
        e = emps.get(task.get("whoId")) if is_int(task.get("whoId")) else None  # испорченное поле не роняет статистику
        who = full_name(e) if e else str(task.get("who") or "").strip()[:60]
        pid = str(task.get("project"))
        p = projs.get(pid)
        late = done_late(task) if done_in else None  # от плана и даты выполнения, не от сохранённого поля
        for r in (row("e", str(e["id"]) if e else ("who:" + norm(who) if who else "none"), who or "Без исполнителя"),
                  row("p", pid, str(p.get("name") or pid) if p else pid), row("t", "all", "Всего")):
            r["tasks"].append(task.get("id"))
            if done_in:
                r["done"] += 1
                if late is not None:
                    r["withPlan"] += 1
                    if late > 0:
                        r["lateDone"] += 1
                        r["delay"] += late
                    else:
                        r["onTime"] += 1
            for h in moves:
                r["moves"] += 1
                r["movesLate" if h.get("late") is True else "movesEarly"] += 1
                c = h["reason"] if isinstance(h.get("reason"), str) and h["reason"] in DUE_REASONS else "other"
                r["reasons"][c] = r["reasons"].get(c, 0) + 1

    def fin(r):
        r["onTimePct"] = round(100 * r["onTime"] / r["withPlan"]) if r["withPlan"] else None
        r["avgDelay"] = round(r.pop("delay") / r["lateDone"], 1) if r["lateDone"] else None
        r["reasons"] = [{"code": c, "label": DUE_REASONS[c], "n": n}
                        for c, n in sorted(r["reasons"].items(), key=lambda x: (-x[1], order.index(x[0])))]
        return r

    key = lambda r: (-(r["done"] + r["moves"]), r["name"].casefold())
    pick = lambda kind: sorted((fin(r) for k, r in rows.items() if k.startswith(kind + ":")), key=key)
    total = rows.get("t:all") or row("t", "all", "Всего")
    return {"ok": True, "from": f, "to": t, "reasonLabels": DUE_REASONS,
            "employees": pick("e"), "projects": pick("p"), "total": fin(total)}


# ---------- привязка событий к проектам ----------

def project_terms(projects):
    terms = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        names = [p.get("name")] + (p.get("aliases") if isinstance(p.get("aliases"), list) else [])
        for n in names:
            n = norm(n)
            if len(n) >= 3:  # короткие слова совпадают случайно
                terms.append((str(p.get("id")), re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)")))
    return terms


def match_project(text, terms):
    """Проект, названный в тексте целым словом. Два разных проекта — сомнительно, не привязываем."""
    t = norm(text)
    hits = {pid for pid, rx in terms if rx.search(t)}
    return hits.pop() if len(hits) == 1 else None


# ---------- календарь: секретные адреса iCal ----------

class CalError(Exception):
    pass


ICAL_HOSTS = ("google.com", "googleusercontent.com", "icloud.com", "outlook.office365.com", "outlook.office.com", "outlook.live.com")


def normalize_url(url: str) -> str:
    """только https и только сервисы календарей (Google, iCloud, Outlook) — не локальные адреса и не чужие хосты."""
    try:
        if url.lower().startswith("webcal://"):
            url = "https://" + url[9:]
        p = urlsplit(url)
        host, port = (p.hostname or "").lower().rstrip("."), p.port
    except ValueError:
        raise CalError("в нём ошибка, скопируйте его заново")
    if p.scheme == "http" and host in ("127.0.0.1", "localhost") and os.environ.get("MOYDEN_TEST_ICAL") == "1" and not getattr(sys, "frozen", False):
        return url  # только для автоматических проверок
    if p.scheme != "https" or not host:
        raise CalError("адрес должен начинаться с https://")
    if "%" in p.netloc or "\\" in p.netloc:  # R10: закодированные символы в имени хоста не принимаем
        raise CalError("в нём ошибка, скопируйте его заново")
    if p.username or p.password or port not in (None, 443) or not any(host == d or host.endswith("." + d) for d in ICAL_HOSTS):
        raise CalError("нужен секретный адрес Google Календаря (calendar.google.com), iCloud или Outlook")
    return url


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправление — только на такой же разрешённый адрес."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        normalize_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


ICAL_OPENER = urllib.request.build_opener(_CheckedRedirect)


def html_to_text(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)  # только настоящие теги: «5 < 6 > 3» остаётся текстом
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


SYNC_STALE = 2 * 3600  # данные синхронизации старше двух часов — тихая пометка


def sync_time(v):
    """Время из файла синхронизации → (строка «ГГГГ-ММ-ДДTЧЧ:ММ» по местному времени, datetime) или None."""
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
    return d.strftime("%Y-%m-%dT%H:%M"), d


class Calendars:
    def __init__(self, cfg_path: Path, cache_dir: Path, refresh: int, data_fn=None, sync_path: Path = None):
        self.sync_path = sync_path
        self.sync_cache = (None, None)  # (mtime_ns+size, разобранный файл)
        self.cfg_path = cfg_path
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.data_fn = data_fn
        self.lock = threading.Lock()
        self.status = {}   # key -> {"error": str|None, "tried": iso}
        self.parsed = {}   # key -> (mtime_ns, query)
        self.cfg_stamp = None
        try:
            import icalendar  # noqa: F401
            import recurring_ical_events  # noqa: F401
            self.packages = True
        except ImportError:
            self.packages = False

    def load_cfg(self):
        try:
            raw = json.loads(self.cfg_path.read_text("utf-8-sig"))
        except FileNotFoundError:  # первый запуск: места под календари есть, адреса пользователь вставит на странице
            return [{"key": hashlib.sha1(n.encode("utf-8")).hexdigest()[:10], "name": n, "color": PALETTE[i % len(PALETTE)], "url": ""}
                    for i, n in enumerate(DEFAULT_CALENDARS)], None
        except (ValueError, UnicodeDecodeError) as e:
            return [], f"{self.cfg_path.name} повреждён (строка {getattr(e, 'lineno', '?')})"
        out, seen = [], set()
        items = raw.get("calendars") if isinstance(raw, dict) else None
        for i, c in enumerate(items if isinstance(items, list) else []):
            if not isinstance(c, dict) or c.get("enabled") is False:
                continue
            name = str(c.get("name") or f"Календарь {i + 1}").strip()[:60]
            color = c.get("color") if isinstance(c.get("color"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c.get("color")) else PALETTE[i % len(PALETTE)]
            key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
            if key in seen:
                key = f"{key}{i}"
            seen.add(key)
            out.append({"key": key, "name": name, "color": color, "url": str(c.get("ical_url") or "").strip()})
        return out, None

    def cache_file(self, key):
        return self.cache_dir / f"{key}.ics"

    def fetch_one(self, cal, record=True):
        """Скачать календарь в кэш. record=False — проверка нового адреса: статус работающего календаря не трогаем (L2)."""
        import icalendar
        try:
            url = normalize_url(cal["url"])
            req = urllib.request.Request(url, headers={"User-Agent": "MoyDen-dashboard/1.0"})
            with ICAL_OPENER.open(req, timeout=25) as r:
                body = r.read(MAX_ICS + 1)
            if len(body) > MAX_ICS:
                raise CalError("календарь больше 20 МБ")
            if b"BEGIN:VCALENDAR" not in body[:4096]:
                raise CalError("по этому адресу не календарь: проверьте, что скопирован «Секретный адрес в формате iCal»")
            try:
                icalendar.Calendar.from_ical(body)
            except Exception:
                raise CalError("файл календаря не читается")
            if not record:
                return None
            self.cache_dir.mkdir(exist_ok=True)
            tmp = self.cache_dir / f"{cal['key']}.tmp"
            tmp.write_bytes(body)
            replace_with_retry(tmp, self.cache_file(cal["key"]))
            err = None
        except CalError as e:
            err = str(e)
        except urllib.error.HTTPError as e:
            err = {404: "адрес не найден (ошибка 404): возможно, секретный адрес сброшен в Google",
                   401: "доступ запрещён (ошибка 401)", 403: "доступ запрещён (ошибка 403)"}.get(e.code, f"Google Календарь ответил ошибкой {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            err = "нет связи с Google Календарём"
        except Exception as e:  # не роняем поток из-за одного календаря
            err = "не удалось обновить календарь"
            log.warning("calendar %s: unexpected %s", cal["name"], type(e).__name__)
        if err:
            # в журнал — только имя календаря и причина, адрес никогда
            log.warning("calendar %s: %s", cal["name"], err)
        if record:
            with self.lock:
                self.status[cal["key"]] = {"error": err, "tried": datetime.now(TZ).isoformat(timespec="seconds")}
        return err

    @staticmethod
    def mask(url):
        """Адрес без секрета: «calendar.google.com/…/basic.ics»."""
        try:
            u = urlsplit(url)
            last = u.path.rstrip('/').rsplit('/', 1)[-1]
            tail = last if last.lower().endswith(".ics") and len(last) <= 24 else "…"  # L3: у iCloud последний сегмент — секрет
            return f"{u.hostname or ''}/…/{tail}" if u.hostname else ""
        except ValueError:
            return ""

    def sync_colors(self):
        sync = self.load_sync()
        return {c["name"]: c["color"] for c in sync["calendars"] if c.get("own")} if sync and not sync["error"] else {}  # R7: только цвет, указанный в Google

    def public(self):
        cals, err = self.load_cfg()
        sc = self.sync_colors()
        cals = [{**c, "color": sc.get(c["name"], c["color"])} for c in cals]  # эталон цвета — Google
        with self.lock:
            st = dict(self.status)
        return {"error": err, "calendars": [{"name": c["name"], "color": c["color"], "connected": bool(c["url"]),
                                             "address": self.mask(c["url"]) if c["url"] else "",
                                             "problem": (st.get(c["key"]) or {}).get("error") if c["url"] else None} for c in cals]}

    def connect(self, body):
        """Пользователь вставил секретный адрес на странице: проверить, что это календарь, и записать в calendars.local.json."""
        name = v_text(body.get("name"), "calendar", 60, required=True)
        url = v_text(body.get("url"), "url", 2000)
        cals, err = self.load_cfg()
        if err and not self.cfg_path.exists():
            cals = []
        cal = next((c for c in cals if c["name"] == name), None)
        if cal is None:
            raise ApiError(404, f"Календаря «{name}» нет в списке")
        if url:
            try:
                normalize_url(url)
            except CalError as e:
                raise ApiError(400, f"Адрес не подходит: {e}")
            if not self.packages:
                raise ApiError(500, "Не хватает частей программы для календаря: переустановите «Мой день»")
            problem = self.fetch_one({**cal, "url": url}, record=False)
            if problem:
                raise ApiError(400, f"Календарь не подключён: {problem}")
        try:
            raw = json.loads(self.cfg_path.read_text("utf-8-sig"))
        except FileNotFoundError:
            raw = {"calendars": [{"name": c["name"], "color": c["color"], "ical_url": ""} for c in cals]}
        except (ValueError, UnicodeDecodeError):
            raise ApiError(500, f"Файл {self.cfg_path.name} повреждён: исправьте его или удалите и подключите календари заново")
        for c in raw.get("calendars", []):
            if isinstance(c, dict) and str(c.get("name") or "").strip()[:60] == name:
                c["ical_url"] = url
                break
        tmp = self.cfg_path.with_name(self.cfg_path.name + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", "utf-8")
        try:
            replace_with_retry(tmp, self.cfg_path)
        except BaseException:  # копия с полным секретным адресом не остаётся рядом
            tmp.unlink(missing_ok=True)
            raise
        if url:
            self.fetch_one({**cal, "url": url})  # адрес проверен — сразу в кэш и в статус
        if not url:
            with self.lock:
                self.status.pop(cal["key"], None)
            self.cache_file(cal["key"]).unlink(missing_ok=True)
        log.info("calendar %s: %s from page", name, "connected" if url else "disconnected")
        return {"name": name, "connected": bool(url), "address": self.mask(url) if url else ""}

    def drop_stale_cache(self, cals):
        # календарь удалён, переименован или адрес стёрт — его события не должны лежать на диске
        keep = {c["key"] for c in cals if c["url"]}
        if not self.cache_dir.exists():
            return
        for f in self.cache_dir.glob("*.ics"):
            if f.stem not in keep:
                f.unlink(missing_ok=True)
                with self.lock:
                    self.parsed.pop(f.stem, None)

    def refresh_all(self):
        cals, err = self.load_cfg()
        if not err:
            self.drop_stale_cache(cals)
        for cal in cals:
            if cal["url"]:
                self.fetch_one(cal)

    def _cfg_changed(self):
        try:
            st = self.cfg_path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        changed = stamp != self.cfg_stamp
        self.cfg_stamp = stamp
        return changed

    def run(self):
        if not self.packages:
            log.warning("calendar packages missing: run with .venv")
            return
        self._cfg_changed()
        while True:
            try:
                self.refresh_all()
            except Exception:
                log.exception("calendar refresh failed")
            deadline = time.monotonic() + self.refresh
            while time.monotonic() < deadline:
                time.sleep(min(3, self.refresh))
                if self._cfg_changed():  # пользователь вписал адрес — подтягиваем сразу
                    break

    def _query(self, key, path):
        import icalendar
        import recurring_ical_events
        import x_wr_timezone
        mtime = path.stat().st_mtime_ns
        cached = self.parsed.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        cal = icalendar.Calendar.from_ical(path.read_bytes())
        cal = x_wr_timezone.to_standard(cal)
        q = recurring_ical_events.of(cal, skip_bad_series=True)
        self.parsed[key] = (mtime, q)
        return q

    def _links(self):
        if not self.data_fn:
            return {}, [], set()
        try:
            doc, _ = self.data_fn()
        except ApiError:
            return {}, [], set()
        links = doc.get("eventLinks") if isinstance(doc.get("eventLinks"), dict) else {}
        return links, project_terms(doc["projects"]), {str(p.get("id")) for p in doc["projects"] if isinstance(p, dict)}

    def load_sync(self):
        """Файл google-events.json (необязательно: пишет внешняя синхронизация, ключ --google-sync). Перечитывается при изменении; битый — не роняет сервер."""
        if not self.sync_path:
            return None
        try:
            st = self.sync_path.stat()
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        if self.sync_cache[0] == stamp:
            return self.sync_cache[1]
        out = {"calendars": [], "events": [], "synced": None, "error": None}
        try:
            raw = json.loads(self.sync_path.read_text("utf-8-sig"))
            if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
                raise ValueError("нет списка events")
            for i, c in enumerate(raw.get("calendars") or []):
                if isinstance(c, dict) and c.get("id"):
                    own = isinstance(c.get("color"), str) and bool(re.fullmatch(r"#[0-9a-fA-F]{6}", c.get("color")))
                    color = c["color"] if own else PALETTE[i % len(PALETTE)]
                    out["calendars"].append({"id": str(c["id"]), "name": str(c.get("name") or c["id"]).strip()[:60], "color": color, "own": own})
            out["synced"] = sync_time(raw.get("synced_at"))
            out["events"] = [e for e in raw["events"] if isinstance(e, dict)]
        except (OSError, ValueError, UnicodeDecodeError) as e:
            out["error"] = "файл синхронизации с Google не читается"
            log.warning("google sync file: %s", type(e).__name__)
        self.sync_cache = (stamp, out)
        return out

    def sync_events(self, sync, base_idx, d_from, d_to):
        cal_idx = {c["id"]: base_idx + i for i, c in enumerate(sync["calendars"])}
        lo, hi = d_from.isoformat(), d_to.isoformat()
        out = []
        for e in sync["events"]:
            ci = cal_idx.get(str(e.get("calendar")))
            if ci is None:
                continue
            title = str(e.get("title") or "").strip()[:300] or "(без названия)"
            if e.get("allDay"):
                sd = str(e.get("start") or "")[:10]
                ed = str(e.get("end") or "")[:10]
                try:
                    date.fromisoformat(sd)
                    ed = ed if ed and ed > sd and date.fromisoformat(ed) else (date.fromisoformat(sd) + timedelta(days=1)).isoformat()
                except ValueError:
                    continue
                start, end, all_day = sd, ed, True
                if not (sd < hi and ed > lo):
                    continue
            else:
                st, en = sync_time(e.get("start")), sync_time(e.get("end"))
                if not st:
                    continue
                if not en or en[1] < st[1]:
                    en = st  # событие без конца — точка во времени
                start, end, all_day = st[0], en[0], False
                lo_t = lo + "T00:00"
                if not (start[:10] < hi and (end > lo_t or (end == start and start >= lo_t))):  # L5: 23:00–00:00 вчера — не сегодня
                    continue
            meet = e.get("meet") if isinstance(e.get("meet"), str) and re.fullmatch(r"https://meet\.google\.com/[\w-]+", e.get("meet")) else ""
            uid = str(e.get("id") or "")[:300]
            out.append({"id": hashlib.sha1(f"g|{ci}|{uid}|{start}".encode("utf-8")).hexdigest()[:16], "uid": uid, "cal": ci,
                        "title": title, "start": start, "end": end, "allDay": all_day,
                        "location": str(e.get("location") or "").strip()[:300],
                        "description": html_to_text(str(e.get("description") or ""))[:2000], "meet": meet})
        return out

    def events(self, d_from: date, d_to: date, project=None):
        cals, cfg_error = self.load_cfg()
        local_colors = {c["name"]: c["color"] for c in cals}
        links, terms, pids = self._links()
        infos, events = [], []
        sync = self.load_sync()
        fresh = bool(sync and not sync["error"] and sync["synced"] and (datetime.now(TZ) - sync["synced"][1]).total_seconds() <= SYNC_STALE)
        synced_names = {c["name"] for c in sync["calendars"]} if fresh else set()  # устаревшая синхронизация не выключает запасной iCal
        cals = [c for c in cals if c["name"] not in synced_names]  # есть синхронизация — адрес iCal того же календаря не нужен (запасной путь)
        sc = {c["name"]: c["color"] for c in sync["calendars"] if c.get("own")} if sync and not sync["error"] else {}
        cals = [{**c, "color": sc.get(c["name"], c["color"])} for c in cals]  # цвет из Google важнее calendars.local.json
        for idx, cal in enumerate(cals):
            path = self.cache_file(cal["key"])
            with self.lock:
                st = dict(self.status.get(cal["key"], {}))
            has_cache = path.exists()
            info = {"name": cal["name"], "color": cal["color"], "configured": bool(cal["url"]),
                    "updated": datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat(timespec="minutes") if has_cache else None,
                    "error": st.get("error"), "pending": bool(cal["url"]) and not has_cache and not st}
            if not self.packages and cal["url"]:
                info["error"] = "не установлены пакеты разбора календаря (.venv)"
            elif cal["url"] and has_cache:
                try:
                    with self.lock:
                        evs = list(self._query(cal["key"], path).between(d_from - timedelta(days=1), d_to + timedelta(days=1)))
                    for ev in evs:
                        item = event_to_json(ev, idx, d_from, d_to)
                        if item:
                            uid = item["uid"]
                            if uid and uid in links and (links[uid] == "" or links[uid] in pids):
                                item["project"], item["link"] = (links[uid] or None), ("manual" if links[uid] else "off")
                            else:
                                pid = match_project(item["title"] + "\n" + item["description"], terms)
                                item["project"], item["link"] = pid, ("auto" if pid else None)
                            events.append(item)
                except Exception as e:
                    info["error"] = "сохранённый календарь не читается"
                    log.warning("calendar %s: parse %s", cal["name"], type(e).__name__)
            infos.append(info)
        if sync:
            age = (datetime.now(TZ) - sync["synced"][1]).total_seconds() if sync["synced"] else None
            for c in sync["calendars"] if not sync["error"] else []:
                infos.append({"name": c["name"], "color": c["color"] if c.get("own") else local_colors.get(c["name"], c["color"]), "configured": True, "source": "sync",
                              "updated": sync["synced"][0] if sync["synced"] else None, "stale": age is None or age > SYNC_STALE,
                              "error": None, "pending": False})
            if sync["error"]:
                infos.append({"name": "Google", "color": PALETTE[0], "configured": True, "source": "sync", "updated": None,
                              "stale": True, "error": sync["error"], "pending": False})
            for item in self.sync_events(sync, len(cals), d_from, d_to) if not sync["error"] else []:
                uid = item["uid"]
                if uid and uid in links and (links[uid] == "" or links[uid] in pids):
                    item["project"], item["link"] = (links[uid] or None), ("manual" if links[uid] else "off")
                else:
                    pid = match_project(item["title"] + "\n" + item["description"], terms)
                    item["project"], item["link"] = pid, ("auto" if pid else None)
                events.append(item)
        if project is not None:
            events = [e for e in events if e["project"] == project]
        events.sort(key=lambda e: (e["start"], e["end"]))
        return {"tz": TZ_NAME, "calendars": infos, "events": events[:5000],
                "configError": cfg_error, "configFile": self.cfg_path.name}


def to_local(v):
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ)  # «плавающее» время без пояса считаем местным
        return v.astimezone(TZ)
    return v


def event_to_json(ev, cal_idx, d_from, d_to):
    if str(ev.get("STATUS", "")).upper() == "CANCELLED":
        return None
    s = ev.get("DTSTART").dt
    if ev.get("DTEND") is not None:
        e = ev.get("DTEND").dt
    elif ev.get("DURATION") is not None:
        e = s + ev.get("DURATION").dt
    else:
        e = s if isinstance(s, datetime) else s + timedelta(days=1)
    all_day = not isinstance(s, datetime)
    if all_day:
        if isinstance(e, datetime):
            e = e.date()
        if e <= s:
            e = s + timedelta(days=1)
        start, end = s.isoformat(), e.isoformat()
        if not (s < d_to and e > d_from):
            return None
    else:
        s, e = to_local(s), to_local(e if isinstance(e, datetime) else datetime(e.year, e.month, e.day))
        if e < s:
            e = s
        lo = datetime(d_from.year, d_from.month, d_from.day, tzinfo=TZ)
        hi = datetime(d_to.year, d_to.month, d_to.day, tzinfo=TZ)
        if not (s < hi and (e > lo or (e == s and s >= lo))):
            return None
        start, end = s.strftime("%Y-%m-%dT%H:%M"), e.strftime("%Y-%m-%dT%H:%M")
    rid = ev.get("RECURRENCE-ID")
    raw_uid = str(ev.get("UID", "") or "")[:300]
    uid = f"{raw_uid}|{rid.dt if rid is not None else start}"
    return {
        "id": hashlib.sha1(f"{cal_idx}|{uid}".encode("utf-8")).hexdigest()[:16],
        "uid": raw_uid,  # привязка к проекту — на всю серию повторов
        "cal": cal_idx,
        "title": str(ev.get("SUMMARY", "") or "").strip()[:300] or "(без названия)",
        "start": start, "end": end, "allDay": all_day,
        "location": str(ev.get("LOCATION", "") or "").strip()[:300],
        "description": html_to_text(str(ev.get("DESCRIPTION", "") or ""))[:2000],
    }


# ---------- погода: Open-Meteo (бесплатно, без ключа) ----------

class Weather:
    def __init__(self, settings_path: Path, cache_path: Path, url=WEATHER_URL, refresh=1800, retry=120, max_age=3600):
        self.settings_path = settings_path
        self.cache_path = cache_path
        self.url = url
        self.refresh, self.retry, self.max_age = refresh, retry, max_age
        self.lock = threading.Lock()
        self.data = {}  # имя города -> последний удачный прогноз
        self.stamp = None
        self._load_cache()

    def cities(self):
        try:
            raw = json.loads(self.settings_path.read_text("utf-8-sig"))
        except FileNotFoundError:
            return [dict(c) for c in DEFAULT_CITIES]
        except (ValueError, UnicodeDecodeError, RecursionError):  # 100 000 скобок — не 500
            log.warning("settings file is broken, weather uses defaults")
            return [dict(c) for c in DEFAULT_CITIES]
        w = raw.get("weather") if isinstance(raw, dict) else None
        if not isinstance(w, dict):
            return [dict(c) for c in DEFAULT_CITIES]
        if w.get("enabled") is False:
            return []
        out = []
        for c in w.get("cities") if isinstance(w.get("cities"), list) else []:
            if not isinstance(c, dict) or c.get("enabled") is False:
                continue
            try:
                lat, lon = float(c["lat"]), float(c["lon"])
            except (KeyError, TypeError, ValueError, OverflowError):  # широта из 401 цифры — город пропускаем
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out.append({"name": str(c.get("name") or "Город").strip()[:40], "lat": lat, "lon": lon,
                            "tz": str(c.get("tz") or "auto")[:40]})
        return out

    def fetch_city(self, c):
        q = urlencode({"latitude": c["lat"], "longitude": c["lon"], "current": "temperature_2m,weather_code,is_day",
                       "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                       "timezone": c["tz"], "forecast_days": 1})
        req = urllib.request.Request(f"{self.url}?{q}", headers={"User-Agent": "MoyDen-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read(256 * 1024).decode("utf-8"))
        cur, d = j["current"], j["daily"]

        def num(v):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError("не число")
            return round(v)
        day = d["time"][0]
        date.fromisoformat(day)
        precip = d.get("precipitation_probability_max", [None])[0]
        return {"name": c["name"], "date": day, "now": num(cur["temperature_2m"]), "code": int(cur["weather_code"]),
                "isDay": bool(cur.get("is_day", 1)), "min": num(d["temperature_2m_min"][0]), "max": num(d["temperature_2m_max"][0]),
                "precip": None if precip is None else num(precip), "offset": int(j.get("utc_offset_seconds", 0)), "fetched": time.time()}

    def refresh_all(self):
        ok, got = True, False
        for c in self.cities():
            try:
                item = self.fetch_city(c)
            except urllib.error.HTTPError as e:
                err = f"сервер погоды ответил ошибкой {e.code}"
            except (urllib.error.URLError, TimeoutError, OSError):
                err = "нет связи с сервером погоды"
            except (ValueError, KeyError, IndexError, TypeError):
                err = "ответ сервера погоды не читается"
            else:
                with self.lock:
                    self.data[c["name"]] = item
                got = True
                continue
            ok = False
            log.warning("weather %s: %s", c["name"], err)
        if got:
            self._save_cache()
        return ok

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        out = []
        for c in self.cities():
            with self.lock:
                d = self.data.get(c["name"])
            if not d:
                continue
            local_day = datetime.fromtimestamp(now, timezone(timedelta(seconds=d["offset"]))).date().isoformat()
            if now - d["fetched"] > self.max_age or d["date"] != local_day:
                continue  # нет свежего прогноза на сегодня — блок прячется, а не врёт
            item = {k: d[k] for k in ("name", "date", "now", "code", "isDay", "min", "max", "precip")}
            item["updated"] = datetime.fromtimestamp(d["fetched"], TZ).isoformat(timespec="minutes")
            out.append(item)
        return {"ok": bool(out), "cities": out}

    def _save_cache(self):
        try:
            with self.lock:
                data = json.dumps(self.data, ensure_ascii=False).encode("utf-8")
            tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
            tmp.write_bytes(data)
            replace_with_retry(tmp, self.cache_path)
        except OSError:
            log.warning("weather cache not saved")

    def _load_cache(self):
        try:
            raw = json.loads(self.cache_path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        need = {"name", "date", "now", "code", "isDay", "min", "max", "precip", "offset", "fetched"}
        if isinstance(raw, dict):
            self.data = {k: v for k, v in raw.items() if isinstance(v, dict) and need <= set(v)}

    def _settings_changed(self):
        try:
            st = self.settings_path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        changed = stamp != self.stamp
        self.stamp = stamp
        return changed

    def run(self):
        self._settings_changed()
        while True:
            try:
                ok = self.refresh_all()
            except Exception:
                log.exception("weather refresh failed")
                ok = False
            deadline = time.monotonic() + (self.refresh if ok else self.retry)
            while time.monotonic() < deadline:
                time.sleep(min(3, self.retry))
                if self._settings_changed():
                    break


def public_settings(path: Path):
    """Несекретная часть settings.json для страницы: тема и отступы режима обоев."""
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError, RecursionError):  # 100 000 скобок — настройки по умолчанию, не 500
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    wp = raw.get("wallpaper") if isinstance(raw.get("wallpaper"), dict) else {}
    px = lambda v, d: int(v) if is_int(v) and 0 <= v <= 1000 else d
    theme = raw.get("theme") if raw.get("theme") in ("light", "dark") else "light"
    cal = raw.get("calendar") if isinstance(raw.get("calendar"), dict) else {}
    th = cal.get("taskHour") if isinstance(cal.get("taskHour"), str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", cal.get("taskHour")) else "18:00"
    return {"theme": theme, "wallpaper": {"left": px(wp.get("left"), 110), "bottom": px(wp.get("bottom"), 48)}, "calendar": {"taskHour": th}}


# ---------- таймер «Помидоро» ----------
POMO_DEFAULTS = {"work": 25, "short": 5, "long": 15, "every": 4}
POMO_LIMITS = {"work": (1, 180, "Длительность работы", "минут"), "short": (1, 60, "Перерыв", "минут"),
               "long": (1, 120, "Длинный перерыв", "минут"), "every": (2, 12, "Число помидоров до длинного перерыва", "")}
POMO_MAX_AGE = 400 * 86400  # отметка начала старше 400 суток или из будущего — битая
POMO_FRESH = 90  # о переходе сообщаем, только если он был не раньше 90 с назад: после закрытой программы уведомлениями не засыпаем
POMO_EXTRA = 300  # «Ещё 5 минут» — продление закончившегося отрезка
POMO_WAIT_MAX = 8 * 3600  # «Время вышло» старше 8 часов (вчерашний помидор) снимается само — утром таймер на 25:00


def plural_ru(n, a, b, c):
    m, h = abs(n) % 10, abs(n) % 100
    return a if m == 1 and h != 11 else b if 2 <= m <= 4 and not 12 <= h <= 14 else c


def pomodoro_message(ev, durations, title=None):
    """Текст уведомления (не длиннее 255 знаков). следующий отрезок сам не начинается — текст говорит, какую кнопку нажать."""
    if ev.get("from") != "work":
        return "Перерыв закончился. Нажмите «Работа» на таймере «Мой день», чтобы начать следующий помидор."
    t = title if not title or len(title) <= 60 else title[:59] + "…"
    head = f"Помидор по задаче «{t}» закончился." if t else "Помидор закончился."
    if ev.get("to") == "long":
        n = durations["long"]
        return f"{head} Пора на длинный перерыв: {n} {plural_ru(n, 'минута', 'минуты', 'минут')} — нажмите «Перерыв» на таймере."
    n = durations["short"]
    return f"{head} Отдохните {n} {plural_ru(n, 'минуту', 'минуты', 'минут')} — нажмите «Перерыв» на таймере."


class Pomodoro:
    """Таймер «Помидоро». Состояние пишет только сервер (помидоро.json в папке данных); окно и обои показывают остаток от отметки
    начала (startedAt), поэтому сворачивание, перезапуск окна и два окна отсчёт не сбивают. Помидор закончился — счётчики за день
    (по дате конца, местное время) и по задаче. «Время вышло» — конец любого отрезка останавливает таймер на следующей фазе
    (waiting): перерыв и работа начинаются только кнопкой, «Ещё 5 минут» продлевает закончившийся отрезок без второго счёта.
    data.json таймер не трогает."""

    def __init__(self, path, data_fn=None, clock=time.time):
        self.path, self.data_fn, self.clock = Path(path), data_fn, clock
        self.lock = threading.Lock()
        self.events = []
        self.boot = time.time_ns()  # номер запуска: страница сравнивает seq только внутри одного запуска сервера
        self.st = self._load()

    @staticmethod
    def _fresh():
        return {"v": 1, "durations": dict(POMO_DEFAULTS), "phase": "work", "running": False, "startedAt": None, "elapsed": 0.0,
                "cycle": 0, "task": None, "days": {}, "tasks": {}, "seq": 0, "waiting": None, "extra": None, "sound": True}

    def _load(self):
        try:
            return self._load_raw()
        except Exception as e:  # любой мусор (огромные числа, битый текст) — копия .bad для разбора, таймер с начала, сервер стартует
            log.warning("pomodoro state unreadable (%s) — copy .bad, defaults", type(e).__name__)
            try:
                self.path.with_name(self.path.name + ".bad").write_bytes(self.path.read_bytes())
            except OSError:
                pass
            return self._fresh()

    def _load_raw(self):
        st = self._fresh()
        try:
            raw = json.loads(self.path.read_text("utf-8-sig"))  # BOM не обнуляет счётчики
        except FileNotFoundError:
            return st
        if not isinstance(raw, dict):  # [1,2,3] и т. п. — копия .bad, иначе первая запись затёрла бы файл
            raise ValueError("pomodoro state is not an object")
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # не 1e309 и не NaN
        d = raw.get("durations") if isinstance(raw.get("durations"), dict) else {}
        for k, (lo, hi, _, _) in POMO_LIMITS.items():
            if is_int(d.get(k)) and lo <= d[k] <= hi:
                st["durations"][k] = d[k]
        if raw.get("phase") in ("work", "short", "long"):
            st["phase"] = raw["phase"]
        now = self.clock()
        if raw.get("running") is True and num(raw.get("startedAt")) and now - POMO_MAX_AGE <= raw["startedAt"] <= now + 60:
            st["running"], st["startedAt"] = True, float(raw["startedAt"])  # миллисекунды, отрицательное время — отрезок с начала
        w = raw.get("waiting")  # «Время вышло» переживает перезапуск; идущий отрезок важнее — тогда ожидания нет
        if not st["running"] and isinstance(w, dict) and w.get("from") in ("work", "short", "long") and num(w.get("at")) and now - POMO_MAX_AGE <= w["at"] <= now + 60:
            st["waiting"] = {"from": w["from"], "at": float(w["at"])}
        if isinstance(raw.get("sound"), bool):
            st["sound"] = raw["sound"]
        if num(raw.get("elapsed")) and 0 <= raw["elapsed"] <= POMO_LIMITS["work"][1] * 60:
            st["elapsed"] = float(raw["elapsed"])
        if is_int(raw.get("cycle")) and 0 <= raw["cycle"] < 1000:
            st["cycle"] = raw["cycle"]
        st["task"] = raw["task"] if is_int(raw.get("task")) else None
        for k in ("days", "tasks"):
            if isinstance(raw.get(k), dict):
                st[k] = {str(a): b for a, b in raw[k].items() if is_int(b) and 0 < b < 10 ** 6}
        st["seq"] = raw["seq"] if is_int(raw.get("seq")) and 0 <= raw["seq"] < 10 ** 12 else 0
        st["days"] = {k: v for k, v in st["days"].items() if re.fullmatch(r"\d{4}-\d\d-\d\d", k)}  # чужие ключи не переживают чистку
        if raw.get("extra") == POMO_EXTRA and not st["waiting"] and (st["running"] or st["elapsed"] > 0):  # продление — только идущего отрезка
            st["extra"] = POMO_EXTRA
        return st

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:  # на диск до замены (как у data.json)
            f.write(json.dumps(self.st, ensure_ascii=False, indent=1))
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, self.path)

    def _dur(self):
        return self.st["extra"] or self.st["durations"][self.st["phase"]] * 60  # «Ещё 5 минут» — длина продления, а не отрезка

    def _done(self, now):
        st = self.st
        return st["elapsed"] + (max(0.0, now - st["startedAt"]) if st["running"] else 0.0)

    def _advance(self, now):
        try:
            changed = self._advance_raw(now)
        except (OverflowError, OSError, ValueError) as e:  # битое время в состоянии: отрезок заново, счётчики целы, «Сброс» работает
            log.warning("pomodoro state broken (%s) — segment reset", type(e).__name__)
            self.st.update(phase="work", running=False, startedAt=None, elapsed=0.0, waiting=None, extra=None)
            self.st["seq"] += 1
            changed = True
        if changed:
            self._save_logged()

    def _save_logged(self):
        """Запись не удалась (файл занят антивирусом, диск полон) — состояние живёт в памяти и запишется со следующим изменением."""
        try:
            self._save()
        except OSError as e:
            log.warning("pomodoro state not saved: %s", type(e).__name__)

    def _advance_raw(self, now):
        """Довести состояние до момента now. «Время вышло» — отрезок кончился, таймер стоит на следующей фазе и ждёт кнопку
        (waiting: какой отрезок и когда кончился); помидор засчитан в момент конца, продление («Ещё 5 минут») второй раз не считается."""
        st, changed = self.st, False
        if st["running"] and self._done(now) >= self._dur():
            end, src = st["startedAt"] + (self._dur() - st["elapsed"]), st["phase"]
            if src == "work":
                if not st["extra"]:
                    day = datetime.fromtimestamp(end, TZ).date().isoformat()
                    st["days"][day] = st["days"].get(day, 0) + 1
                    if st["task"] is not None:
                        k = str(st["task"])
                        st["tasks"][k] = st["tasks"].get(k, 0) + 1
                    st["cycle"] += 1
                dst = "long" if st["cycle"] >= st["durations"]["every"] else "short"
            else:
                if src == "long":
                    st["cycle"] = 0
                dst = "work"
            st.update(phase=dst, running=False, startedAt=None, elapsed=0.0, extra=None, waiting={"from": src, "at": end})
            self.events.append({"from": src, "to": dst, "at": end, "task": st["task"], "durations": dict(st["durations"]), "sound": st["sound"]})
            changed = True
        w = st["waiting"]
        if w and now - w["at"] > POMO_WAIT_MAX:  # вчерашнее «Время вышло» не висит утром: таймер на 25:00, как после «Сброса»
            if st["phase"] == "long":
                st["cycle"] = 0
            st.update(phase="work", running=False, startedAt=None, elapsed=0.0, waiting=None, extra=None)
            changed = True
        if changed:
            cut = (datetime.fromtimestamp(now, TZ).date() - timedelta(days=90)).isoformat()
            st["days"] = {k: v for k, v in st["days"].items() if k >= cut}
            st["seq"] += 1
        return changed

    def _public(self, now):
        st = self.st
        dur, day = self._dur(), datetime.fromtimestamp(now, TZ).date().isoformat()
        rem = max(0.0, dur - self._done(now))
        return {"ok": True, "phase": st["phase"], "running": st["running"], "duration": dur, "remaining": round(rem, 3),
                "endsAt": round((now + rem) * 1000) if st["running"] else None, "serverNow": round(now * 1000), "cycle": st["cycle"],
                "every": st["durations"]["every"], "durations": dict(st["durations"]), "task": st["task"], "today": st["days"].get(day, 0),
                "taskCount": st["tasks"].get(str(st["task"]), 0) if st["task"] is not None else 0, "seq": st["seq"], "boot": self.boot,
                "waiting": bool(st["waiting"]), "expired": {"from": st["waiting"]["from"], "at": round(st["waiting"]["at"] * 1000)} if st["waiting"] else None,
                "extra": st["extra"], "sound": st["sound"]}

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self._advance(now)
            return self._public(now)

    def tick(self):
        """Довести состояние и вернуть свежие переходы (для звука и уведомления) — каждый ровно один раз."""
        with self.lock:
            now = self.clock()
            self._advance(now)
            ev, self.events = self.events, []
        return [e for e in ev if now - e["at"] < POMO_FRESH]

    def run(self, notify, interval=1.0, stop=None):
        stop = stop or threading.Event()
        while True:
            try:
                for ev in self.tick():
                    notify(ev)
            except Exception:  # сбой уведомления или записи не останавливает таймер; причина — в журнал
                log.exception("pomodoro tick failed")
            if stop.wait(interval):
                return

    def _check_task(self, v):
        if v is None:
            return None
        ok = is_int(v)
        if ok and self.data_fn:
            doc, _ = self.data_fn()
            ok = any(isinstance(t, dict) and t.get("id") == v and t.get("state") != "done" for t in doc.get("tasks", []))
        if not ok:
            raise ApiError(400, "Задача не найдена или уже выполнена")
        return v

    @staticmethod
    def _check_durations(d):
        if not isinstance(d, dict) or not d:
            raise ApiError(400, "Укажите длительности таймера целыми числами минут.")
        out = {}
        for k, v in d.items():
            if k not in POMO_LIMITS:
                continue
            lo, hi, label, unit = POMO_LIMITS[k]
            if not is_int(v) or not lo <= v <= hi:
                raise ApiError(400, f"{label} — от {lo} до {hi}" + (f" {unit}." if unit else "."))
            out[k] = v
        if not out:
            raise ApiError(400, "Укажите длительности таймера целыми числами минут.")
        return out

    def command(self, body):
        act = body.get("action") if isinstance(body, dict) else None
        if act not in ("start", "pause", "reset", "task", "settings", "extend", "sound"):
            raise ApiError(400, "Неизвестное действие таймера")
        tid = self._check_task(body.get("task")) if act == "task" else None
        dur = self._check_durations(body.get("durations")) if act == "settings" else None
        if act == "sound" and not isinstance(body.get("on"), bool):
            raise ApiError(400, "Звук таймера: укажите on — true или false.")
        with self.lock:
            now = self.clock()
            self._advance(now)
            st = self.st
            if act == "start" and not st["running"]:
                st.update(running=True, startedAt=now, waiting=None)  # «Время вышло» → следующий отрезок с этой минуты
            elif act == "extend" and st["waiting"]:  # «Ещё 5 минут» закончившегося отрезка; повторное нажатие — без второго продления
                st.update(phase=st["waiting"]["from"], running=True, startedAt=now, elapsed=0.0, extra=POMO_EXTRA, waiting=None)
            elif act == "pause" and st["running"]:
                st.update(elapsed=self._done(now), running=False, startedAt=None)
            elif act == "reset":
                if st["phase"] == "long":
                    st["cycle"] = 0  # длинный перерыв пропущен — новый круг из помидоров
                st.update(phase="work", running=False, startedAt=None, elapsed=0.0, waiting=None, extra=None)
            elif act == "sound":
                st["sound"] = body["on"]
            elif act == "task":
                st["task"] = tid
            elif act == "settings":
                st["durations"].update(dur)
                if self._done(now) > self._dur():  # укоротили посреди отрезка — отрезок кончается сейчас (со звуком), а не задним числом
                    if st["running"]:
                        st["startedAt"] = now - (self._dur() - st["elapsed"])
                    else:
                        st["elapsed"] = float(self._dur())
            st["seq"] += 1
            self._save_logged()
            self._advance(now)  # новая длительность могла закончить текущий отрезок
            return self._public(now)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "MoyDen/1.0"
    protocol_version = "HTTP/1.1"

    # pythonw: stderr нет, поэтому пишем в журнал, а не в консоль
    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        log.warning("http: " + fmt, *args)

    @property
    def app(self):
        return self.server.app

    def common_headers(self):
        # страницу нельзя встроить в чужой сайт (кликджекинг)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def send_json(self, status, obj, etag=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.common_headers()
        if etag:
            self.send_header("ETag", etag)
        if status >= 400:
            self.close_connection = True  # тело запроса могло остаться непрочитанным
            self.send_header("Connection", "close")
            self._linger = self.body_unread()  # тогда закрытие «мягкое» (finish)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, ctype):
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            return self.send_json(404, {"ok": False, "error": "нет файла"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.common_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def write_ok(self):
        # защита от чужих сайтов в браузере: запись только с этой страницы или без Origin (curl, клиент API)
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            return False
        return self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"

    def body_unread(self):
        """у запроса есть тело, а сервер его не прочитал (ответ — ошибка до чтения)."""
        if getattr(self, "_body_read", False):
            return False
        try:
            return int(self.headers.get("Content-Length") or 0) != 0
        except (TypeError, ValueError, AttributeError):
            return True

    def finish(self):
        linger, self._linger = getattr(self, "_linger", False), False
        try:
            super().finish()
        finally:
            if linger:
                drain_after_error(self.connection)

    def read_body(self, limit=MAX_BODY):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Неверная длина запроса")
        if n < 0:
            raise ApiError(400, "Неверная длина запроса")
        if n > limit:
            raise ApiError(413, "Слишком большой запрос: не больше " + (f"{limit // 1024 // 1024} МБ" if limit >= 1 << 20 else f"{limit // 1024} КБ"))
        raw = self.rfile.read(n) if n else b"{}"
        self._body_read = True  # 
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "Тело запроса — не JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "Тело запроса должно быть объектом JSON")
        return body

    def dispatch(self, method):
        self._body_read = False  # соединение может нести несколько запросов
        try:
            if not self.host_ok():
                return self.send_json(403, {"ok": False, "error": "Неверный адрес"})
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            if method == "GET":
                return self.route_get(path, parse_qs(parts.query))
            if not self.write_ok():
                return self.send_json(403, {"ok": False, "error": "Запись только с JSON и с этой страницы"})
            body = self.read_body(MAX_RESTORE if (method, path) == ("POST", "/api/tasks") else MAX_BODY) if method in ("POST", "PATCH", "DELETE") else {}
            from_page = self.headers.get("Origin") is not None or self.headers.get("Sec-Fetch-Site") == "same-origin"
            by = "owner" if from_page else "bot"
            if path in ("/api/app/show", "/api/app/quit", "/api/app/wallpaper-focus", "/api/app/wallpaper-release") and method == "POST":
                return self.app_control(path, body)
            if path == "/api/pomodoro" and method == "POST":  # таймер — свой файл, не data.json
                return self.send_json(200, self.app.pomo_command(body))
            if path == "/api/calendars/connect" and method == "POST":
                return self.send_json(200, {"ok": True, "item": self.app.calendars.connect(body)})
            em = re.fullmatch(r"/api/employees(?:/([^/]+))?(?:/notes(?:/([^/]+))?)?", path)
            cm = re.fullmatch(r"/api/tasks/([^/]+)/comments(?:/([^/]+))?", path)
            rm = re.fullmatch(r"/api/reminders(?:/([^/]+))?", path)
            if path == "/api/event-links" and method == "POST":
                fn = lambda d: set_event_link(d, body)
            elif cm:
                tid, cid = cm.groups()
                if (method == "POST") == (cid is not None):
                    raise ApiError(405, "Это действие здесь не поддерживается")
                fn = lambda d: comment_op(d, tid, cid, method, body, from_page)
            elif rm:
                rid = rm.group(1)
                if (method == "POST") == (rid is not None):
                    raise ApiError(405, "Это действие здесь не поддерживается")
                fn = lambda d: reminder_op(d, rid, method, body)
            elif em and "/notes" in path:
                eid, nid = em.groups()
                if eid is None or (method == "POST") == (nid is not None):
                    raise ApiError(405, "Это действие здесь не поддерживается")
                fn = lambda d: note_op(d, eid, nid, method, body)
            elif em:
                eid = em.group(1)
                fn = {("POST", False): lambda d: create_employee(d, body),
                      ("PATCH", True): lambda d: patch_employee(d, eid, body),
                      ("DELETE", True): lambda d: delete_employee(d, eid, body)}.get((method, eid is not None))
                if fn is None:
                    raise ApiError(405, "Это действие здесь не поддерживается")
            else:
                m = re.fullmatch(r"/api/(tasks|projects)(?:/([^/]+))?", path)
                if not m:
                    raise ApiError(404, "Нет такого адреса")
                kind, item_id = m.groups()
                fn = {
                    ("tasks", "POST", False): lambda d: create_task(d, body),
                    ("tasks", "PATCH", True): lambda d: patch_task(d, item_id, body),
                    ("tasks", "DELETE", True): lambda d: delete_task(d, item_id),
                    ("projects", "POST", False): lambda d: create_project(d, body),
                    ("projects", "PATCH", True): lambda d: patch_project(d, item_id, body, by),
                }.get((kind, method, item_id is not None))
                if fn is None:
                    raise ApiError(405, "Это действие здесь не поддерживается")
            doc, etag, item = self.app.store.mutate(fn)
            self.send_json(201 if method == "POST" else 200, {"ok": True, "item": item, "data": doc, "etag": etag}, etag)
        except ApiError as e:
            self.send_json(e.status, {"ok": False, "error": e.message})
        except Exception:
            log.exception("request failed: %s %s", method, urlsplit(self.path).path)
            self.send_json(500, {"ok": False, "error": "Внутренняя ошибка сервера, подробности в server.log"})

    def app_control(self, path, body=None):
        ui = getattr(self.app, "ui", None)
        if path in ("/api/app/wallpaper-focus", "/api/app/wallpaper-release"):  # клавиатура обоям и обратно
            if not ui or not hasattr(ui, "wallpaper_focus"):
                raise ApiError(409, "«Мой день» работает без окна: обоев нет")
            if path.endswith("release"):
                return self.send_json(200, {"ok": True, "released": bool(ui.wallpaper_release())})
            fid, hsh = (body or {}).get("fid", ""), (body or {}).get("hash", "")
            fid = fid if isinstance(fid, str) and re.fullmatch(r"[\w:.\-]{0,80}", fid) else ""  # неверное — пусто: фокус всё равно отдаём
            hsh = hsh if isinstance(hsh, str) and len(hsh) <= 200 and hsh.startswith("#") else ""
            return self.send_json(200, {"ok": True, "focus": ui.wallpaper_focus(fid, hsh)})
        if path == "/api/app/show":
            if not ui:
                raise ApiError(409, "«Мой день» работает без окна: показывать нечего")
            ui.show()
            return self.send_json(200, {"ok": True})
        if ui:
            raise ApiError(409, "Сервер работает внутри приложения «Мой день»; закройте его через значок в трее")
        threading.Thread(target=self.server.shutdown, daemon=True).start()  # приложение забирает работу себе
        return self.send_json(200, {"ok": True})

    def route_get(self, path, query):
        if path in ("/", "/index.html"):
            return self.send_file(self.app.html, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self.send_file(self.app.icon, "image/x-icon")
        if path == "/api/ping":
            return self.send_json(200, {"ok": True, "app": APP, "pid": os.getpid(), "schema": SCHEMA,
                                        "ui": bool(getattr(self.app, "ui", None)), "aumid": getattr(self.app, "aumid", None)})
        if path == "/api/settings":
            # weather.on — страница не держит место под погоду, если она выключена (те же правила, что у сервиса погоды)
            try:
                n = len(self.app.weather.cities())
            except Exception as e:  # сбой разбора настроек погоды не роняет /api/settings: место под погоду держим
                log.warning("weather settings: %s", e)
                n = None
            return self.send_json(200, dict(public_settings(self.app.weather.settings_path), weather={"on": n != 0, "n": n}))
        if path == "/api/data":
            doc, raw = self.app.store.load()
            etag = etag_of(raw)
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.send_json(200, doc, etag)
        if path == "/api/summary":
            doc, _ = self.app.store.load()
            return self.send_json(200, summary(doc))
        if path == "/api/pomodoro":
            return self.send_json(200, self.app.pomodoro.snapshot())
        if path == "/api/deadline-stats":  # «Статистика сроков» (страница и отчёт ~29 числа)
            try:
                d_from = date.fromisoformat(query.get("from", [""])[0])
                d_to = date.fromisoformat(query.get("to", [""])[0])
            except ValueError:
                raise ApiError(400, "Нужны from и to в виде ГГГГ-ММ-ДД")
            if d_from > d_to or (d_to - d_from).days > 400:
                raise ApiError(400, "Период — от одного дня до 400 дней, from не позже to")
            doc, _ = self.app.store.load()
            return self.send_json(200, deadline_stats(doc, d_from, d_to))
        if path == "/api/weather":
            return self.send_json(200, self.app.weather.snapshot())
        if path == "/api/calendars":
            return self.send_json(200, self.app.calendars.public())
        if path == "/api/events":
            try:
                d_from = date.fromisoformat(query.get("from", [""])[0])
                d_to = date.fromisoformat(query.get("to", [""])[0])
            except ValueError:
                raise ApiError(400, "Нужны from и to в виде ГГГГ-ММ-ДД")
            if not d_from < d_to or (d_to - d_from).days > 62:
                raise ApiError(400, "Диапазон — от 1 до 62 дней")
            project = query.get("project", [None])[0]
            return self.send_json(200, self.app.calendars.events(d_from, d_to, project))
        raise ApiError(404, "Нет такого адреса")

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # на Windows SO_REUSEADDR разрешил бы второй экземпляр на том же порту
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        log.exception("connection error")


class App:
    def __init__(self, args):
        g = lambda k, d: getattr(args, k, None) or d
        self.store = Store(Path(args.data))
        page = Path(PAGE_DIR) if PAGE_DIR else Path(__file__).resolve().parent
        self.html = page / "дашборд.html"  # страница — из папки программы, а не из папки данных 
        self.icon = page / "мой_день.ico"
        sync = getattr(args, "google_sync", None)
        sync = Path(sync) if sync else None  # синхронизация файлом — только по ключу --google-sync
        self.calendars = Calendars(Path(args.calendars), Path(args.cache), args.refresh, data_fn=self.store.load, sync_path=sync)
        self.weather = Weather(Path(g("settings", HERE / "settings.json")), Path(g("weather_cache", HERE / "кэш-погоды.json")),
                               url=g("weather_url", WEATHER_URL), refresh=g("weather_refresh", 1800),
                               retry=g("weather_retry", 120), max_age=g("weather_max_age", 3600))
        self.pomodoro = Pomodoro(Path(args.data).resolve().parent / "помидоро.json", data_fn=self.store.load)

    def pomo_notify(self, ev):
        """Конец отрезка таймера: звук и уведомление в трее (их показывает программа; сервер без окна молчит)."""
        ui = getattr(self, "ui", None)
        if not ui or not hasattr(ui, "pomodoro_alert"):
            return
        if not self.pomo_waiting(ev.get("at")):  # «Время вышло» уже подтвердили (до тика) — сигнал не подаём
            log.info("pomodoro alert skipped: «Время вышло» уже подтверждено")
            return
        title = None
        if ev.get("task") is not None:
            try:
                doc, _ = self.store.load()
                t = next((t for t in doc["tasks"] if isinstance(t, dict) and t.get("id") == ev["task"]), None)
                title = t["title"] if t and isinstance(t.get("title"), str) else None
            except Exception:  # data.json занят или не того вида — уведомление и звук всё равно, без названия задачи
                title = None
        ui.pomodoro_alert(pomodoro_message(ev, ev.get("durations") or self.pomodoro.st["durations"], title), sound=ev.get("sound", True) is not False,
                          still=lambda: self.pomo_waiting(ev.get("at")), title=("Время вышло: перерыв" if ev.get("from") == "work" else "Время вышло: работа"))

    def pomo_command(self, body):
        """команда таймера; «Время вышло» подтверждено (кнопкой в окне или на обоях) — тост с экрана убирает программа."""
        r = self.pomodoro.command(body)
        ui = getattr(self, "ui", None)
        if not r.get("waiting") and getattr(ui, "toast_shown", False) and hasattr(ui, "pomodoro_clear"):
            threading.Thread(target=ui.pomodoro_clear, name="pomo-toast-clear", daemon=True).start()
        if not r.get("waiting") and getattr(ui, "flash_shown", False) and hasattr(ui, "flash_restore"):  # окно — обратно в трей
            threading.Thread(target=ui.flash_restore, name="pomo-flash-restore", daemon=True).start()
        return r

    def pomo_waiting(self, at):
        """«Время вышло» этого конца отрезка ещё не подтверждено (для повтора уведомления, если тост Windows не показался)."""
        with self.pomodoro.lock:
            w = self.pomodoro.st.get("waiting")
        return bool(w) and at is not None and abs(w["at"] - at) < 1e-3


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сервер дашборда «Мой день»")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data", default=str(HERE / "data.json"))
    ap.add_argument("--calendars", default=str(HERE / "calendars.local.json"))
    ap.add_argument("--cache", default=str(HERE / "кэш-календаря"))
    ap.add_argument("--refresh", type=int, default=300, help="как часто обновлять календари, секунд")
    ap.add_argument("--settings", default=str(HERE / "settings.json"))
    ap.add_argument("--weather-url", default=WEATHER_URL)
    ap.add_argument("--weather-cache", default=str(HERE / "кэш-погоды.json"))
    ap.add_argument("--weather-refresh", type=int, default=1800)
    ap.add_argument("--weather-retry", type=int, default=120)
    ap.add_argument("--weather-max-age", type=int, default=3600)
    ap.add_argument("--log", default=str(HERE / "server.log"))
    ap.add_argument("--google-sync", default=None, help="файл google-events.json внешней синхронизации (по умолчанию — выключено)")
    args = ap.parse_args(argv)

    handler = RotatingFileHandler(args.log, maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    ensure_data(Path(args.data))
    app = App(args)
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        log.info("port %s busy: another instance is running, exit", args.port)
        return 3
    try:
        app.store.migrate_file()
    except (ApiError, OSError) as e:
        log.warning("migration skipped: %s", getattr(e, "message", type(e).__name__))  # битый файл не трогаем
    srv.app = app
    threading.Thread(target=app.calendars.run, name="calendars", daemon=True).start()
    threading.Thread(target=app.weather.run, name="weather", daemon=True).start()
    threading.Thread(target=app.pomodoro.run, args=(app.pomo_notify,), name="pomodoro", daemon=True).start()
    log.info("started pid=%s port=%s", os.getpid(), args.port)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
