"""плановый срок, история переносов с причиной, «вовремя / с опозданием», миграция (схема 3, бэкап, идемпотентность),
откат на 1.3.11, «Статистика сроков» (/api/deadline-stats), L4 «Удалить → Вернуть» с doneFrom и историей, L5 устаревшие поля после отката,
L10 .tmp после 409, цвет календаря — только #rrggbb с сервера.
Запуск: python -m unittest tests.test_1312"""
import importlib.util
import json
import os
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
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
os.environ.setdefault("MOYDEN_NO_TOAST", "1")  # тест не показывает уведомление на экране (страж в toast_run)
import server as S  # noqa: E402

D = lambda n: (date.fromisoformat(S.today()) + timedelta(days=n)).isoformat()  # дата относительно сегодня (Найроби)


def doc_with(*tasks):
    return {"projects": [{"id": "a", "name": "Альфа"}, {"id": "b", "name": "Бета"}],
            "employees": [{"id": 1, "firstName": "Нина", "lastName": "Белова"}, {"id": 2, "firstName": "Егор", "lastName": "Соколов"}],
            "tasks": list(tasks), "nextId": 100, "schema": S.SCHEMA}


def new_task(doc, **kw):
    return S.create_task(doc, {"project": "a", "title": "Макеты", "whoId": 1, **kw})


class Plan1312(unittest.TestCase):
    def test_first_due_is_plan_forever(self):
        doc = doc_with()
        t = new_task(doc, due=D(3))
        self.assertEqual(t.get("plan"), D(3), "FAIL первый срок не запомнен как плановый")
        S.patch_task(doc, t["id"], {"due": D(8), "dueReason": {"code": "client"}})
        S.patch_task(doc, t["id"], {"due": D(5)})
        self.assertEqual((t["due"], t["plan"]), (D(5), D(3)), "плановый не меняется при переносах")

    def test_first_due_set_later_is_plan_without_history(self):
        doc = doc_with()
        t = new_task(doc)
        S.patch_task(doc, t["id"], {"due": D(4)})
        self.assertEqual((t.get("plan"), t.get("dueHistory")), (D(4), None), "первый срок — не перенос")

    def test_later_without_reason_rejected(self):
        doc = doc_with()
        t = new_task(doc, due=D(3))
        with self.assertRaises(S.ApiError) as e:
            S.patch_task(doc, t["id"], {"due": D(9)})
        self.assertEqual(e.exception.status, 400)
        self.assertIn("причину", e.exception.message)
        self.assertEqual((t["due"], t.get("dueHistory")), (D(3), None), "без причины перенос не сохраняется")
        for bad in ({"code": "нет такой"}, {"code": "other"}, {"code": "other", "text": "   "}, 5):
            with self.assertRaises(S.ApiError, msg=str(bad)):
                S.patch_task(doc, t["id"], {"due": D(9), "dueReason": bad})
        S.patch_task(doc, t["id"], {"due": D(9), "dueReason": {"code": "other", "text": "заказчик в отпуске"}})
        h = t["dueHistory"][-1]
        self.assertEqual((h["from"], h["to"], h["reason"], h["text"], h["late"]), (D(3), D(9), "other", "заказчик в отпуске", False))
        self.assertRegex(h["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
        self.assertEqual(h["at"][:10], S.today())

    def test_overdue_flag_at_move(self):
        doc = doc_with()
        t = new_task(doc, due=D(-5))
        S.patch_task(doc, t["id"], {"due": D(2), "dueReason": {"code": "staff"}})
        self.assertIs(t["dueHistory"][-1]["late"], True, "срок уже прошёл в момент переноса — «после просрочки»")
        t2 = new_task(doc, due=D(0))
        S.patch_task(doc, t2["id"], {"due": D(2), "dueReason": "subs"})
        self.assertIs(t2["dueHistory"][-1]["late"], False, "срок сегодня — ещё не просрочен")

    def test_earlier_without_question_and_removal_needs_reason(self):
        doc = doc_with()
        t = new_task(doc, due=D(10))
        S.patch_task(doc, t["id"], {"due": D(4)})
        h = t["dueHistory"][-1]
        self.assertEqual((h["from"], h["to"], "reason" in h), (D(10), D(4), False))
        with self.assertRaises(S.ApiError):
            S.patch_task(doc, t["id"], {"due": ""})
        self.assertEqual(t["due"], D(4))
        S.patch_task(doc, t["id"], {"due": "", "dueReason": {"code": "priority"}})
        self.assertEqual((t["due"], t["dueHistory"][-1]["to"], t["dueHistory"][-1]["reason"], t["plan"]), ("", "", "priority", D(10)))
        S.patch_task(doc, t["id"], {"due": D(20)})  # срок снова поставлен: запись «без срока → дата», плановый прежний
        self.assertEqual((t["dueHistory"][-1]["from"], t["dueHistory"][-1]["to"], t["plan"]), ("", D(20), D(10)))
        self.assertEqual(len(t["dueHistory"]), 3)

    def test_same_due_and_time_change_not_recorded(self):
        doc = doc_with()
        t = new_task(doc, due=D(2))
        S.patch_task(doc, t["id"], {"due": D(2), "time": "15:00"})
        self.assertNotIn("dueHistory", t)

    def test_done_on_time_and_late_and_return(self):
        doc = doc_with()
        t = new_task(doc, due=D(-8))
        S.patch_task(doc, t["id"], {"state": "done"})
        self.assertEqual(t.get("doneLate"), 8, "выполнена через 8 дней после плана")
        S.patch_task(doc, t["id"], {"state": "work"})
        self.assertNotIn("doneLate", t, "«Вернуть» снимает отметку")
        t2 = new_task(doc, due=D(3))
        S.patch_task(doc, t2["id"], {"state": "done"})
        self.assertEqual(t2["doneLate"], 0, "раньше плана — вовремя")
        t3 = new_task(doc, due=D(-2))
        S.patch_task(doc, t3["id"], {"due": D(5), "dueReason": "client"})
        S.patch_task(doc, t3["id"], {"state": "done"})
        self.assertEqual(t3["doneLate"], 2, "опоздание — от планового срока, а не от перенесённого")
        t4 = new_task(doc)
        S.patch_task(doc, t4["id"], {"state": "done"})
        self.assertNotIn("doneLate", t4, "без срока — ни вовремя, ни с опозданием")

    def test_finish_project_marks_done_late(self):
        doc = doc_with()
        t = new_task(doc, due=D(-3), state="wait")
        S.finish_project(doc, doc["projects"][0], {"openTasks": "done"}, "owner")
        self.assertEqual((t["state"], t["doneLate"], t["doneFrom"]), ("done", 3, "wait"))
        S.reopen_tasks(doc, doc["projects"][0])
        self.assertEqual((t["state"], "doneLate" in t), ("wait", False))


class Migration1312(unittest.TestCase):
    def test_migrate_plan_done_late_idempotent(self):
        doc = doc_with({"id": 1, "project": "a", "title": "x", "due": "2026-10-20", "state": "work"},
                       {"id": 2, "project": "a", "title": "y", "due": "2026-10-10", "state": "done", "done": "2026-10-13"},
                       {"id": 3, "project": "a", "title": "z", "due": "", "state": "work"},
                       {"id": 4, "project": "a", "title": "старое", "due": "2026-10-25", "state": "work", "doneFrom": "wait", "doneLate": 4},
                       {"id": 5, "project": "a", "title": "битая дата", "due": "2026-13-45", "state": "work"})
        doc["schema"] = 2
        self.assertTrue(S.migrate(doc))
        ts = {t["id"]: t for t in doc["tasks"]}
        self.assertEqual(ts[1].get("plan"), "2026-10-20", "FAIL старым задачам плановый = текущий срок")
        self.assertNotIn("dueHistory", ts[1], "истории у старых задач нет")
        self.assertEqual((ts[2]["plan"], ts[2]["doneLate"]), ("2026-10-10", 3))
        self.assertNotIn("plan", ts[3])
        self.assertNotIn("plan", ts[5], "битая дата не становится плановой")
        self.assertEqual(("doneFrom" in ts[4], "doneLate" in ts[4]), (False, False), "L5: после отката поля выполнения у открытой задачи сняты")
        self.assertEqual(doc["schema"], 3)
        snap = json.dumps(doc, sort_keys=True)
        self.assertFalse(S.migrate(doc), "повторная миграция ничего не меняет")
        self.assertEqual(json.dumps(doc, sort_keys=True), snap)

    def test_migrate_file_backup_once(self):
        tmp = Path(tempfile.mkdtemp(prefix="moyden-1312-"))
        try:
            old = doc_with({"id": 1, "project": "a", "title": "x", "due": "2026-10-20", "state": "work"})
            old["schema"] = 2
            raw = json.dumps(old, ensure_ascii=False).encode("utf-8")
            (tmp / "data.json").write_bytes(raw)
            st = S.Store(tmp / "data.json")
            self.assertTrue(st.migrate_file())
            backups = list((tmp / "резерв").glob("data.json.до-схемы-3-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), raw, "бэкап до миграции — байт в байт")
            self.assertEqual(json.loads((tmp / "data.json").read_text("utf-8"))["tasks"][0]["plan"], "2026-10-20")
            self.assertFalse(st.migrate_file(), "второй запуск — без миграции и без второго бэкапа")
            self.assertEqual(len(list((tmp / "резерв").glob("data.json.до-схемы-*"))), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class Stats1312(unittest.TestCase):
    def doc(self):
        f = lambda **kw: {"project": "a", "state": "work", **kw}
        return doc_with(
            f(id=1, title="вовремя", whoId=1, plan="2026-10-10", due="2026-10-10", state="done", done="2026-10-09", doneLate=0),
            f(id=2, title="с опозданием", whoId=1, plan="2026-10-05", due="2026-10-12", state="done", done="2026-10-12", doneLate=7,
              dueHistory=[{"from": "2026-10-05", "to": "2026-10-12", "at": "2026-10-04T10:00", "reason": "client", "late": False}]),
            f(id=3, title="перенос после просрочки", whoId=2, project="b", plan="2026-10-01", due="2026-11-03",
              dueHistory=[{"from": "2026-10-01", "to": "2026-10-20", "at": "2026-10-03T09:00", "reason": "staff", "late": True},
                          {"from": "2026-10-20", "to": "2026-11-03", "at": "2026-10-21T09:00", "reason": "staff", "late": True},
                          {"from": "2026-11-03", "to": "2026-10-30", "at": "2026-10-22T09:00", "late": False},
                          {"from": "2026-10-30", "to": "2026-11-03", "at": "2026-09-30T09:00", "reason": "subs", "late": False}]),
            f(id=4, title="с опозданием 3", who="Подрядчик", project="b", plan="2026-10-01", due="2026-10-01", state="done", done="2026-10-04"),
            f(id=5, title="вне периода", whoId=1, plan="2026-09-01", due="2026-09-01", state="done", done="2026-09-02", doneLate=1),
            f(id=6, title="без срока", whoId=1, state="done", done="2026-10-15"),
            f(id=7, title="пример", whoId=1, example=True, plan="2026-10-01", due="2026-10-01", state="done", done="2026-10-30", doneLate=29))

    def test_numbers(self):
        r = S.deadline_stats(self.doc(), date(2026, 10, 1), date(2026, 10, 31))
        e = {x["name"]: x for x in r["employees"]}
        m = e["Нина Белова"]
        self.assertEqual((m["done"], m["withPlan"], m["onTime"], m["onTimePct"], m["lateDone"], m["avgDelay"]), (3, 2, 1, 50, 1, 7.0))
        self.assertEqual((m["moves"], m["movesEarly"], m["movesLate"]), (1, 1, 0))
        self.assertEqual(sorted(m["tasks"]), [1, 2, 6], "пример и задача вне периода не считаются")
        a = e["Егор Соколов"]
        self.assertEqual((a["done"], a["onTimePct"], a["moves"], a["movesLate"], a["movesEarly"]), (0, None, 2, 2, 0),
                         "приближение срока — не перенос; перенос в сентябре — вне периода")
        self.assertEqual(a["reasons"], [{"code": "staff", "label": "сотрудник не успел", "n": 2}])
        self.assertEqual(e["Подрядчик"]["avgDelay"], 3.0, "doneLate нет (выполнена старой версией) — считается от плана")
        p = {x["key"]: x for x in r["projects"]}
        self.assertEqual((p["p:a"]["done"], p["p:b"]["done"], p["p:b"]["moves"], p["p:b"]["name"]), (3, 1, 2, "Бета"))
        t = r["total"]
        self.assertEqual((t["done"], t["withPlan"], t["onTime"], t["onTimePct"], t["moves"], t["avgDelay"]), (4, 3, 1, 33, 3, 5.0))
        self.assertEqual([x["code"] for x in t["reasons"]], ["staff", "client"], "частые причины — по убыванию")

    def test_empty_period(self):
        r = S.deadline_stats(self.doc(), date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual((r["employees"], r["projects"], r["total"]["done"], r["total"]["onTimePct"]), ([], [], 0, None))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Http1312(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="moyden-1312h-"))
        shutil.copyfile(HERE / "fixture-data.json", self.tmp / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", self.tmp / "settings.json")
        (self.tmp / "calendars.local.json").write_text(json.dumps({"calendars": [
            {"name": "Работа", "color": "red;background:url(x)", "ical_url": ""}, {"name": "Личный", "color": "#0B8F7F", "ical_url": ""}]}), "utf-8")
        (self.tmp / "google-events.json").write_text(json.dumps({"synced_at": None, "events": [], "calendars": [
            {"id": "g1", "name": "Работа", "color": "#123\"><script>"}, {"id": "g2", "name": "Личный", "color": "#aabbcc"}]}), "utf-8")
        self.port = free_port()
        args = Namespace(data=str(self.tmp / "data.json"), calendars=str(self.tmp / "calendars.local.json"), cache=str(self.tmp / "cache"),
                         refresh=300, settings=str(self.tmp / "settings.json"), weather_url="http://127.0.0.1:9/none",
                         weather_cache=str(self.tmp / "weather.json"), weather_refresh=1800, weather_retry=120, weather_max_age=3600,
                         google_sync=str(self.tmp / "google-events.json"))
        self.app = S.App(args)
        self.srv = S.Server(("127.0.0.1", self.port), S.Handler)
        self.srv.app = self.app
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                   method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")

    def test_patch_later_without_reason_400_and_file_unchanged(self):
        st, res = self.req("POST", "/api/tasks", {"project": json.loads((self.tmp / "data.json").read_text("utf-8"))["projects"][0]["id"],
                                                  "title": "Проба", "due": D(2)})
        self.assertEqual(st, 201)
        tid = res["item"]["id"]
        before = (self.tmp / "data.json").read_bytes()
        st, res = self.req("PATCH", f"/api/tasks/{tid}", {"due": D(6)})
        self.assertEqual(st, 400, res)
        self.assertIn("dueReason", res["error"])
        self.assertEqual((self.tmp / "data.json").read_bytes(), before, "файл не тронут")
        st, res = self.req("PATCH", f"/api/tasks/{tid}", {"due": D(6), "dueReason": {"code": "scope"}})
        self.assertEqual((st, res["item"]["plan"], res["item"]["dueHistory"][0]["reason"]), (200, D(2), "scope"))

    def test_restore_deleted_keeps_history_and_done_from(self):
        """L4: «Удалить → Вернуть» выполненной из «ждём ответа» задачи — doneFrom, плановый срок и история на месте."""
        pid = json.loads((self.tmp / "data.json").read_text("utf-8"))["projects"][0]["id"]
        tid = self.req("POST", "/api/tasks", {"project": pid, "title": "Проба", "due": D(-3), "state": "wait"})[1]["item"]["id"]
        self.req("PATCH", f"/api/tasks/{tid}", {"due": D(1), "dueReason": "client"})
        copy = self.req("PATCH", f"/api/tasks/{tid}", {"state": "done"})[1]["item"]
        self.assertEqual((copy["doneFrom"], copy["doneLate"]), ("wait", 3))
        self.assertEqual(self.req("DELETE", f"/api/tasks/{tid}")[0], 200)
        st, res = self.req("POST", "/api/tasks", copy)
        self.assertEqual(st, 201, res)
        t = res["item"]
        self.assertEqual((t["id"], t.get("doneFrom"), t.get("plan"), len(t.get("dueHistory") or []), t.get("doneLate")),
                         (tid, "wait", D(-3), 1, 3), "FAIL doneFrom и история терялись")

    def test_stats_endpoint(self):
        st, res = self.req("GET", f"/api/deadline-stats?from={S.today()[:8]}01&to={S.today()}")
        self.assertEqual((st, res["ok"]), (200, True))
        self.assertIn("employees", res)
        for q in ("", "?from=2026-10-31&to=2026-10-01", "?from=2026-01-01&to=2027-12-31", "?from=x&to=y"):
            self.assertEqual(self.req("GET", "/api/deadline-stats" + q)[0], 400, q)

    def test_calendar_colors_only_hex(self):
        """Очередь цвет календаря проверяет сервер — в ответ уходит только #rrggbb (страница вставляет его в style)."""
        import re
        st, res = self.req("GET", "/api/calendars")
        self.assertEqual(st, 200)
        cols = [c["color"] for c in res["calendars"]]
        self.assertTrue(cols and all(re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in cols), cols)
        st, res = self.req("GET", f"/api/events?from={S.today()}&to={D(1)}")
        cols = [c.get("color") for c in res.get("calendars", [])]
        self.assertTrue(all(re.fullmatch(r"#[0-9a-fA-F]{6}", c or "") for c in cols), cols)

    def test_tmp_removed_after_lock_409(self):
        """L10: замок занят дольше 3 с — 409, а data.json.tmp не остаётся."""
        lock = self.tmp / "data.json.lock"
        stop = threading.Event()

        def hold():  # свежий замок держится: сервер не считает его забытым
            while not stop.is_set():
                lock.write_text("бот")
                time.sleep(0.5)
        th = threading.Thread(target=hold, daemon=True)
        th.start()
        try:
            st, res = self.req("PATCH", "/api/tasks/1", {"title": "занято"})
        finally:
            stop.set()
            th.join(2)
            lock.unlink(missing_ok=True)
        self.assertEqual(st, 409, res)
        self.assertFalse((self.tmp / "data.json.tmp").exists(), "FAIL недописанная копия осталась")


class AppRestore1312(unittest.TestCase):
    """окно свернули вечером (сброс с датой вечера), развернули утром — снова «День» на сегодня."""
    class Win:
        def __init__(self):
            self.js, self.ev = [], threading.Event()

        def evaluate_js(self, js):
            self.js.append(js)
            self.ev.set()

    def test_restore_after_minimize_resets_again(self):
        import app as A
        ui = A.UI(None)
        ui.win = self.Win()
        ui.on_minimized()
        self.assertTrue(ui.win.ev.wait(3))
        ui.win.ev.clear()
        ui.on_restored()
        self.assertTrue(ui.win.ev.wait(3), "FAIL при разворачивании «День» не обновлялся")
        self.assertEqual(ui.win.js[-1], "window.calHome&&window.calHome()")
        ui.win.ev.clear()
        ui.on_restored()  # второй «развернули» без сворачивания (развернули на весь экран) — не трогаем
        self.assertFalse(ui.win.ev.wait(0.5))
        self.assertEqual(len(ui.win.js), 2)


class Review1312(unittest.TestCase):
    def test_bad_types_400_not_500_and_stats_survive(self):
        doc = doc_with()
        t = new_task(doc, due=D(1))
        with self.assertRaises(S.ApiError) as e:
            S.patch_task(doc, t["id"], {"due": D(5), "dueReason": {"code": []}})
        self.assertEqual(e.exception.status, 400)
        doc["tasks"].append({"id": 50, "project": "a", "title": "битая", "whoId": [1], "plan": D(-3), "due": D(-3), "state": "done", "done": D(0),
                             "doneLate": 10 ** 400, "dueHistory": [{"from": D(-3), "to": D(-1), "at": D(0) + "T10:00", "reason": ["x"], "late": True}]})
        r = S.deadline_stats(doc, date.fromisoformat(D(-30)), date.fromisoformat(D(0)))
        self.assertEqual((r["total"]["done"], r["total"]["avgDelay"], r["total"]["reasons"][0]["code"]), (1, 3.0, "other"))


class PomoAlert1312(unittest.TestCase):
    """П. 27 (часть): конец отрезка — звук 3 раза, уведомление, мигание кнопки, строка в журнале при каждой подаче."""
    def test_alert_sound_notify_flash_logged(self):
        import app as A
        calls = {"sound": 0, "flash": 0, "notify": []}
        done = threading.Event()

        class Icon:
            def notify(self, text, title):
                calls["notify"].append(text)

        class U32:
            def FindWindowW(self, a, b):
                return 1234

            def IsWindowVisible(self, h):  # окно видно — показывать свёрнутым не нужно
                return 1

            def FlashWindowEx(self, p):
                calls["flash"] += 1
                return 1
        old_play, old_u32, old_toast = A.winsound.PlaySound, A.u32, A.toast_run
        def play(*a):
            calls["sound"] += 1
            if calls["sound"] == 3:
                done.set()
        old_find, A.find_own = A.find_own, (lambda title: A.u32.FindWindowW(None, title))
        A.winsound.PlaySound, A.u32, A.toast_run = play, U32(), lambda *a, **k: "не показан (код 1): проверка"  # путь «тост не прошёл»
        try:
            ui = A.UI(None)
            ui.icon = Icon()
            with self.assertLogs(A.log, "INFO") as lg:
                ui.pomodoro_alert("Работа закончена — перерыв 5 минут")
                self.assertTrue(done.wait(5), "FAIL звук один раз")
                for th in [t for t in threading.enumerate() if t.name == "pomo-sound"]:
                    th.join(5)
        finally:
            A.winsound.PlaySound, A.u32, A.toast_run, A.find_own = old_play, old_u32, old_toast, old_find
        self.assertEqual((calls["sound"], calls["flash"], calls["notify"]), (3, 1, ["Работа закончена — перерыв 5 минут"]))
        self.assertTrue(any("pomodoro alert" in m and "тост: не показан" in m and "уведомление трея: ok" in m and "мигание: ok" in m for m in lg.output), lg.output)


# ---------- «Время вышло», «Ещё 5 минут», звук, уведомление Windows ----------
import base64  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import winreg  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402
from datetime import datetime  # noqa: E402
from unittest import mock  # noqa: E402

T0P = datetime(2026, 10, 28, 10, 0, tzinfo=S.TZ).timestamp()
HKCU = winreg.HKEY_CURRENT_USER


class PomoDue1312(unittest.TestCase):
    """Сервер: конец отрезка — «Время вышло» (waiting); следующий отрезок только кнопкой; «Ещё 5 минут» без второго счёта;
    «Время вышло» переживает перезапуск, старше 8 часов снимается само; выключатель звука."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="moyden-1312p-"))
        self.path = self.tmp / "помидоро.json"
        self.now = [T0P]
        self.P = self.new()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new(self):
        return S.Pomodoro(self.path, clock=lambda: self.now[0])

    def go(self, sec):
        self.now[0] += sec

    def end_work(self):
        self.P.command({"action": "start"})
        self.go(1500)
        return self.P.snapshot()

    def test_work_end_waits_break_not_started(self):
        st = self.end_work()
        self.assertEqual((st["phase"], st["running"], st["waiting"], st["today"], st["cycle"]), ("short", False, True, 1, 1))
        self.assertEqual(st["expired"], {"from": "work", "at": round((T0P + 1500) * 1000)})
        self.go(600)
        st = self.P.snapshot()
        self.assertEqual((st["running"], st["waiting"], st["remaining"]), (False, True, 300), "FAIL перерыв шёл сам и тихо кончился")
        st = self.P.command({"action": "start"})
        self.assertEqual((st["phase"], st["running"], st["waiting"], st["expired"]), ("short", True, False, None))
        self.go(120)
        self.assertAlmostEqual(self.P.snapshot()["remaining"], 180, places=2, msg="перерыв считается от нажатия, а не от конца помидора")
        self.go(180)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["waiting"], st["expired"]["from"]), ("work", False, True, "short"))
        self.assertEqual([(e["from"], e["to"]) for e in self.P.tick()], [("short", "work")])

    def test_extend_five_minutes_counts_once(self):
        self.end_work()
        st = self.P.command({"action": "extend"})
        self.assertEqual((st["phase"], st["running"], st["duration"], st["waiting"], st["extra"]), ("work", True, 300, False, 300))
        self.assertEqual(self.P.command({"action": "extend"})["duration"], 300, "двойное нажатие — второго продления нет")
        self.go(299)
        self.assertTrue(self.P.snapshot()["running"])
        self.go(1.5)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["waiting"], st["today"], st["cycle"], st["extra"], st["duration"]), ("short", True, 1, 1, None, 300),
                         "продление не засчитано вторым помидором, перерыв — обычной длины")
        self.assertEqual([(e["from"], e["to"]) for e in self.P.tick()], [("work", "short")], "конец продления — снова сигнал")
        self.P.command({"action": "start"})
        self.go(300)
        st = self.P.command({"action": "extend"})
        self.assertEqual((st["phase"], st["running"], st["duration"]), ("short", True, 300), "«Ещё 5 минут» перерыва")
        self.go(300)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["waiting"], st["expired"]["from"], st["today"]), ("work", True, "short", 1))

    def test_extend_without_waiting_does_nothing(self):
        st = self.P.command({"action": "extend"})
        self.assertEqual((st["phase"], st["running"], st["duration"]), ("work", False, 1500))
        self.P.command({"action": "start"})
        self.go(100)
        self.assertAlmostEqual(self.P.command({"action": "extend"})["remaining"], 1400, places=2, msg="идущий отрезок не продлевается")

    def test_long_break_kept_after_extend_and_reset_clears(self):
        self.P.command({"action": "settings", "durations": {"every": 2}})
        self.end_work()
        self.P.command({"action": "start"})
        self.go(300)
        st = self.end_work()
        self.assertEqual((st["phase"], st["cycle"]), ("long", 2))
        self.P.command({"action": "extend"})
        self.go(300)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["waiting"], st["today"], st["cycle"]), ("long", True, 2, 2), "после «Ещё 5 минут» — тот же длинный перерыв")
        st = self.P.command({"action": "reset"})
        self.assertEqual((st["phase"], st["running"], st["waiting"], st["expired"], st["cycle"], st["remaining"]), ("work", False, False, None, 0, 1500))

    def test_waiting_survives_restart_and_stale_cleared(self):
        self.end_work()
        P2 = self.new()
        st = P2.snapshot()
        self.assertEqual((st["phase"], st["waiting"], st["expired"]["at"]), ("short", True, round((T0P + 1500) * 1000)), "«Время вышло» после перезапуска")
        self.go(8 * 3600 - 60)
        self.assertTrue(P2.snapshot()["waiting"], "до 8 часов «Время вышло» держится")
        self.go(61)
        st = P2.snapshot()
        self.assertEqual((st["phase"], st["running"], st["waiting"], st["remaining"], st["today"]), ("work", False, False, 1500, 1),
                         "вчерашнее «Время вышло» утром не висит, счётчик цел")
        self.assertIsNone(json.loads(self.path.read_text("utf-8"))["waiting"], "снятие записано в файл")

    def test_stale_long_break_resets_cycle(self):
        self.P.command({"action": "settings", "durations": {"every": 2}})
        self.end_work()
        self.P.command({"action": "start"})
        self.go(300)
        self.end_work()
        self.go(8 * 3600 + 1)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["cycle"], st["waiting"]), ("work", 0, False))

    def test_hostile_waiting_extra_sound_in_file(self):
        for raw in ({"waiting": {"from": "обед", "at": T0P}}, {"waiting": {"from": "work", "at": "x"}}, {"waiting": {"from": "work", "at": 1e300}},
                    {"waiting": ["work"]}, {"extra": 99999}, {"extra": True}, {"extra": 0}, {"sound": "нет"}, {"sound": 0}):
            self.path.write_text(json.dumps(raw), "utf-8")
            st = self.new().snapshot()
            self.assertEqual((st["waiting"], st["extra"], st["sound"], st["duration"]), (False, None, True, 1500), raw)
        self.path.write_text(json.dumps({"extra": 300}), "utf-8")  # продление без идущего отрезка — «5-минутный незасчитываемый помидор»
        self.assertEqual(self.new().snapshot()["duration"], 1500)
        self.path.write_text(json.dumps({"extra": 300, "running": True, "startedAt": T0P - 10}), "utf-8")
        self.assertEqual(self.new().snapshot()["duration"], 300, "идущее продление после перезапуска — 5 минут")
        self.path.write_text(json.dumps({"running": True, "startedAt": T0P - 10, "waiting": {"from": "work", "at": T0P - 20}}), "utf-8")
        self.assertFalse(self.new().snapshot()["waiting"], "идёт отрезок — «Время вышло» из файла не показываем")

    def test_sound_toggle_persists_validated_and_in_event(self):
        self.assertTrue(self.P.snapshot()["sound"], "звук включён по умолчанию")
        self.assertFalse(self.P.command({"action": "sound", "on": False})["sound"])
        self.assertFalse(self.new().snapshot()["sound"], "выключатель переживает перезапуск")
        for bad in ({"action": "sound"}, {"action": "sound", "on": "false"}, {"action": "sound", "on": 0}):
            with self.assertRaises(S.ApiError) as e:
                self.P.command(bad)
            self.assertEqual(e.exception.status, 400)
        self.end_work()
        self.assertEqual([e["sound"] for e in self.P.tick()], [False], "событие несёт выключатель — программа не звенит")


class PomoHttp1312(unittest.TestCase):
    """HTTP: «Ещё 5 минут» и звук через /api/pomodoro; подтверждение «Время вышло» (кнопкой в окне или на обоях) убирает тост."""
    setUp, tearDown, req = Http1312.setUp, Http1312.tearDown, Http1312.req

    def test_extend_sound_and_toast_clear(self):
        P, now, cleared = self.app.pomodoro, [T0P], threading.Event()
        P.clock = lambda: now[0]

        class UI:
            toast_shown = False  # тоста нет, пока не было сигнала

            def pomodoro_clear(self_):
                cleared.set()
        self.app.ui = ui = UI()
        st, r = self.req("POST", "/api/pomodoro", {"action": "start"})
        self.assertEqual((st, r["running"]), (200, True))
        self.assertFalse(cleared.wait(0.3), "тоста не было — убирать нечего")
        now[0] += 1500
        st, r = self.req("GET", "/api/pomodoro")
        self.assertEqual((r["waiting"], r["expired"]["from"], r["phase"]), (True, "work", "short"))
        ui.toast_shown = True  # сигнал «Время вышло» показал тост
        self.req("POST", "/api/pomodoro", {"action": "task", "task": None})
        self.assertFalse(cleared.wait(0.3), "«Время вышло» не подтверждено — тост остаётся")
        st, r = self.req("POST", "/api/pomodoro", {"action": "extend"})
        self.assertEqual((st, r["phase"], r["running"], r["duration"], r["waiting"]), (200, "work", True, 300, False))
        self.assertTrue(cleared.wait(3), "подтверждение в окне или на обоях убирает тост")
        st, r = self.req("POST", "/api/pomodoro", {"action": "sound", "on": "нет"})
        self.assertEqual(st, 400)
        st, r = self.req("POST", "/api/pomodoro", {"action": "sound", "on": False})
        self.assertEqual((st, r["sound"]), (200, False))

    def test_no_signal_after_confirm_and_still_checks_time(self):  # подтвердили до тика — ни звука, ни тоста
        P, now, got = self.app.pomodoro, [T0P], []
        P.clock = lambda: now[0]
        self.app.ui = type("U", (), {"pomodoro_alert": lambda s, t, **kw: got.append(kw)})()
        P.command({"action": "start"})
        now[0] += 1500
        P.snapshot()  # отрезок кончился (окно увидело «Время вышло»)
        P.command({"action": "start"})  # «Перерыв» нажат до тика
        for ev in P.tick():
            self.app.pomo_notify(ev)
        self.assertEqual(got, [], "сигнал после подтверждения")
        now[0] += 300
        ev2 = P.tick()[0]  # перерыв кончился — новое «Время вышло»
        self.app.pomo_notify(ev2)
        self.assertEqual(len(got), 1)
        self.assertFalse(self.app.pomo_waiting(T0P + 1500), "старый конец отрезка — не текущее «Время вышло»: старый повтор молчит")
        self.assertTrue(got[0]["still"]())

    def test_notify_passes_sound_title_and_still(self):
        P, now, got = self.app.pomodoro, [T0P], []
        P.clock = lambda: now[0]
        self.app.ui = type("U", (), {"pomodoro_alert": lambda s, t, **kw: got.append((t, kw))})()
        P.command({"action": "sound", "on": False})
        P.command({"action": "start"})
        now[0] += 1500
        self.app.pomo_notify(P.tick()[0])
        text, kw = got[0]
        self.assertEqual((kw["sound"], kw["title"]), (False, "Время вышло: перерыв"))
        self.assertIn("нажмите «Перерыв»", text)
        self.assertTrue(kw["still"](), "«Время вышло» ещё не подтверждено — повтор возможен")
        P.command({"action": "start"})
        self.assertFalse(kw["still"](), "подтвердили — повтора нет")


class Alert1312(unittest.TestCase):
    """Пути подачи сигнала (unit + мутанты): тост показан — без трея и повтора; не показан — трей и повтор каждые N с, пока не подтверждено,
    не больше 3; «Выход» прерывает повтор; звук по выключателю; окна нет (трей, обои) и значка нет — сигнал всё равно подаётся."""
    def make(self, toast="ok", hwnd=1234):
        import app as A
        calls = self.calls = {"sound": 0, "flash": 0, "notify": [], "toast": []}

        class Icon:
            def notify(self_, text, title):
                calls["notify"].append(text)

        class U32:
            def FindWindowW(self_, a, b):
                return hwnd

            def IsWindowVisible(self_, h):  # окно видно — показывать свёрнутым не нужно 
                return 1

            def FlashWindowEx(self_, p):
                calls["flash"] += 1
                return 1

        def snd(*a):
            calls["sound"] += 1
        for p in (mock.patch.object(A, "u32", U32()), mock.patch.object(A, "find_own", lambda title: A.u32.FindWindowW(None, title)),
                  mock.patch.object(A.winsound, "PlaySound", side_effect=snd),
                  mock.patch.object(A, "toast_run", side_effect=lambda *a, **k: calls["toast"].append((a, k)) or toast)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.join)
        ui = A.UI(None)
        ui.icon = Icon()
        ui.repeat_every = 0.05
        self.A = A
        return ui

    @staticmethod
    def join():
        for th in [t for t in threading.enumerate() if t.name in ("pomo-sound", "pomo-repeat")]:
            th.join(10)

    def test_toast_shown_no_tray_no_repeat(self):
        ui = self.make("ok")
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("Помидор закончился.", still=lambda: True, title="Время вышло: перерыв")
            self.join()
            time.sleep(0.3)
        c = self.calls
        self.assertEqual((c["toast"], c["notify"], c["flash"], c["sound"], ui.toast_shown),
                         ([(("Время вышло: перерыв", "Помидор закончился."), {})], [], 1, 3, True))
        self.assertTrue(any("тост: ok" in m and "уведомление трея: не нужно (тост)" in m and "звук: 3 раза" in m for m in lg.output), lg.output)

    def test_toast_failed_tray_and_repeat_until_confirmed(self):
        ui = self.make("не показан (код 1): проверка")

        def still():  # «ещё ждёт» — по состоянию (подтвердили после 3-го уведомления), а не по числу вызовов still()
            return len(self.calls["notify"]) < 3
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("Перерыв закончился.", still=still)
            self.join()
        self.assertEqual((len(self.calls["notify"]), self.calls["flash"], ui.toast_shown), (3, 3, False), "1 сразу + 2 повтора, третий — подтверждено")
        self.assertTrue(any("pomodoro repeat 2 из 3" in m for m in lg.output) and any("подтверждено — повтора нет" in m for m in lg.output), lg.output)

    def test_repeat_at_most_three(self):
        ui = self.make("не показан: FileNotFoundError")
        ui.pomodoro_alert("x", still=lambda: True)
        self.join()
        self.assertEqual(len(self.calls["notify"]), 4, "не больше 3 повторов")

    def test_quit_stops_repeat(self):
        ui = self.make("не показан: TimeoutExpired")
        ui.repeat_every = 30
        ui.pomodoro_alert("x", still=lambda: True)
        t0 = time.time()
        ui.stop_ev.set()
        self.join()
        self.assertLess(time.time() - t0, 5, "повтор не держит выход")
        self.assertEqual(len(self.calls["notify"]), 1)

    def test_sound_off(self):
        ui = self.make("ok")
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("x", sound=False)
            self.join()
        self.assertEqual(self.calls["sound"], 0, "звук выключен в настройках таймера")
        self.assertTrue(any("звук: выключен" in m for m in lg.output), lg.output)

    def test_no_window_no_tray_still_signals(self):
        ui = self.make("не показан (код 3): setting=DisabledForUser", hwnd=0)  # окно закрыто в трей / только обои; уведомления выключены
        ui.icon = None
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("x")
            self.join()
        self.assertEqual((len(self.calls["toast"]), self.calls["sound"]), (1, 3))
        self.assertTrue(any("уведомление трея: нет значка в трее" in m and "мигание: окна нет" in m for m in lg.output), lg.output)
        ui2 = self.make("ok", hwnd=0)
        ui2.pomodoro_alert("x")
        self.assertTrue(ui2.toast_shown, "тост не зависит от окна: свёрнуто, в трее, на обоях")

    def test_confirmed_while_toast_showing_removes_it(self):  # нажали «Перерыв», пока PowerShell показывал тост
        ui = self.make("ok")
        ui.pomodoro_alert("x", still=lambda: False)
        self.join()
        self.assertEqual(self.calls["toast"][-1], ((), {"mode": "remove"}), "тост убран сразу после показа")
        self.assertFalse(ui.toast_shown)

    def test_clear_only_after_shown(self):
        ui = self.make("ok")
        self.assertIsNone(ui.pomodoro_clear())
        self.assertEqual(self.calls["toast"], [])
        ui.toast_shown = True
        self.assertEqual(ui.pomodoro_clear(), "ok")
        self.assertEqual(self.calls["toast"], [((), {"mode": "remove"})])
        self.assertFalse(ui.toast_shown)


class Toast1312(unittest.TestCase):
    """Тост: XML (reminder + системная «Закрыть» + без звука + экранирование), команда PowerShell (только ASCII, данные — base64, без окна),
    разбор ответов, страж проверок, настоящий запуск без показа (dry); ID окна и ярлыка «Пуска» не меняется."""
    def test_xml_reminder_dismiss_silent_escaped(self):
        import app as A
        x = ET.fromstring(A.toast_xml("Время вышло", 'Задача «Макет "А" & <Б>»\x01\x1f'))
        self.assertEqual(x.get("scenario"), "reminder", "держится на экране до «Закрыть»")
        self.assertEqual([(a.get("activationType"), a.get("arguments")) for a in x.findall("./actions/action")], [("system", "dismiss")],
                         "без кнопки reminder — обычный тост на 5 с")
        self.assertEqual(x.find("audio").get("silent"), "true")
        self.assertEqual([t.text for t in x.iter("text")], ["Время вышло", 'Задача «Макет "А" & <Б>»'])

    def test_script_ascii_base64_and_results(self):
        import app as A
        calls = []

        def fake(code=0, out=b"ok\r\n", err=b""):
            def run(args, **kw):
                calls.append((args, kw))
                return subprocess.CompletedProcess(args, code, out, err)
            return run
        with mock.patch.dict(os.environ, {"MOYDEN_NO_TOAST": "0"}):
            self.assertEqual(A.toast_run("Время вышло", "Текст «к» 'x'", run=fake()), "ok")
            args, kw = calls[-1]
            self.assertEqual(Path(args[0]).parts[-3:], ("WindowsPowerShell", "v1.0", "powershell.exe"))
            self.assertEqual(Path(args[0]).parts[-4].lower(), "system32", "полный путь, не поиск по PATH")
            self.assertEqual(args[1:], ["-NoProfile", "-NonInteractive", "-Command", "-"])
            self.assertEqual(kw["creationflags"], 0x08000000, "без окна консоли")
            script = kw["input"].decode("ascii")  # только ASCII: кириллица и кавычки — внутри base64
            b64 = re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
            self.assertEqual(base64.b64decode(b64[0]).decode(), A.TOAST_ID)
            self.assertIn("Текст «к» 'x'", base64.b64decode(b64[1]).decode())
            self.assertIn("$mode='show'", script)
            self.assertTrue(A.toast_run("a", "b", run=fake(3, b"setting=DisabledForUser\r\n")).startswith("не показан (код 3): setting=DisabledForUser"))
            self.assertIn("LoadXml", A.toast_run("a", "b", run=fake(1, b"", "Исключение LoadXml".encode())))
            self.assertTrue(A.toast_run("a", "b", run=fake(0, b"")).startswith("не показан"), "пустой ответ — не «ok»")

            def boom(*a, **k):
                raise FileNotFoundError("нет powershell")
            self.assertEqual(A.toast_run("a", "b", run=boom), "не показан: FileNotFoundError")

            def slow(*a, **k):
                raise subprocess.TimeoutExpired("ps", 25)
            self.assertEqual(A.toast_run("a", "b", run=slow), "не показан: TimeoutExpired")
            with self.assertRaises(ValueError):
                A.toast_run(mode="зачем")
            self.assertEqual(A.toast_run(mode="remove", run=fake()), "ok")
            self.assertIn("$mode='remove'", calls[-1][1]["input"].decode("ascii"))

    def test_guard_blocks_show_in_checks(self):
        import app as A
        called = []
        with mock.patch.dict(os.environ, {"MOYDEN_NO_TOAST": "1"}):
            r1 = A.toast_run("a", "b", run=lambda *a, **k: called.append(1))
            r2 = A.toast_run(mode="remove", run=lambda *a, **k: called.append(1))
        self.assertEqual((called, r1.startswith("не показан"), r2.startswith("не показан")), ([], True, True))

    def test_real_dry_run_script_valid(self):
        """Настоящий Windows PowerShell 5.1: типы WinRT, разбор XML с кавычками и кириллицей, создание уведомления — без показа (dry)."""
        import app as A
        r = A.toast_run("Время вышло: перерыв", 'Помидор по задаче «Макет "А" & <Б>» закончился.', mode="dry")
        self.assertTrue(r.startswith("dry setting="), r)

    def test_window_and_start_id_untouched(self):
        import app as A
        import установщик as I
        self.assertIsNone(A.aumid_for(True, ""), "собранная программа — ID окна из пути exe, как у ярлыка «Пуска» и закреплённой кнопки")
        src = (SRC / "app.py").read_text("utf-8")
        self.assertEqual(src.count("SetCurrentProcessExplicitAppUserModelID"), 1, "ID окна задаётся только из исходников (if AUMID)")
        self.assertNotIn("AppUserModel", (SRC / "установщик.py").read_text("utf-8").replace("AppUserModelId\\\\MoyDen", ""),
                         "ярлыкам «Пуска» установщик ID не ставит")
        self.assertTrue(A.TOAST_ID.startswith("MoyDen.App"))
        main = src[src.index("def main(argv)"):]
        self.assertLess(main.index("if FROZEN:"), main.index("toast_register()"), "ключ уведомлений пишет только установленная программа")


class ToastRegister1312(unittest.TestCase):
    def test_register_key_and_png_from_ico(self):
        import app as A
        from PIL import Image
        tmp = Path(tempfile.mkdtemp(prefix="moyden-1312r-"))
        key = "Software\\Classes\\AppUserModelId\\MoyDen.App.тест1312-" + str(os.getpid())
        png = tmp / "значок-уведомлений.png"
        try:
            with mock.patch.object(A, "TOAST_KEY", key), mock.patch.object(A, "TOAST_PNG", png):
                self.assertEqual(A.toast_register(), "ok")
                with winreg.OpenKey(HKCU, key) as k:
                    self.assertEqual((winreg.QueryValueEx(k, "DisplayName")[0], winreg.QueryValueEx(k, "IconUri")[0]), (A.TITLE, str(png)))
                with Image.open(png) as im:
                    self.assertEqual((im.format, im.size), ("PNG", (256, 256)), "самый крупный кадр мой_день.ico")
                m1 = png.stat().st_mtime_ns
                time.sleep(0.05)
                A.toast_register()
                self.assertEqual(png.stat().st_mtime_ns, m1, "тот же значок второй раз не пишется")
        finally:
            try:
                winreg.DeleteKey(HKCU, key)
            except OSError:
                pass
            shutil.rmtree(tmp, ignore_errors=True)


class UninstallToast1312(unittest.TestCase):
    """Удаление программы убирает свой ключ уведомлений и значок; чужой ключ (значок в другой папке) не трогает."""
    def setUp(self):
        import установщик as I
        self.I, self.keys = I, []
        self.tmp = Path(tempfile.mkdtemp(prefix="moyden-1312u-"))

    def tearDown(self):
        for k in self.keys:
            try:
                winreg.DeleteKey(HKCU, k)
            except OSError:
                pass
        time.sleep(3.5)  # удалитель убирает свою папку через cmd после выхода — не мешаем ему
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, inst):
        I = self.I
        base = self.tmp / inst
        t, dd = base / "Программа", base / "Данные"
        dirs = {k: base / k for k in ("Пуск", "РС", "Авто")}
        for p in [t, dd, *dirs.values()]:
            p.mkdir(parents=True)
        shutil.copyfile(HERE / "fixture-data.json", dd / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", dd / "settings.json")
        pay = base / "payload"
        pay.mkdir()
        (pay / "MoyDen.exe").write_bytes(b"MZ 1312")
        shutil.copyfile(DEV / "мой_день.ico", pay / "мой_день.ico")
        name, key = f"Мой день ({inst})", "MoyDen.T" + inst
        self.keys.append(rf"{I.UNINSTALL_ROOT}\{key}")
        a = Namespace(target=str(t), data=str(dd), name=name, regkey=key, port=free_port(), instance=inst, manifest=None, desktop_dir=str(dirs["РС"]),
                      startup_dir=str(dirs["Авто"]), start_dir=str(dirs["Пуск"]), replace_old=False, wallpaper_on=False, launch=False, uninstall=False, quiet=True)
        with mock.patch.object(I, "PAYLOAD", pay), mock.patch.object(I, "recycle", return_value=True), \
                mock.patch.object(I, "other_build", return_value=None):  # компьютер проверки — как без другой сборки «Мой день»
            I.install(a)
        tk = "Software\\Classes\\AppUserModelId\\MoyDen.App." + inst
        self.keys.append(tk)
        return t, tk

    def uninstall(self, t):
        self.I.uninstall(Namespace(manifest=str(t / "установка.json"), quiet=True))

    def test_own_key_and_png_removed(self):
        t, tk = self.install("т1312u")
        png = t / "значок-уведомлений.png"
        png.write_bytes(b"\x89PNG")
        with winreg.CreateKey(HKCU, tk) as k:
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(png))
        self.uninstall(t)
        with self.assertRaises(OSError):
            winreg.OpenKey(HKCU, tk).Close()
        self.assertFalse(png.exists(), "значок уведомлений удалён вместе с программой")

    def test_foreign_key_kept(self):
        t, tk = self.install("т1312v")
        foreign = str(self.tmp / "чужая" / "значок.png")
        with winreg.CreateKey(HKCU, tk) as k:
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, foreign)
        self.uninstall(t)
        with winreg.OpenKey(HKCU, tk) as k:
            self.assertEqual(winreg.QueryValueEx(k, "IconUri")[0], foreign, "чужой ключ не трогаем")


class Flash1312(unittest.TestCase):
    """окно в трее — кнопки нет, мигать нечему: показать свёрнутым без активации (SW_SHOWMINNOACTIVE = 7), потом мигание;
    журнал — что было; подтверждение «Время вышло» прячет окно обратно, если его не открыли; открытое — не трогает."""

    class U32:
        def __init__(self, visible=False, can_show=True, fg=0):
            self.visible, self.iconic, self.can_show, self.fg, self.show, self.flash = visible, False, can_show, fg, [], []

        def FindWindowW(self, a, b):
            return 77

        def IsWindowVisible(self, h):
            return int(self.visible)

        def IsIconic(self, h):
            return int(self.iconic)

        def GetForegroundWindow(self):
            return self.fg

        def ShowWindow(self, h, cmd):
            self.show.append(cmd)
            if cmd == 0:
                self.visible = self.iconic = False
            elif self.can_show:
                self.visible, self.iconic = True, cmd in (2, 6, 7)
            return 1

        def FlashWindowEx(self, p):
            self.flash.append(p._obj.dwFlags)
            return 1

    def ui(self, u):
        import app as A
        self.A = A
        self.addCleanup(setattr, A, "u32", A.u32)
        self.addCleanup(setattr, A, "find_own", A.find_own)
        A.u32 = u
        A.find_own = lambda title: A.u32.FindWindowW(None, title)
        return A.UI(None)

    def test_tray_window_shown_minimized_without_activation(self):
        u = self.U32(visible=False)
        ui = self.ui(u)
        r = ui._flash()
        self.assertEqual(u.show, [7], "FAIL 1.3.12 до окно в трее не показано — кнопки нет, мигание впустую")
        self.assertEqual(u.flash, [0xF])
        self.assertEqual(r, "ok (окно было в трее — показано свёрнутым без активации)")
        self.assertTrue(ui.flash_shown)
        self.assertEqual(ui._flash(), "ok")  # повтор уведомления: окно уже на панели — второй раз не показывать
        self.assertEqual(u.show, [7])

    def test_visible_window_not_touched(self):
        u = self.U32(visible=True)
        ui = self.ui(u)
        self.assertEqual(ui._flash(), "ok")
        self.assertEqual((u.show, u.flash), ([], [0xF]))
        self.assertIsNone(ui.flash_restore())

    def test_show_failed_logged_honestly(self):
        u = self.U32(visible=False, can_show=False)
        r = self.ui(u)._flash()
        self.assertEqual(r, "окно в трее, показать свёрнутым не вышло — кнопки нет, мигать нечему")
        self.assertEqual(u.flash, [], "мигание по невидимому окну с записью «ok»")

    def test_confirm_returns_window_to_tray(self):
        u = self.U32(visible=False)
        ui = self.ui(u)
        ui._flash()
        with self.assertLogs(self.A.log, "INFO") as lg:
            r = ui.flash_restore()
        self.assertEqual((r, u.show[-1], u.flash[-1], u.visible), ("окно снова в трее", 0, 0, False))
        self.assertTrue(any("flash restore: окно снова в трее" in m for m in lg.output), lg.output)
        self.assertIsNone(ui.flash_restore())

    def test_opened_window_stays(self):  # каждая половина условия отдельно
        for iconic, fg, why in ((False, 0, "открыто кнопкой на панели, но пользователь ушёл в другую программу"), (True, 77, "свёрнуто, но активно")):
            u = self.U32(visible=False)
            ui = self.ui(u)
            ui._flash()
            u.iconic, u.fg = iconic, fg
            self.assertEqual(ui.flash_restore(), "окно открыто пользователем — не трогаю", why)
            self.assertNotIn(0, u.show, why)

    def test_confirmed_during_toast_window_not_shown(self):  #
        u = self.U32(visible=False)
        ui = self.ui(u)
        st = {"w": True}

        def toast(*a, **k):
            st["w"] = False  # пользователь нажал «Перерыв», пока PowerShell показывал тост
            return "ok"
        self.addCleanup(setattr, self.A, "toast_run", self.A.toast_run)
        self.A.toast_run = toast
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("Работа закончена", sound=False, still=lambda: st["w"])
        self.assertEqual((u.show, u.visible, getattr(ui, "flash_shown", False)), ([], False, False), "окно показано после подтверждения")
        self.assertTrue(any("мигание: не нужно (уже подтверждено)" in m for m in lg.output), lg.output)

    def test_alert_log_says_what_happened(self):
        u = self.U32(visible=False)
        ui = self.ui(u)
        self.addCleanup(setattr, self.A, "toast_run", self.A.toast_run)
        self.A.toast_run = lambda *a, **k: "ok"
        with self.assertLogs(self.A.log, "INFO") as lg:
            ui.pomodoro_alert("Работа закончена", sound=False)
        self.assertTrue(any("мигание: ok (окно было в трее — показано свёрнутым без активации)" in m for m in lg.output), lg.output)

    def test_confirm_command_starts_restore(self):
        ev = threading.Event()

        class UIx:
            flash_shown, toast_shown = True, False

            def flash_restore(self):
                ev.set()

        class P:
            def __init__(self, waiting):
                self.w = waiting

            def command(self, body):
                return {"ok": True, "waiting": self.w}
        S.App.pomo_command(Namespace(pomodoro=P(False), ui=UIx()), {"action": "reset"})
        self.assertTrue(ev.wait(3), "подтверждение не вернуло окно в трей")
        ev.clear()
        S.App.pomo_command(Namespace(pomodoro=P(True), ui=UIx()), {"action": "pause"})
        self.assertFalse(ev.wait(0.5), "окно спрятано, хотя «Время вышло» ещё ждёт")


if __name__ == "__main__":
    unittest.main()
