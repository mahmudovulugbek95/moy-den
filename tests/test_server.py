"""Проверка сервера дашборда: API, запись на диск, защита, календарь iCal, погода, звезда, пауза, миграция.

Запуск (из папки репозитория):  python -m unittest tests.test_server
Работает на копии данных во временной папке, настоящий data.json не трогает.
"""
import http.server
import json
import os
import logging
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from argparse import Namespace
from pathlib import Path

os.environ["MOYDEN_TEST_ICAL"] = "1"  # тестовый календарь — на 127.0.0.1 (в программе запрещено, )
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import server as S  # noqa: E402

SECRET = "private-SECRET-KEY-7f3a9c"
ICS = (HERE / "test-calendar.ics").read_bytes()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietICS(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class MeteoStub(http.server.BaseHTTPRequestHandler):
    """Подмена Open-Meteo: mode = ok | bad | yesterday."""
    mode = "ok"
    calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        MeteoStub.calls.append(self.path)
        day = S.today() if MeteoStub.mode != "yesterday" else "2020-01-01"
        second = "latitude=35" in self.path  # второй город фикстуры (UTC+9)
        body = {"utc_offset_seconds": 32400 if second else 10800,
                "current": {"temperature_2m": 18.4 if second else 23.6, "weather_code": 61 if second else 0, "is_day": 1},
                "daily": {"time": [day], "weather_code": [61 if second else 2], "temperature_2m_max": [21.2 if second else 26.9],
                          "temperature_2m_min": [13.0 if second else 14.1], "precipitation_probability_max": [70 if second else 0]}}
        raw = b"<html>oops" if MeteoStub.mode == "bad" else json.dumps(body).encode()
        if second and MeteoStub.mode == "ok":  # у Токио своё «сегодня» (UTC+9)
            from datetime import datetime, timedelta, timezone
            body["daily"]["time"] = [datetime.now(timezone(timedelta(hours=9))).date().isoformat()]
            raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="moyden-"))
        shutil.copyfile(HERE / "fixture-data.json", self.tmp / "data.json")  # своя фикстура: правки пользователя в data.json тесты не ломают
        # календарь «Google» — локальный сервер с секретом в пути, как у настоящего адреса
        (self.tmp / "ics" / SECRET).mkdir(parents=True)
        (self.tmp / "ics" / SECRET / "basic.ics").write_bytes(ICS)
        self.ics_port = free_port()
        ics_dir = str(self.tmp / "ics")
        self.ics = http.server.ThreadingHTTPServer(("127.0.0.1", self.ics_port),
                                                   lambda *a, **k: QuietICS(*a, directory=ics_dir, **k))
        threading.Thread(target=self.ics.serve_forever, daemon=True).start()
        self.write_cfg(f"http://127.0.0.1:{self.ics_port}/{SECRET}/basic.ics")
        MeteoStub.mode, MeteoStub.calls = "ok", []
        self.meteo_port = free_port()
        self.meteo = http.server.ThreadingHTTPServer(("127.0.0.1", self.meteo_port), MeteoStub)
        threading.Thread(target=self.meteo.serve_forever, daemon=True).start()
        shutil.copyfile(HERE / "fixture-settings.json", self.tmp / "settings.json")
        self.log_path = self.tmp / "server.log"
        self.handler = logging.FileHandler(self.log_path, encoding="utf-8")
        S.log.addHandler(self.handler)
        S.log.setLevel(logging.INFO)
        self.port = free_port()
        args = Namespace(data=str(self.tmp / "data.json"), calendars=str(self.tmp / "calendars.local.json"),
                         cache=str(self.tmp / "cache"), refresh=300, settings=str(self.tmp / "settings.json"),
                         weather_url=f"http://127.0.0.1:{self.meteo_port}/v1/forecast",
                         weather_cache=str(self.tmp / "weather.json"), weather_refresh=1800, weather_retry=120, weather_max_age=3600,
                         google_sync=str(self.tmp / "google-events.json"))
        self.args = args
        self.app = S.App(args)
        self.srv = S.Server(("127.0.0.1", self.port), S.Handler)
        self.srv.app = self.app
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for s in (self.ics, self.meteo):
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
        S.log.removeHandler(self.handler)
        self.handler.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_cfg(self, url, name="Рабочий"):
        cfg = {"calendars": [{"name": name, "color": "#4a6cf7", "ical_url": url},
                             {"name": "Личный", "color": "#0b8f7f", "ical_url": ""}]}
        (self.tmp / "calendars.local.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")

    def req(self, method, path, body=None, headers=None, raw=None):
        h = {"Content-Type": "application/json"} if body is not None or method in ("POST", "PATCH", "DELETE") else {}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                txt = resp.read().decode()
                return resp.status, (json.loads(txt) if txt else None), resp.headers
        except urllib.error.HTTPError as e:
            txt = e.read().decode()
            return e.code, (json.loads(txt) if txt else None), e.headers

    def data(self):
        return json.loads((self.tmp / "data.json").read_text("utf-8"))


class DataApi(Base):
    def test_get_and_etag(self):
        st, doc, h = self.req("GET", "/api/data")
        self.assertEqual(st, 200)
        self.assertEqual(len(doc["projects"]), 7)
        self.assertEqual(sum(1 for t in doc["tasks"] if t.get("example")), 3)
        st, _, _ = self.req("GET", "/api/data", headers={"If-None-Match": h["ETag"]})
        self.assertEqual(st, 304)

    def test_task_lifecycle(self):
        before = self.data()
        st, res, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "  Проверить макеты  ", "who": "Нина", "due": "2026-10-28"})
        self.assertEqual(st, 201)
        t = res["item"]
        self.assertEqual(t["id"], max(x["id"] for x in before["tasks"]) + 1)
        self.assertEqual(t["title"], "Проверить макеты")
        self.assertEqual(t["state"], "work")
        tid = t["id"]
        st, res, _ = self.req("PATCH", f"/api/tasks/{tid}", {"time": "15:30", "state": "wait"})
        self.assertEqual((res["item"]["time"], res["item"]["state"]), ("15:30", "wait"))
        st, res, _ = self.req("PATCH", f"/api/tasks/{tid}", {"state": "done"})
        self.assertEqual(res["item"]["done"], S.today())
        self.assertEqual(self.data()["tasks"][-1]["state"], "done")  # на диске
        st, res, _ = self.req("PATCH", f"/api/tasks/{tid}", {"state": "work"})
        self.assertNotIn("done", res["item"])
        st, res, _ = self.req("PATCH", f"/api/tasks/{tid}", {"due": "", "dueReason": {"code": "priority"}})  # снятие срока — с причиной
        self.assertNotIn("time", res["item"], "время без даты удаляется")
        saved = res["item"]
        st, res, _ = self.req("DELETE", f"/api/tasks/{tid}")
        self.assertEqual(st, 200)
        self.assertFalse(any(x["id"] == tid for x in self.data()["tasks"]))
        st, res, _ = self.req("POST", "/api/tasks", saved)  # «Вернуть»
        self.assertEqual(res["item"]["id"], tid)
        after = {t["id"]: t for t in self.data()["tasks"]}
        for old in before["tasks"]:  # прежние поля на месте (миграция только добавляет whoId)
            self.assertEqual({k: after[old["id"]].get(k) for k in old}, old)

    def test_next_id_never_reused(self):
        """номер удалённой задачи не достаётся новой."""
        top = max(t["id"] for t in self.data()["tasks"])
        self.req("DELETE", f"/api/tasks/{top}")
        st, res, _ = self.req("POST", "/api/tasks", {"project": "zeta", "title": "Новая"})
        self.assertEqual(st, 201)
        self.assertEqual(res["item"]["id"], top + 1, "max+1 после удаления дал бы старый номер")
        self.assertEqual(self.data()["nextId"], top + 2)
        st, res, _ = self.req("POST", "/api/tasks", {"project": "zeta", "title": "Чужой номер", "id": top + 1})
        self.assertNotEqual(res["item"]["id"], top + 1, "занятый номер не выдаётся")

    def test_bot_edit_during_write_is_not_lost(self):
        """бот записал файл между чтением и записью сервера — правка клиента API остаётся."""
        calls = []

        def fn(doc):
            if not calls:
                ext = self.data()
                ext["tasks"].append({"id": 600, "project": "zeta", "title": "Правка бота", "who": "", "due": "", "state": "work"})
                (self.tmp / "data.json").write_text(json.dumps(ext, ensure_ascii=False), "utf-8")
            calls.append(1)
            S.find(doc["projects"], "portal", "Проект")["note"] = "правка страницы"
        self.app.store.mutate(fn)
        d = self.data()
        self.assertEqual(len(calls), 2, "правка применена повторно к свежим данным")
        self.assertTrue(any(t["id"] == 600 for t in d["tasks"]), "правка клиента API потеряна")
        self.assertEqual(S.find(d["projects"], "portal", "")["note"], "правка страницы")

    def test_projects(self):
        st, res, _ = self.req("POST", "/api/projects", {"name": "Новый ЖК"})
        self.assertEqual(st, 201)
        p = res["item"]
        self.assertEqual((p["id"], p["status"], p["stage"]), ("p1", "ok", ""))
        st, res, _ = self.req("PATCH", "/api/projects/p1", {"status": "risk", "owner": "Влад", "deadline": "2026-12-01"})
        self.assertEqual(res["item"]["status"], "risk")
        st, res, _ = self.req("PATCH", "/api/projects/p1", {"aliases": "Новый жилой, НЖК"})
        self.assertEqual(res["item"]["aliases"], ["Новый жилой", "НЖК"])
        st, res, _ = self.req("POST", "/api/tasks", {"project": "p1", "title": "Первый шаг"})
        self.assertEqual(st, 201)

    def test_star(self):
        st, res, _ = self.req("PATCH", "/api/tasks/1", {"star": True})
        self.assertEqual((st, res["item"]["star"]), (200, True))
        self.assertTrue(next(t for t in self.data()["tasks"] if t["id"] == 1)["star"])
        st, res, _ = self.req("PATCH", "/api/tasks/1", {"star": "да"})
        self.assertEqual(st, 400, "звезда — только true/false")
        st, res, _ = self.req("PATCH", "/api/tasks/1", {"star": False})
        self.assertNotIn("star", res["item"])

    def test_pause_project_and_history(self):
        st, res, _ = self.req("PATCH", "/api/projects/lumen", {"pause": {"reason": "ждём заказчика", "until": "2026-11-01"}})
        self.assertEqual(st, 200)
        p = res["item"]
        self.assertEqual(p["pause"], {"since": S.today(), "reason": "ждём заказчика", "until": "2026-11-01"})
        self.assertEqual(p["status"], "ok", "статус не затирается паузой")
        st, res, _ = self.req("PATCH", "/api/projects/lumen", {"pause": {"reason": "ждём аванс"}})
        self.assertEqual((res["item"]["pause"]["reason"], res["item"]["pause"]["since"]), ("ждём аванс", S.today()))
        st, res, _ = self.req("PATCH", "/api/projects/lumen", {"pause": None})
        p = res["item"]
        self.assertNotIn("pause", p)
        self.assertEqual(p["pauseHistory"], [{"from": S.today(), "to": S.today(), "reason": "ждём аванс"}])
        st, res, _ = self.req("PATCH", "/api/projects/lumen", {"pause": {"since": "2026-10-20", "until": "2026-10-10"}})
        self.assertEqual(st, 400, "«вернуть до» раньше начала")
        st, res, _ = self.req("PATCH", "/api/projects/lumen", {"status": "pause"})  # старый способ клиента API
        self.assertEqual((res["item"]["status"], bool(res["item"].get("pause"))), ("ok", True))

    def test_pause_task(self):
        st, res, _ = self.req("PATCH", "/api/tasks/2", {"pause": True})
        self.assertEqual(res["item"]["pause"]["since"], S.today())
        st, res, _ = self.req("PATCH", "/api/tasks/2", {"pause": False})
        self.assertNotIn("pause", res["item"])
        self.assertEqual(len(res["item"]["pauseHistory"]), 1)
        st, _, _ = self.req("PATCH", "/api/tasks/2", {"pause": "да"})
        self.assertEqual(st, 400)

    def test_summary_excludes_pause(self):
        day = "2026-10-23"
        d0 = S.summary(self.data(), day)
        self.assertEqual((d0["late"], d0["today"]), ([90, 2, 3], [1]))  # 90 — пример с просрочкой 21.10
        self.req("PATCH", "/api/tasks/2", {"star": True})
        self.req("PATCH", "/api/tasks/1", {"star": True})
        self.req("PATCH", "/api/tasks/92", {"star": True})
        d1 = S.summary(self.data(), day)
        self.assertEqual(d1["priority"], [2, 1, 92], "просроченная сверху, затем по сроку, без срока в конце")
        self.req("PATCH", "/api/projects/kedr", {"pause": {}})   # задача 3 — в проекте на паузе
        self.req("PATCH", "/api/tasks/2", {"pause": {}})          # задача 2 — сама на паузе
        d2 = S.summary(self.data(), day)
        self.assertEqual(d2["late"], [90], "задачи на паузе не просрочены")
        self.assertEqual(d2["priority"], [1, 92], "звезда на паузе не в «Приоритетных»")
        self.assertEqual(d2["paused"], {"projects": ["kedr"], "tasks": [2]})
        self.assertEqual((d2["counts"]["pausedProjects"], d2["counts"]["pausedTasks"]), (1, 1))
        self.req("PATCH", "/api/tasks/1", {"state": "done"})
        self.assertEqual(S.summary(self.data(), day)["priority"], [92], "выполненная исчезает")
        st, res, _ = self.req("GET", "/api/summary")
        self.assertEqual(st, 200)
        self.assertIn("counts", res)

    def test_migration_lossless(self):
        old = self.data()
        old.pop("nextId", None)
        old.pop("schema", None)
        old["projects"][1]["status"] = "pause"
        old["projects"][0]["свое_поле"] = {"a": 1}
        old["tasks"][0]["заметка_бота"] = "x"
        raw = json.dumps(old, ensure_ascii=False, indent=1).encode("utf-8")
        (self.tmp / "data.json").write_bytes(raw)
        self.assertTrue(self.app.store.migrate_file())
        new = self.data()
        backup = self.tmp / "резерв" / f"data.json.до-схемы-{S.SCHEMA}-{S.today()}"
        self.assertEqual(backup.read_bytes(), raw, "копия до миграции — байт в байт")
        for a, b in zip(old["tasks"], new["tasks"]):
            self.assertEqual({k: b.get(k) for k in a}, a, "задачи без потерь")
        for a, b in zip(old["employees"], new["employees"]):
            self.assertEqual({k: b.get(k) for k in a}, a, "сотрудники без потерь")
        for a, b in zip(old["projects"], new["projects"]):
            for k, v in a.items():
                if k != "status":
                    self.assertEqual(b[k], v, k)
        self.assertEqual((new["projects"][1]["status"], new["projects"][1]["pause"]["since"]), ("ok", S.today()))
        self.assertEqual((new["schema"], new["nextId"]), (S.SCHEMA, max(t["id"] for t in old["tasks"]) + 1))
        self.assertFalse(self.app.store.migrate_file(), "повторная миграция ничего не меняет")

    def test_validation(self):
        cases = [
            ({"project": "portal", "title": "  "}, 400),
            ({"project": "portal", "title": "x", "due": "2026-13-01"}, 400),
            ({"project": "portal", "title": "x", "due": "23.10.2026"}, 400),
            ({"project": "portal", "title": "x", "state": "maybe"}, 400),
            ({"project": "nope", "title": "x"}, 404),
            ({"project": "portal", "title": "x" * 301}, 400),
            ({"project": "portal", "title": "x", "due": "2026-10-23", "time": "25:00"}, 400),
            ({"project": "portal", "title": "x", "star": 1}, 400),
        ]
        for body, code in cases:
            st, res, _ = self.req("POST", "/api/tasks", body)
            self.assertEqual(st, code, body)
            self.assertFalse(res["ok"])
            self.assertTrue(res["error"])
        # POST /api/tasks — до 4 МБ («Вернуть»), предел 64 КБ — у правок. Разбор 1.3.12 (п5, WinError 10053): сервер отвечает 413 по заявленной
        # длине, не читая тело, и закрывает соединение; уже отправленное тело остаётся непрочитанным → Windows рвёт соединение сбросом, ответ иногда
        # теряется (и на 1.3.11). Поэтому тело не шлём: 413 должен прийти по одному заголовку — сервер, ждущий тело, не ответил бы за 10 с.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(f"PATCH /api/tasks/2 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: {S.MAX_BODY + 10}\r\n\r\n".encode())
            buf = b""
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        self.assertEqual(lines[0].split(" ")[1], "413", lines[0])
        self.assertIn("connection: close", [x.lower() for x in lines[1:]])
        res = json.loads(body.decode("utf-8"))
        self.assertFalse(res["ok"])
        self.assertIn("64 КБ", res["error"])
        st, _, _ = self.req("PATCH", "/api/tasks/99999", {"title": "x"})
        self.assertEqual(st, 404)
        st, _, _ = self.req("PATCH", "/api/projects/portal", {"status": "maybe"})
        self.assertEqual(st, 400)

    def test_negative_content_length(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(f"POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: -5\r\n\r\n".encode())
            self.assertIn(b"400", s.recv(200).split(b"\r\n")[0], "отрицательная длина не вешает поток")

    def test_security(self):
        st, _, _ = self.req("GET", "/api/data", headers={"Host": "evil.example:8699"})
        self.assertEqual(st, 403, "DNS rebinding: чужой Host")
        st, _, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403, "чужой сайт не пишет")
        st, _, _ = self.req("POST", "/api/tasks", None, headers={"Content-Type": "text/plain"}, raw=b'{"project":"portal","title":"x"}')
        self.assertEqual(st, 403, "простой форм-запрос без JSON не пишет")
        st, _, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403, "запрос с чужого сайта")
        st, _, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "x"}, headers={"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(st, 201)
        r = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10)
        self.assertEqual(r.headers["X-Frame-Options"], "DENY", "страницу нельзя встроить во фрейм")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        for path in ("/data.json", "/calendars.local.json", "/server.py", "/../data.json", "/settings.json"):
            st, _, _ = self.req("GET", path)
            self.assertEqual(st, 404, path)

    def test_atomic_write_and_backups(self):
        prev = (self.tmp / "data.json").read_bytes()
        self.req("POST", "/api/tasks", {"project": "portal", "title": "a"})
        self.assertEqual((self.tmp / "data.json.bak").read_bytes(), prev)
        snap = self.tmp / "резерв" / f"data-{S.today()}.json"
        self.assertEqual(snap.read_bytes(), prev, "дневной резерв = состояние до первой правки")
        prev2 = (self.tmp / "data.json").read_bytes()
        self.req("POST", "/api/tasks", {"project": "portal", "title": "b"})
        self.assertEqual((self.tmp / "data.json.bak").read_bytes(), prev2)
        self.assertEqual(snap.read_bytes(), prev, "дневной резерв не перезаписывается")
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])
        self.assertEqual(self.data()["updated"], S.today())

    def test_corrupted_file_is_not_overwritten(self):
        bad = b'{"projects": [ broken'
        (self.tmp / "data.json").write_bytes(bad)
        st, res, _ = self.req("GET", "/api/data")
        self.assertEqual(st, 500)
        self.assertIn("повреждён", res["error"])
        self.assertIn("резерв", res["error"], "совет упоминает дневной резерв")
        st, res, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "x"})
        self.assertEqual(st, 500)
        self.assertEqual((self.tmp / "data.json").read_bytes(), bad)

    def test_external_edit_visible(self):
        _, _, h = self.req("GET", "/api/data")
        doc = self.data()
        doc["tasks"].append({"id": 500, "project": "zeta", "title": "Правка бота", "who": "Нина", "due": "", "state": "wait"})
        (self.tmp / "data.json").write_text(json.dumps(doc, ensure_ascii=False), "utf-8-sig")  # с BOM, как пишет Блокнот
        st, doc2, h2 = self.req("GET", "/api/data", headers={"If-None-Match": h["ETag"]})
        self.assertEqual(st, 200)
        self.assertTrue(any(t["id"] == 500 for t in doc2["tasks"]))

    def test_single_instance(self):
        with self.assertRaises(OSError):
            S.Server(("127.0.0.1", self.port), S.Handler)


class CalendarIcs(Base):
    def ev(self, d1, d2, extra=""):
        st, res, _ = self.req("GET", f"/api/events?from={d1}&to={d2}{extra}")
        self.assertEqual(st, 200)
        return res

    def test_expansion(self):
        self.app.calendars.refresh_all()
        res = self.ev("2026-10-01", "2026-12-01")
        info = res["calendars"][0]
        self.assertTrue(info["configured"])
        self.assertIsNone(info["error"])
        self.assertFalse(res["calendars"][1]["configured"])
        by = {}
        for e in res["events"]:
            by.setdefault(e["title"], []).append(e)
        weekly = [(e["start"], e["end"]) for e in by["Стендап"]]
        self.assertEqual([s for s, _ in weekly], ["2026-10-05T10:00", "2026-10-12T10:00", "2026-11-02T10:00",
                                                 "2026-11-09T10:00", "2026-11-16T10:00", "2026-11-23T10:00"])
        self.assertNotIn("2026-10-19T10:00", [s for s, _ in weekly], "EXDATE исключён")
        moved = by["Стендап (перенесён)"]
        self.assertEqual([(e["start"], e["end"]) for e in moved], [("2026-10-26T12:00", "2026-10-26T13:30")])
        bd = by["День рождения Нины"]
        self.assertEqual([(e["start"], e["end"], e["allDay"]) for e in bd], [("2026-10-24", "2026-10-25", True)])
        night = by["Ночной деплой"][0]
        self.assertEqual((night["start"], night["end"]), ("2026-10-23T23:00", "2026-10-24T02:00"))
        self.assertEqual((by["Созвон с Токио"][0]["start"], by["Созвон с Токио"][0]["end"]), ("2026-10-23T11:00", "2026-10-23T12:00"), "UTC → Найроби")
        self.assertEqual(by["Звонок из Лагоса"][0]["start"], "2026-10-22T17:00", "Москва +3 → Найроби +5")
        self.assertEqual(by["Обед"][0]["start"], "2026-10-23T13:00", "время без пояса = местное")
        self.assertNotIn("Отменено", by)
        self.assertNotIn("<br>", by["Стендап"][0]["description"])
        self.assertIn("https://meet.example.com/abc", by["Стендап"][0]["description"])
        self.assertEqual(by["Стендап"][0]["location"], "Офис")
        self.assertEqual(by["Созвон с фрилансером"][0]["description"], "По проекту Кедр: 5 < 6 > 3", "«<» и «>» не теги")
        res = self.ev("2027-10-01", "2027-11-01")
        self.assertIn(("2027-10-24", True), [(e["start"], e["allDay"]) for e in res["events"] if e["title"] == "День рождения Нины"])

    def test_day_windows(self):
        self.app.calendars.refresh_all()
        titles = lambda d1, d2: sorted(e["title"] for e in self.ev(d1, d2)["events"])
        self.assertEqual(titles("2026-10-24", "2026-10-25"), ["День рождения Нины", "Ночной деплой"], "через полночь виден и на следующий день")
        self.assertIn("Ночной деплой", titles("2026-10-23", "2026-10-24"))
        self.assertNotIn("Ночной деплой", titles("2026-10-25", "2026-10-26"))
        self.assertEqual(titles("2026-10-19", "2026-10-20"), [], "в день исключения пусто")

    def test_edge_cases(self):
        """конец ровно в 00:00, весь день без конца, конец раньше начала."""
        self.app.calendars.refresh_all()
        titles = lambda d1, d2: sorted(e["title"] for e in self.ev(d1, d2)["events"])
        self.assertIn("До полуночи", titles("2026-11-03", "2026-11-04"))
        self.assertNotIn("До полуночи", titles("2026-11-04", "2026-11-05"), "22:00–00:00 не попадает на следующий день")
        allday = [e for e in self.ev("2026-11-05", "2026-11-06")["events"] if e["title"] == "Весь день без конца"]
        self.assertEqual([(e["start"], e["end"]) for e in allday], [("2026-11-05", "2026-11-06")])
        self.assertEqual(titles("2026-11-06", "2026-11-07"), ["Конец раньше начала"])
        rev = self.ev("2026-11-06", "2026-11-07")["events"][0]
        # библиотека повторов сама меняет местами перепутанные начало и конец; главное — событие не пропало и не «отрицательное»
        self.assertTrue(rev["start"].startswith("2026-11-06") and rev["start"] <= rev["end"], rev)

    def test_project_links(self):
        """П.7: в проект — только явно названное; сомнительное не привязывается; ручная отвязка переживает обновление."""
        self.req("PATCH", "/api/projects/panda", {"aliases": ["Ред Панда", "Рэд Панда"]})
        self.app.calendars.refresh_all()
        by = {e["title"]: e for e in self.ev("2026-10-01", "2026-11-01")["events"]}
        self.assertEqual((by["Встреча Ред Панда"]["project"], by["Встреча Ред Панда"]["link"]), ("panda", "auto"), "синоним из проекта")
        self.assertEqual(by["Созвон с фрилансером"]["project"], "kedr", "название проекта в описании")
        self.assertIsNone(by["Купить кеды"]["project"], "«кеды» и «Кедровый» — не «Кедр»")
        self.assertIsNone(by["Стендап (перенесён)"]["project"], "событие без проекта остаётся только в календаре")
        self.assertIsNone(by["Сверка: Магазин Портал и LUMEN"]["project"], "два проекта — сомнительно, не привязываем")
        self.assertIsNone(by["День рождения Нины"]["project"])
        uid = by["Встреча Ред Панда"]["uid"]
        st, res, _ = self.req("POST", "/api/event-links", {"uid": uid, "project": ""})
        self.assertEqual(st, 201)
        self.app.calendars.refresh_all()  # «обновление iCal»
        panda = [e for e in self.ev("2026-10-01", "2026-11-01")["events"] if e["title"] == "Встреча Ред Панда"][0]
        self.assertEqual((panda["project"], panda["link"]), (None, "off"), "ручная отвязка сохранилась")
        st, _, _ = self.req("POST", "/api/event-links", {"uid": "weekly-1@test", "project": "portal"})
        evs = self.ev("2026-10-01", "2026-11-01", "&project=portal")["events"]
        self.assertEqual(sorted({e["title"] for e in evs}), ["Стендап", "Стендап (перенесён)"], "ручная привязка — на всю серию")
        self.assertTrue(all(e["link"] == "manual" for e in evs))
        st, _, _ = self.req("POST", "/api/event-links", {"uid": uid, "project": "nope"})
        self.assertEqual(st, 404)
        self.assertNotIn("tasks", [k for k in self.data().get("eventLinks", {})], "задачи из событий сами не создаются")
        self.assertEqual(len(self.data()["tasks"]), 6)

    def test_network_failure_keeps_cache(self):
        self.app.calendars.refresh_all()
        good = self.ev("2026-10-01", "2026-11-01")
        self.ics.shutdown()
        self.ics.server_close()
        self.app.calendars.refresh_all()
        res = self.ev("2026-10-01", "2026-11-01")
        info = res["calendars"][0]
        self.assertEqual(info["error"], "нет связи с Google Календарём")
        self.assertTrue(info["updated"])
        self.assertEqual(len(res["events"]), len(good["events"]), "показан кэш, а не пустота")

    def test_stale_cache_removed(self):
        """календарь переименован/удалён — его кэш стирается с диска."""
        self.app.calendars.refresh_all()
        self.assertEqual(len(list((self.tmp / "cache").glob("*.ics"))), 1)
        self.write_cfg("", name="Рабочий")
        self.app.calendars.refresh_all()
        self.assertEqual(list((self.tmp / "cache").glob("*.ics")), [])

    def test_not_found_and_not_calendar(self):
        self.write_cfg(f"http://127.0.0.1:{self.ics_port}/{SECRET}/missing.ics")
        self.app.calendars.refresh_all()
        self.assertIn("404", self.ev("2026-10-01", "2026-11-01")["calendars"][0]["error"])
        (self.tmp / "ics" / SECRET / "page.html").write_text("<html>login</html>", "utf-8")
        self.write_cfg(f"http://127.0.0.1:{self.ics_port}/{SECRET}/page.html")
        self.app.calendars.refresh_all()
        self.assertIn("не календарь", self.ev("2026-10-01", "2026-11-01")["calendars"][0]["error"])

    def test_bad_scheme_is_not_read(self):
        self.write_cfg((self.tmp / "data.json").as_uri())
        self.app.calendars.refresh_all()
        res = self.ev("2026-10-01", "2026-11-01")
        self.assertEqual(res["calendars"][0]["error"], "адрес должен начинаться с https://")
        self.assertEqual(res["events"], [])

    def test_not_configured(self):
        self.write_cfg("")
        res = self.ev("2026-10-01", "2026-11-01")
        self.assertEqual([c["configured"] for c in res["calendars"]], [False, False])
        self.assertEqual(res["events"], [])

    def test_secret_never_leaks(self):
        self.app.calendars.refresh_all()
        self.ics.shutdown()
        self.ics.server_close()
        self.app.calendars.refresh_all()  # ошибка тоже пишется в журнал
        for path in ("/api/events?from=2026-10-01&to=2026-11-01", "/api/data", "/", "/api/summary"):
            r = urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10).read().decode()
            self.assertNotIn(SECRET, r, path)
        self.handler.flush()
        log_text = self.log_path.read_text("utf-8")
        self.assertIn("Рабочий", log_text)
        self.assertNotIn(SECRET, log_text)

    def test_range_limits(self):
        st, _, _ = self.req("GET", "/api/events?from=2026-10-01&to=2027-10-01")
        self.assertEqual(st, 400)
        st, _, _ = self.req("GET", "/api/events?from=bad&to=2026-10-01")
        self.assertEqual(st, 400)


class WeatherApi(Base):
    def weather(self):
        st, res, _ = self.req("GET", "/api/weather")
        self.assertEqual(st, 200)
        return res

    def test_weather_ok_with_stub(self):
        self.assertEqual(self.weather(), {"ok": False, "cities": []}, "до первого ответа блок скрыт")
        self.assertTrue(self.app.weather.refresh_all())
        w = self.weather()
        self.assertTrue(w["ok"])
        self.assertEqual([c["name"] for c in w["cities"]], ["Найроби"], "Токио выключен по умолчанию")
        c = w["cities"][0]
        self.assertEqual((c["now"], c["min"], c["max"], c["precip"], c["code"], c["isDay"]), (24, 14, 27, 0, 0, True))
        self.assertIn("timezone=Africa%2FNairobi", MeteoStub.calls[0])
        self.assertIn("latitude=-1.2921", MeteoStub.calls[0])

    def test_second_city_option(self):
        cfg = json.loads((self.tmp / "settings.json").read_text("utf-8"))
        cfg["weather"]["cities"][1]["enabled"] = True
        (self.tmp / "settings.json").write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        self.app.weather.refresh_all()
        w = self.weather()
        self.assertEqual([c["name"] for c in w["cities"]], ["Найроби", "Токио"])
        self.assertEqual((w["cities"][1]["now"], w["cities"][1]["precip"]), (18, 70))

    def test_settings_weather_on(self):
        """/api/settings говорит странице, выключена ли погода (тогда место в шапке не держится)."""
        self.assertIs(self.req("GET", "/api/settings")[1]["weather"]["on"], True, "по умолчанию Найроби включён")
        self.assertEqual(self.req("GET", "/api/settings")[1]["weather"]["n"], 1, "число включённых городов")
        p = self.tmp / "settings.json"
        cfg = json.loads(p.read_text("utf-8"))
        for c in cfg["weather"]["cities"]:
            c["enabled"] = False
        p.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        self.assertIs(self.req("GET", "/api/settings")[1]["weather"]["on"], False, "ни одного включённого города")
        self.assertEqual(self.req("GET", "/api/settings")[1]["weather"]["n"], 0)
        cfg["weather"]["cities"][0]["enabled"] = True
        cfg["weather"]["enabled"] = False
        p.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        self.assertIs(self.req("GET", "/api/settings")[1]["weather"]["on"], False, "weather.enabled: false")
        cfg["weather"]["enabled"] = True
        p.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        self.assertIs(self.req("GET", "/api/settings")[1]["weather"]["on"], True, "снова включена")
        cfg["weather"]["cities"][1]["enabled"] = True
        p.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
        self.assertEqual(self.req("GET", "/api/settings")[1]["weather"]["n"], 2, "два города — страница держит место выше")

    def test_network_failure_hides_block(self):
        self.app.weather.refresh_all()
        self.meteo.shutdown()
        self.meteo.server_close()
        self.assertFalse(self.app.weather.refresh_all(), "обрыв — ошибка, а не молчание")
        self.assertTrue(self.weather()["ok"], "свежий прогноз (< 60 мин) ещё показывается")
        later = self.app.weather.snapshot(now=time.time() + 3700)
        self.assertEqual(later, {"ok": False, "cities": []}, "нет связи больше часа — блок прячется")
        self.handler.flush()
        self.assertIn("нет связи с сервером погоды", self.log_path.read_text("utf-8"))

    def test_bad_answer_and_old_date(self):
        MeteoStub.mode = "bad"
        self.assertFalse(self.app.weather.refresh_all())
        self.assertFalse(self.weather()["ok"])
        MeteoStub.mode = "yesterday"
        self.app.weather.refresh_all()
        self.assertFalse(self.weather()["ok"], "прогноз не на сегодня не показывается")

    def test_cache_survives_restart(self):
        self.app.weather.refresh_all()
        w2 = S.Weather(Path(self.args.settings), Path(self.args.weather_cache), url="http://127.0.0.1:9/none")
        self.assertTrue(w2.snapshot()["ok"], "после перезапуска сервера прогноз из кэша")


class Employees(Base):
    def emp(self, name):
        return next(e for e in self.data()["employees"] if e.get("firstName", e.get("name")) == name)

    def test_migration_of_old_employees(self):
        self.app.store.migrate_file()
        d = self.data()
        self.assertEqual([(e["id"], e["firstName"], e["lastName"]) for e in d["employees"]],
                         [(1, "Борис", ""), (2, "Нина", ""), (3, "Денис", ""), (4, "Влад", "")])
        self.assertEqual(self.emp("Нина")["role"], "менеджер проектов")
        self.assertEqual(next(t for t in d["tasks"] if t["id"] == 2)["whoId"], 2, "задачи связаны с сотрудником")
        self.assertEqual(d["nextEmployeeId"], 5)

    def test_add_edit_and_name_everywhere(self):
        st, res, _ = self.req("POST", "/api/employees", {"firstName": "Егор", "role": "архитектор"})
        self.assertEqual(st, 400, "фамилия обязательна")
        st, res, _ = self.req("POST", "/api/employees", {"firstName": "Егор", "lastName": "Соколов", "office": "Найроби",
                                                          "contacts": {"telegram": "@egor", "email": "a@example.com"}})
        self.assertEqual(st, 201)
        e = res["item"]
        self.assertEqual((e["id"], e["name"], e["archived"]), (5, "Егор Соколов", False))
        st, res, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "Иконки", "whoId": 5})
        self.assertEqual(res["item"]["who"], "Егор Соколов")
        st, res, _ = self.req("POST", "/api/tasks", {"project": "portal", "title": "Бот назвал по имени", "who": "Егор"})
        self.assertEqual(res["item"].get("whoId"), 5, "бот пишет имя — сервер находит сотрудника")
        self.req("PATCH", "/api/employees/2", {"lastName": "Белова"})
        self.assertEqual(next(t for t in self.data()["tasks"] if t["id"] == 2)["who"], "Нина Белова")
        st, res, _ = self.req("PATCH", "/api/employees/5", {"office": "Москва"})  # офис — свободный текст (до 40 знаков)
        self.assertEqual((st, res["item"]["office"]), (200, "Москва"))
        st, _, _ = self.req("PATCH", "/api/employees/5", {"office": "О" * 41})
        self.assertEqual(st, 400)
        st, _, _ = self.req("PATCH", "/api/employees/5", {"contacts": {"email": "не-почта"}})
        self.assertEqual(st, 400)

    def test_delete_requires_choice_then_reassign_archive_restore(self):
        st, res, _ = self.req("DELETE", "/api/employees/2")
        self.assertEqual(st, 409, "FAIL-сценарий: у Нины открытые задачи, без выбора удалить нельзя")
        self.assertFalse(self.emp("Нина").get("archived"))
        st, res, _ = self.req("DELETE", "/api/employees/2", {"reassign": 2})
        self.assertEqual(st, 400, "нельзя переназначить на самого себя")
        self.req("PATCH", "/api/tasks/2", {"state": "done"})  # выполненная хранит имя
        st, res, _ = self.req("DELETE", "/api/employees/2", {"reassign": 4})
        self.assertEqual((st, res["item"]["archived"]), (200, True))
        d = self.data()
        t90 = next(t for t in d["tasks"] if t["id"] == 90)
        self.assertEqual((t90["whoId"], t90["who"]), (4, "Влад"), "открытая задача переназначена")
        t2 = next(t for t in d["tasks"] if t["id"] == 2)
        self.assertEqual((t2["who"], t2["whoId"]), ("Нина", 2), "история выполненных сохраняет имя")
        st, _, _ = self.req("POST", "/api/tasks", {"project": "zeta", "title": "x", "whoId": 2})
        self.assertEqual(st, 400, "на архивного не назначить")
        st, _, _ = self.req("POST", "/api/tasks", {"project": "zeta", "title": "x", "who": "Нина"})
        self.assertEqual(st, 400, "и по имени на архивного не назначить")
        st, res, _ = self.req("PATCH", "/api/employees/2", {"archived": False})
        self.assertFalse(res["item"]["archived"], "возврат из архива")
        st, res, _ = self.req("DELETE", "/api/employees/1", {"reassign": "none"})
        self.assertEqual(st, 200)
        self.assertTrue(all(t.get("who") == "" for t in self.data()["tasks"] if t["id"] in (1, 3)), "без исполнителя")

    def test_notes(self):
        st, res, _ = self.req("POST", "/api/employees/3/notes", {"text": "Спросить про отпуск"})
        self.assertEqual(st, 201)
        nid = res["item"]["id"]
        self.assertTrue(res["item"]["date"].startswith(S.today()))
        st, _, _ = self.req("POST", "/api/employees/3/notes", {"text": "  "})
        self.assertEqual(st, 400)
        self.req("PATCH", f"/api/employees/3/notes/{nid}", {"text": "Отпуск в декабре"})
        self.assertEqual(self.emp("Денис")["notes"][0]["text"], "Отпуск в декабре")
        self.req("DELETE", f"/api/employees/3/notes/{nid}")
        self.assertEqual(self.emp("Денис")["notes"], [])


class AppControl(Base):
    def test_show_quit_settings(self):
        st, _, _ = self.req("POST", "/api/app/show", {})
        self.assertEqual(st, 409, "без окна показывать нечего")
        self.app.ui = type("U", (), {"shown": False, "show": lambda s: setattr(s, "shown", True)})()
        st, _, _ = self.req("POST", "/api/app/show", {})
        self.assertTrue(st == 200 and self.app.ui.shown)
        st, _, _ = self.req("POST", "/api/app/quit", {})
        self.assertEqual(st, 409, "при открытом окне сервер не останавливается")
        st, _, _ = self.req("POST", "/api/app/quit", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403, "чужой сайт не останавливает сервер")
        self.app.ui = None
        st, _, _ = self.req("POST", "/api/app/quit", {})
        self.assertEqual(st, 200)
        time.sleep(1.5)
        with self.assertRaises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ping", timeout=2)


class Reminders(Base):
    def test_reminders_crud_and_validation(self):
        st, res, _ = self.req("POST", "/api/reminders", {"text": "Позвонить подрядчику", "date": "2026-10-23", "time": "09:15", "project": "portal"})
        self.assertEqual(st, 201)
        r = res["item"]
        self.assertEqual((r["id"], r["time"], r["project"], r["done"]), (1, "09:15", "portal", False))
        for body, code in (({"text": "", "date": "2026-10-23", "time": "09:00"}, 400), ({"text": "x", "date": "2026-10-23", "time": "9"}, 400),
                           ({"text": "x", "date": "", "time": "09:00"}, 400), ({"text": "x", "date": "2026-10-23", "time": "09:00", "project": "nope"}, 404)):
            st, _, _ = self.req("POST", "/api/reminders", body)
            self.assertEqual(st, code, body)
        st, res, _ = self.req("PATCH", "/api/reminders/1", {"time": "10:00", "project": None})
        self.assertEqual(res["item"]["time"], "10:00")
        self.assertNotIn("project", res["item"])
        self.assertEqual(self.data()["reminders"][0]["text"], "Позвонить подрядчику", "хранится в data.json")
        self.req("DELETE", "/api/reminders/1")
        self.assertEqual(self.data()["reminders"], [])
        st, res, _ = self.req("POST", "/api/reminders", {"text": "Второе", "date": "2026-10-24", "time": "18:00"})
        self.assertEqual(res["item"]["id"], 2, "номер удалённого напоминания не переиспользуется")

    def test_task_hour_setting(self):
        st, res, _ = self.req("GET", "/api/settings")
        self.assertEqual((res["calendar"]["taskHour"], res["theme"]), ("18:00", "light"))


class Comments(Base):
    """комментарии к задаче. FAIL-сценарии: пустой не сохраняется, удаление и «Вернуть», перезапуск, HTML как текст."""
    def page(self):
        return {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"}

    def comments(self, tid):
        return next(t for t in self.data()["tasks"] if t["id"] == tid).get("comments", [])

    def test_add_author_and_empty_fail(self):
        for text in ("", "   ", "\n\t"):
            st, res, _ = self.req("POST", "/api/tasks/2/comments", {"text": text}, headers=self.page())
            self.assertEqual(st, 400, f"FAIL: пустой комментарий {text!r} не сохраняется")
            self.assertIn("Текст", res["error"])
        self.assertEqual(self.comments(2), [], "на диске пусто")
        st, res, _ = self.req("POST", "/api/tasks/2/comments", {"text": "  Позвонила заказчику  "}, headers=self.page())
        self.assertEqual(st, 201)
        c = res["item"]
        self.assertEqual((c["id"], c["text"], c["author"]), (1, "Позвонила заказчику", "owner"), "со страницы — пользователь")
        self.assertRegex(c["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
        self.assertTrue(c["at"].startswith(S.today()))
        st, res, _ = self.req("POST", "/api/tasks/2/comments", {"text": "Напомнил Нины"})
        self.assertEqual(res["item"]["author"], "bot", "без адреса страницы (curl клиента API) — бот")
        st, res, _ = self.req("POST", "/api/tasks/2/comments", {"text": "x", "author": "кто-то"})
        self.assertEqual(st, 400)
        st, _, _ = self.req("POST", "/api/tasks/99999/comments", {"text": "x"})
        self.assertEqual(st, 404)
        st, _, _ = self.req("POST", "/api/tasks/2/comments", {"text": "x" * 2001})
        self.assertEqual(st, 400)
        self.assertEqual([c["text"] for c in self.comments(2)], ["Позвонила заказчику", "Напомнил Нины"])

    def test_edit_delete_restore_and_ids_never_reused(self):
        ids = [self.req("POST", "/api/tasks/3/comments", {"text": t}, headers=self.page())[1]["item"]["id"] for t in ("первый", "второй")]
        self.assertEqual(ids, [1, 2])
        st, res, _ = self.req("PATCH", "/api/tasks/3/comments/2", {"text": "второй, исправлен"})
        self.assertEqual((res["item"]["text"], bool(res["item"].get("edited"))), ("второй, исправлен", True))
        st, _, _ = self.req("PATCH", "/api/tasks/3/comments/2", {"text": " "})
        self.assertEqual(st, 400, "FAIL: правка в пустой не сохраняется")
        self.assertEqual(self.comments(3)[1]["text"], "второй, исправлен")
        saved = self.comments(3)[1]
        st, _, _ = self.req("DELETE", "/api/tasks/3/comments/2")
        self.assertEqual(st, 200)
        self.assertEqual([c["id"] for c in self.comments(3)], [1])
        st, res, _ = self.req("POST", "/api/tasks/3/comments", {"text": "новый"}, headers=self.page())
        self.assertEqual(res["item"]["id"], 3, "номер удалённого комментария не достаётся новому")
        st, res, _ = self.req("POST", "/api/tasks/3/comments", saved, headers=self.page())  # «Вернуть»
        self.assertEqual((res["item"]["id"], res["item"]["at"], res["item"]["text"]), (2, saved["at"], "второй, исправлен"))
        self.assertEqual([c["id"] for c in self.comments(3)], [1, 2, 3], "вернулся на своё место по времени")
        st, res, _ = self.req("POST", "/api/tasks/3/comments", {**saved, "text": "дубль"})
        self.assertNotEqual(res["item"]["id"], 2, "занятый номер второй раз не выдаётся")
        st, _, _ = self.req("DELETE", "/api/tasks/3/comments/777")
        self.assertEqual(st, 404)
        # задача удалена вместе с комментариями → «Вернуть» возвращает и их
        task = next(t for t in self.data()["tasks"] if t["id"] == 3)
        self.req("DELETE", "/api/tasks/3")
        st, res, _ = self.req("POST", "/api/tasks", task)
        self.assertEqual([c["id"] for c in res["item"]["comments"]], [c["id"] for c in task["comments"]])

    def test_survives_server_restart(self):
        self.req("POST", "/api/tasks/90/comments", {"text": "до перезапуска"}, headers=self.page())
        self.srv.shutdown()
        self.srv.server_close()
        self.app = S.App(self.args)  # новый процесс сервера на тех же данных
        self.srv = S.Server(("127.0.0.1", self.port), S.Handler)
        self.srv.app = self.app
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        st, doc, _ = self.req("GET", "/api/data")
        t = next(x for x in doc["tasks"] if x["id"] == 90)
        self.assertEqual([c["text"] for c in t["comments"]], ["до перезапуска"])
        self.req("DELETE", "/api/tasks/90/comments/1")
        st, res, _ = self.req("POST", "/api/tasks/90/comments", {"text": "после"})
        self.assertEqual(res["item"]["id"], 2, "счётчик nextCommentId пережил перезапуск")
        self.assertNotIn("comments", next(x for x in self.data()["tasks"] if x["id"] == 1), "у задач без комментариев поля нет")

    def test_html_is_stored_as_text(self):
        evil = '<img src=x onerror="alert(1)"> & <b>жирный</b>'
        st, res, _ = self.req("POST", "/api/tasks/2/comments", {"text": evil})
        self.assertEqual(res["item"]["text"], evil, "сервер хранит текст как есть; страница выводит его через esc()")
        raw = (self.tmp / "data.json").read_text("utf-8")
        self.assertIn('onerror=\\"alert(1)\\"', raw, "в JSON экранировано кавычками, а не вырезано")


class Milestones(Base):
    """даты проекта (срок и этапы) — для календаря."""

    def test_valid_sorted_and_cleared(self):
        st, res, _ = self.req("PATCH", "/api/projects/portal", {"deadline": "2026-11-30", "milestones": [
            {"title": "Сдача макета", "date": "2026-11-20"}, {"title": "Встреча с клиентом", "date": "2026-11-05", "time": "15:00"}]})
        self.assertEqual(st, 200)
        ms = res["item"]["milestones"]
        self.assertEqual([m["title"] for m in ms], ["Встреча с клиентом", "Сдача макета"], "по дате")
        self.assertEqual(ms[0]["time"], "15:00")
        self.assertNotIn("time", ms[1])
        for bad in ([{"title": "", "date": "2026-11-05"}], [{"title": "Без даты", "date": ""}], [{"title": "x", "date": "05.11.2026"}],
                    [{"title": "x", "date": "2026-11-05", "time": "15"}], ["строка"], {"title": "не список"}):
            st, res, _ = self.req("PATCH", "/api/projects/portal", {"milestones": bad})
            self.assertEqual(st, 400, bad)
        self.assertEqual(len(next(p for p in self.data()["projects"] if p["id"] == "portal")["milestones"]), 2, "плохая правка ничего не меняет")
        self.req("PATCH", "/api/projects/portal", {"milestones": []})
        self.assertNotIn("milestones", next(p for p in self.data()["projects"] if p["id"] == "portal"))


class CalendarConnect(Base):
    """подключение Google Календаря со страницы — адрес проверяется, наружу показывается только маска."""

    def cfg(self):
        return json.loads((self.tmp / "calendars.local.json").read_text("utf-8"))

    def test_connect_check_mask_disconnect(self):
        self.write_cfg("", name="Рабочий")
        good = f"http://127.0.0.1:{self.ics_port}/{SECRET}/basic.ics"
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": "ftp://example.com/a.ics"})
        self.assertEqual(st, 400, "FAIL: не https")
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": f"http://127.0.0.1:{self.ics_port}/{SECRET}/nope.ics"})
        self.assertEqual(st, 400, "FAIL: по адресу ничего нет")
        self.assertEqual(self.cfg()["calendars"][0]["ical_url"], "", "плохой адрес не записан")
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Нет такого", "url": good})
        self.assertEqual(st, 404)
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": good}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403, "чужой сайт не подменит адрес календаря")
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": good})
        self.assertEqual(st, 200)
        self.assertEqual(self.cfg()["calendars"][0]["ical_url"], good)
        self.assertEqual(res["item"]["address"], "127.0.0.1/…/basic.ics")
        st, pub, _ = self.req("GET", "/api/calendars")
        self.assertEqual([(c["name"], c["connected"]) for c in pub["calendars"]], [("Рабочий", True), ("Личный", False)])
        self.assertNotIn(SECRET, json.dumps(pub, ensure_ascii=False), "секрет не показывается после сохранения")
        self.assertNotIn(SECRET, json.dumps(res, ensure_ascii=False))
        self.assertNotIn(SECRET, self.log_path.read_text("utf-8"), "и не пишется в журнал")
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": ""})
        self.assertEqual((st, res["item"]["connected"]), (200, False))
        self.assertEqual(self.cfg()["calendars"][0]["ical_url"], "")


class LowRound2(Base):
    def test_garbage_item_does_not_break_writes(self):
        """строка вместо объекта в списке от клиента API не роняет правки."""
        d = self.data()
        d["tasks"].append("мусор")
        d["employees"].append(42)
        (self.tmp / "data.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        st, _, _ = self.req("PATCH", "/api/tasks/2", {"title": "Разбор референсов, обновлено"})
        self.assertEqual(st, 200)
        st, _, _ = self.req("POST", "/api/tasks", {"project": "zeta", "title": "новая"})
        self.assertEqual(st, 201)
        st, _, _ = self.req("POST", "/api/employees", {"firstName": "Егор", "lastName": "Соколов"})
        self.assertEqual(st, 201)
        st, _, _ = self.req("POST", "/api/employees/3/notes", {"text": "заметка"})
        self.assertEqual(st, 201)

    def test_empty_state_counts_as_work(self):
        """state "" — «в работе» и на странице, и в /api/summary."""
        d = self.data()
        t = next(x for x in d["tasks"] if x["id"] == 92)
        t["state"] = ""
        (self.tmp / "data.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        st, s, _ = self.req("GET", "/api/summary")
        self.assertIn(92, s["noDue"])

    def test_note_id_not_reused(self):
        """номер удалённой последней заметки не достаётся новой."""
        a = self.req("POST", "/api/employees/3/notes", {"text": "раз"})[1]["item"]["id"]
        b = self.req("POST", "/api/employees/3/notes", {"text": "два"})[1]["item"]["id"]
        self.req("DELETE", f"/api/employees/3/notes/{b}")
        c = self.req("POST", "/api/employees/3/notes", {"text": "три"})[1]["item"]["id"]
        self.assertEqual((a, b, c), (1, 2, 3))

    def test_second_migration_same_day_keeps_both_backups(self):
        """вторая миграция за день не затирает копию первой."""
        old = json.loads((HERE / "fixture-old-data.json").read_text("utf-8"))
        for _ in range(2):
            (self.tmp / "data.json").write_text(json.dumps(old, ensure_ascii=False), "utf-8")
            self.assertTrue(self.app.store.migrate_file())
        self.assertEqual(len(list((self.tmp / "резерв").glob(f"data.json.до-схемы-{S.SCHEMA}-*"))), 2)



class GoogleSync(Base):
    """События Google из файла синхронизации: чтение, битый файл, без конца, весь день, обновление при изменении."""

    def write_sync(self, events, synced=None, raw=None):
        from datetime import datetime as dt
        doc = {"synced_at": synced or dt.now(S.TZ).isoformat(timespec="seconds"),
               "calendars": [{"id": "work", "name": "Рабочий", "color": "#E8833A"}], "events": events}
        f = self.tmp / "google-events.json"
        f.write_text(raw if raw is not None else json.dumps(doc, ensure_ascii=False), "utf-8")

    def ev(self):
        return self.req("GET", f"/api/events?from={S.today()}&to={(S.date.fromisoformat(S.today()) + S.timedelta(days=1)).isoformat()}")[1]

    def test_read_timed_allday_no_end_meet_and_project(self):
        d = S.today()
        self.write_sync([
            {"id": "a1", "calendar": "work", "title": "Напоминание: встреча по брендбуку", "start": f"{d}T15:00:00+03:00", "end": f"{d}T15:30:00+03:00", "allDay": False,
             "meet": "https://meet.google.com/abc-defg-hij"},
            {"id": "a2", "calendar": "work", "title": "Встреча Ред Панда", "start": f"{d}T09:00:00+03:00", "allDay": False},
            {"id": "a3", "calendar": "work", "title": "День рождения", "start": d, "allDay": True},
            {"id": "a4", "calendar": "nope", "title": "чужой календарь", "start": f"{d}T10:00:00+03:00", "allDay": False},
            {"id": "a5", "calendar": "work", "title": "плохая ссылка", "start": f"{d}T11:00:00+03:00", "end": f"{d}T11:30:00+03:00", "meet": "javascript:alert(1)"}])
        res = self.ev()
        by = {e["uid"]: e for e in res["events"]}
        self.assertEqual((by["a1"]["start"][11:], by["a1"]["end"][11:], by["a1"]["meet"]), ("15:00", "15:30", "https://meet.google.com/abc-defg-hij"))
        self.assertEqual(by["a2"]["start"], by["a2"]["end"], "событие без конца — точка во времени")
        self.assertEqual(by["a2"]["project"], "panda", "привязка к проекту по синониму (правило )")
        self.assertTrue(by["a3"]["allDay"])
        self.assertEqual(by["a3"]["end"], (S.date.fromisoformat(d) + S.timedelta(days=1)).isoformat(), "весь день без конца — один день")
        self.assertNotIn("a4", by)
        self.assertEqual(by["a5"]["meet"], "", "в ссылку Meet пропускается только meet.google.com")
        sync_cal = next(c for c in res["calendars"] if c.get("source") == "sync")
        self.assertEqual((sync_cal["name"], sync_cal["stale"]), ("Рабочий", False))
        self.assertFalse(any(c["name"] == "Рабочий" and c.get("source") != "sync" for c in res["calendars"]), "iCal того же календаря не дублирует")

    def test_broken_file_does_not_crash_and_stale_mark(self):
        self.write_sync([], raw="{ это не json")
        st, res, _ = self.req("GET", f"/api/events?from={S.today()}&to=2099-01-01")
        self.assertEqual(st, 400)  # диапазон > 62 дней — своя ошибка, сервер жив
        res = self.ev()
        self.assertTrue(any(c.get("error") == "файл синхронизации с Google не читается" for c in res["calendars"]))
        self.assertTrue(any(c["name"] == "Рабочий" and c.get("source") != "sync" for c in res["calendars"]), "битый файл — работает запасной iCal")
        self.write_sync([], synced="2020-01-01T10:00:00+03:00")
        res = self.ev()
        self.assertTrue(next(c for c in res["calendars"] if c.get("source") == "sync")["stale"], "старше двух часов — пометка")

    def test_reloads_when_file_changes(self):
        d = S.today()
        self.write_sync([{"id": "x", "calendar": "work", "title": "Первое", "start": f"{d}T12:00:00+03:00", "end": f"{d}T13:00:00+03:00"}])
        self.assertEqual([e["title"] for e in self.ev()["events"] if e["uid"] == "x"], ["Первое"])
        time.sleep(0.05)
        self.write_sync([{"id": "x", "calendar": "work", "title": "Перенесено", "start": f"{d}T16:00:00+03:00", "end": f"{d}T17:00:00+03:00"}])
        e = next(e for e in self.ev()["events"] if e["uid"] == "x")
        self.assertEqual((e["title"], e["start"][11:]), ("Перенесено", "16:00"))



class FinishProject(Base):
    """завершить проект. FAIL: с открытыми задачами без выбора действия не проходит."""

    def proj(self, pid):
        return next(p for p in self.data()["projects"] if p["id"] == pid)

    def test_finish_requires_choice_then_done_summary_and_reopen(self):
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "done"})
        self.assertEqual(st, 409, "FAIL-сценарий: открытые задачи без выбора — не завершается")
        self.assertIn("1 открытая задача", res["error"])
        self.assertEqual(self.proj("zeta")["status"], "ok", "ничего не изменилось")
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "done", "openTasks": "done", "doneAt": "2026-10-23", "doneNote": "Штурм проведён"},
                              headers={"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(st, 200)
        p = self.proj("zeta")
        self.assertEqual((p["status"], p["doneAt"], p["doneNote"], p["statusBefore"]), ("done", "2026-10-23", "Штурм проведён", "ok"))
        self.assertEqual((p["history"][-1]["action"], p["history"][-1]["by"]), ("finished", "owner"))
        t2 = next(t for t in self.data()["tasks"] if t["id"] == 2)
        self.assertEqual((t2["state"], t2["done"]), ("done", "2026-10-23"))
        st, s, _ = self.req("GET", "/api/summary")
        self.assertIn("zeta", s["finishedProjects"])
        self.assertNotIn(2, s["late"] + s["today"] + s["noDue"] + s["priority"])
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "ok"})  # без reopen — не возвращается
        self.assertEqual((st, self.proj("zeta")["status"]), (409, "done"))
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "ok", "reopen": True})  # клиент API: «верни проект в работу»
        p = self.proj("zeta")
        self.assertEqual(p["status"], "ok")
        self.assertEqual(next(t for t in self.data()["tasks"] if t["id"] == 2)["state"], "work", "задача снова открыта")
        self.assertNotIn("doneAt", p)
        self.assertEqual([h["action"] for h in p["history"]], ["finished", "reopened"])
        self.assertEqual(p["history"][-1]["by"], "bot")

    def test_keep_and_move_and_paused(self):
        self.req("PATCH", "/api/projects/kedr", {"pause": {"reason": "ждём"}})
        st, _, _ = self.req("PATCH", "/api/projects/kedr", {"status": "done", "openTasks": "keep"})
        self.assertEqual(st, 200)
        k = self.proj("kedr")
        self.assertNotIn("pause", k, "завершение снимает паузу")
        self.assertEqual(len(k["pauseHistory"]), 1)
        st, s, _ = self.req("GET", "/api/summary")
        self.assertNotIn("kedr", s["paused"]["projects"])
        self.assertNotIn(3, s["late"], "задача завершённого проекта, оставленная открытой, не горит")
        st, _, _ = self.req("PATCH", "/api/projects/portal", {"status": "done", "openTasks": "move", "moveTo": "kedr"})
        self.assertEqual(st, 400, "в завершённый проект не переносится")
        st, _, _ = self.req("PATCH", "/api/projects/portal", {"status": "done", "openTasks": "move", "moveTo": "panda"})
        self.assertEqual(st, 200)
        self.assertEqual(next(t for t in self.data()["tasks"] if t["id"] == 90)["project"], "panda")
        st, _, _ = self.req("POST", "/api/projects", {"name": "Новый", "status": "done"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
