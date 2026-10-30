"""Проверки версии 1.3.1 : страница из папки программы, возврат завершённого проекта,
гонка с клиентом API, SSRF календаря, «Вернуть» задачу с длинной лентой, цвет Google, файл синхронизации, мусор от клиента API.

Запуск (из папки репозитория):  python -m unittest tests.test_131
"""
import http.server
import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_server import Base, S, free_port  # noqa: E402

OWNER = None  # заголовки страницы — задаются в setUp (нужен порт)


class V131(Base):
    def page(self):
        return {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"}

    def proj(self, pid):
        return next(p for p in self.data()["projects"] if p["id"] == pid)

    def task(self, tid):
        return next(t for t in self.data()["tasks"] if t["id"] == tid)

    # --- архитектура: страница из папки программы ---
    def test_page_served_from_program_dir_not_data_dir(self):
        (self.tmp / "дашборд.html").write_text("<p>СТРАНИЦА-ИЗ-ПАПКИ-ДАННЫХ</p>", "utf-8")
        prog = self.tmp / "программа"
        prog.mkdir()
        (prog / "дашборд.html").write_text("<p>СТРАНИЦА-ПРОГРАММЫ-1.3.1</p>", "utf-8")
        old = S.PAGE_DIR
        try:
            S.PAGE_DIR = prog
            self.srv.app = S.App(self.args)
            st, _, _ = self.req("GET", "/api/ping")
            import urllib.request
            html = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10).read().decode()
            self.assertIn("СТРАНИЦА-ПРОГРАММЫ-1.3.1", html)
            self.assertNotIn("ПАПКИ-ДАННЫХ", html, "FAIL-сценарий: страница взята из папки данных")
            S.PAGE_DIR = None  # без настройки — страница рядом с server.py, а не в папке данных
            self.srv.app = S.App(self.args)
            html = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10).read().decode()
            self.assertNotIn("ПАПКИ-ДАННЫХ", html)
        finally:
            S.PAGE_DIR = old

    # --- / / L12 ---
    def test_reopen_restores_tasks_closed_by_finish(self):
        self.assertEqual(self.req("PATCH", "/api/tasks/2", {"state": "wait"})[0], 200)
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "done", "openTasks": "done"}, headers=self.page())
        self.assertEqual(st, 200)
        self.assertEqual(self.task(2)["state"], "done")
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "risk"}, headers=self.page())
        self.assertEqual(st, 409, "смена статуса у завершённого проекта не возвращает его в работу")
        self.assertIn("Вернуть в работу", res["error"])
        self.assertEqual(self.proj("zeta")["status"], "done")
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "ok", "reopen": True}, headers=self.page())
        self.assertEqual(st, 200)
        t = self.task(2)
        self.assertEqual(t["state"], "wait", "задача вернулась в прежнее состояние")
        self.assertNotIn("done", t)
        p = self.proj("zeta")
        self.assertNotIn("closedOnFinish", p)
        self.assertEqual(p["history"][-1].get("tasks"), 1)

    def test_reopen_does_not_touch_tasks_done_before_finish(self):
        self.req("PATCH", "/api/tasks/2", {"state": "done"})
        self.req("PATCH", "/api/projects/zeta", {"status": "done"})  # открытых задач нет — выбор не нужен
        self.req("PATCH", "/api/projects/zeta", {"status": "ok", "reopen": True})
        self.assertEqual(self.task(2)["state"], "done", "выполненная до завершения задача так и остаётся выполненной")

    def test_done_project_cannot_be_paused_and_move_to_paused_forbidden(self):
        self.req("PATCH", "/api/tasks/2", {"state": "done"})
        self.req("PATCH", "/api/projects/zeta", {"status": "done"})
        self.assertEqual(self.req("PATCH", "/api/projects/zeta", {"status": "pause"})[0], 400)
        self.assertEqual(self.req("PATCH", "/api/projects/zeta", {"pause": {"reason": "x"}})[0], 400)
        self.assertNotIn("pause", self.proj("zeta"))
        self.req("PATCH", "/api/projects/zeta", {"status": "ok", "reopen": True})
        self.req("PATCH", "/api/tasks/2", {"state": "work"})
        self.assertEqual(self.req("PATCH", "/api/projects/brand", {"pause": {"reason": "ждём"}})[0], 200)
        st, res, _ = self.req("PATCH", "/api/projects/zeta", {"status": "done", "openTasks": "move", "moveTo": "brand"})
        self.assertEqual(st, 400, "перенос в проект на паузе запрещён")
        self.assertEqual(self.task(2)["project"], "zeta")

    # --- «Вернуть» задачу с лентой > 64 КБ ---
    def test_restore_task_with_comments_over_64kb(self):
        t = self.task(2)
        comments = [{"id": 900 + i, "text": "ж" * 2000, "at": "2026-10-20T10:00", "author": "owner"} for i in range(40)]
        self.assertEqual(self.req("DELETE", "/api/tasks/2")[0], 200)
        body = {**{k: t[k] for k in ("title", "project", "due") if k in t}, "id": 2, "comments": comments}
        self.assertGreater(len(json.dumps(body).encode()), 64 * 1024)
        st, res, _ = self.req("POST", "/api/tasks", body)
        self.assertEqual(st, 201, res)
        self.assertEqual((self.task(2)["id"], len(self.task(2)["comments"])), (2, 40))
        st, res, _ = self.req("PATCH", "/api/tasks/2", {"title": "я" * 40000})  # обычные правки — прежний предел 64 КБ
        self.assertEqual(st, 413)

    # --- FAIL-сценарии записи ---
    def test_migration_does_not_overwrite_bot_write(self):
        path = self.tmp / "data.json"
        path.write_text((Path(__file__).resolve().parent / "fixture-old-data.json").read_text("utf-8"), "utf-8")
        st, orig = self.app.store, self.app.store._write

        def racing(data, expect=None):  # клиент API записал файл между чтением и записью миграции
            path.write_text('{"projects": [], "tasks": [], "employees": [], "bot": 1}', "utf-8")
            return orig(data, expect)
        st._write = racing
        try:
            with self.assertRaises(S.ApiError) as cm:
                st.migrate_file()
        finally:
            del st._write
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(json.loads(path.read_text("utf-8")).get("bot"), 1, "R6: запись клиента API затёрта миграцией")

    def test_sync_without_color_keeps_local_color(self):
        synced = datetime.now(S.TZ).isoformat(timespec="seconds")
        (self.tmp / "google-events.json").write_text(json.dumps({"synced_at": synced, "calendars": [{"id": "work", "name": "Рабочий"}],
                                                                  "events": []}, ensure_ascii=False), "utf-8")
        st, pub, _ = self.req("GET", "/api/calendars")
        self.assertEqual(next(c for c in pub["calendars"] if c["name"] == "Рабочий")["color"], "#4a6cf7", "R7")
        day = S.today()
        st, r, _ = self.req("GET", f"/api/events?from={day}&to={(datetime.fromisoformat(day) + timedelta(days=1)).date()}")
        self.assertEqual({c["color"] for c in r["calendars"] if c["name"] == "Рабочий"}, {"#4a6cf7"}, "R7: цвет из палитры вытеснил цвет пользователя")

    def test_encoded_host_rejected(self):
        for u in ("https://evil.com%2f.google.com/a.ics", "https://calendar.google.com%2e/a.ics", "https://evil.com\\.google.com/a.ics"):
            with self.assertRaises(S.CalError, msg=u):
                S.normalize_url(u)

    def test_lock_of_other_writer_is_not_removed(self):
        lock = self.tmp / "data.json.lock"
        with self.app.store.file_lock():
            lock.write_bytes(b"other-writer")  # замок перехватил другой писатель (забытый замок снят)
        self.assertEqual(lock.read_bytes(), b"other-writer", "сервер удалил чужой замок")
        lock.unlink()

    # --- очередь записи при обрыве связи: сервер ---
    def test_same_entry_edited_text_updates_not_duplicates(self):
        st, r1, _ = self.req("POST", "/api/tasks/2/comments", {"text": "B: исходный", "clientId": "e-1:aaaa"})
        st, r2, _ = self.req("POST", "/api/tasks/2/comments", {"text": "B: исходный + правка", "clientId": "e-1:bbbb"})
        self.assertEqual(r1["item"]["id"], r2["item"]["id"])
        cs = [c for c in self.task(2)["comments"] if c["text"].startswith("B:")]
        self.assertEqual([c["text"] for c in cs], ["B: исходный + правка"], "дописанный текст — второй комментарий")
        self.assertIn("edited", cs[0])

    def test_deleted_comment_does_not_resurrect_and_restore_clears_tombstone(self):
        st, r, _ = self.req("POST", "/api/tasks/2/comments", {"text": "G", "clientId": "e-7:1111"})
        cidn = r["item"]["id"]
        st, rd, _ = self.req("DELETE", f"/api/tasks/2/comments/{cidn}")
        self.assertEqual(st, 200)
        st, r3, _ = self.req("POST", "/api/tasks/2/comments", {"text": "G", "clientId": "e-7:1111"})
        self.assertEqual(st, 201)
        self.assertIsNone(r3["item"])
        self.assertFalse(any(c["text"] == "G" for c in self.task(2).get("comments", [])), "MED-γ: комментарий воскрес")
        st, r4, _ = self.req("POST", "/api/tasks/2/comments", {**rd["item"]})  # «Вернуть»
        self.assertEqual(st, 201)
        self.assertFalse(any(str(x).startswith("e-7:") for x in self.data().get("commentTombstones", [])))
        st, r5, _ = self.req("POST", "/api/tasks/2/comments", {"text": "G", "clientId": "e-7:1111"})
        self.assertEqual(len([c for c in self.task(2)["comments"] if c["text"] == "G"]), 1, "после «Вернуть» повтор не дублирует")

    def test_text_added_after_delete_is_not_lost(self):
        st, r, _ = self.req("POST", "/api/tasks/2/comments", {"text": "S1: первый", "clientId": "e-9:aaaa"})
        self.req("DELETE", f"/api/tasks/2/comments/{r['item']['id']}")
        st, r2, _ = self.req("POST", "/api/tasks/2/comments", {"text": "S1: первый + важное дописано", "clientId": "e-9:bbbb"})
        self.assertIsNotNone(r2["item"], "дописанный после удаления текст пропал")
        self.assertEqual([c["text"] for c in self.task(2)["comments"] if c["text"].startswith("S1")], ["S1: первый + важное дописано"])
        st, r3, _ = self.req("POST", "/api/tasks/2/comments", {"text": "S1: первый", "clientId": "e-9:aaaa"})
        self.assertIsNone(r3["item"], "тот же удалённый текст по-прежнему не воскресает")

    def test_double_restore_and_manual_edit(self):
        st, r, _ = self.req("POST", "/api/tasks/2/comments", {"text": "R", "clientId": "e-r:1"})
        st, rd, _ = self.req("DELETE", f"/api/tasks/2/comments/{r['item']['id']}")
        a = self.req("POST", "/api/tasks/2/comments", {**rd["item"]})[1]["item"]
        b = self.req("POST", "/api/tasks/2/comments", {**rd["item"]})[1]["item"]
        self.assertEqual(a["id"], b["id"], "повтор «Вернуть» создал второй комментарий")
        self.req("PATCH", f"/api/tasks/2/comments/{a['id']}", {"text": "R исправлено вручную"})
        self.req("POST", "/api/tasks/2/comments", {"text": "R старое из спавшего окна", "clientId": "e-r:2"})
        self.assertEqual([c["text"] for c in self.task(2)["comments"] if c["text"].startswith("R")], ["R исправлено вручную"], "досылка затёрла ручную правку")
        self.req("DELETE", f"/api/tasks/2/comments/{a['id']}")
        st, r4, _ = self.req("POST", "/api/tasks/2/comments", {"text": "R ещё вариант", "clientId": "e-r:3"})
        self.assertIsNone(r4["item"], "правленный и удалённый комментарий не воскресает ни с каким текстом")

    def test_tombstones_capped_at_50(self):
        for i in range(55):
            r = self.req("POST", "/api/tasks/2/comments", {"text": f"t{i}", "clientId": f"z-{i}:1"})[1]["item"]
            self.req("DELETE", f"/api/tasks/2/comments/{r['id']}")
        tombs = self.data()["commentTombstones"]
        self.assertEqual(len(tombs), 50)
        self.assertEqual(tombs[-1], "z-54:1")

    # --- удалитель без маркера установки ничего не трогает ---
    def test_uninstall_without_manifest_touches_nothing(self):
        import subprocess
        d = self.tmp / "чужая-папка"
        d.mkdir()
        for f in ("Мой день.exe", "мой_день.ico", "data.json"):
            (d / f).write_text("x", "utf-8")
        (d / "настройки-установки.json").write_text('{"port": 8697}', "utf-8")
        root = Path(__file__).resolve().parent.parent
        r = subprocess.run([sys.executable, str(root / "установщик.py"), "--uninstall", "--quiet", "--manifest", str(d / "установка.json")],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 6)
        self.assertEqual(sorted(p.name for p in d.iterdir()), sorted(["Мой день.exe", "мой_день.ico", "data.json", "настройки-установки.json"]))
        (d / "установка.json").write_text(json.dumps({"target": str(self.tmp), "name": "Мой день", "regkey": "MoyDen.X"}), "utf-8")  # чужая цель
        r = subprocess.run([sys.executable, str(root / "установщик.py"), "--uninstall", "--quiet", "--manifest", str(d / "установка.json")],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 6)
        self.assertTrue((d / "Мой день.exe").exists())

    # --- повтор отправки после обрыва не создаёт дубль ---
    def test_comment_retry_with_same_client_id_is_not_duplicated(self):
        b = {"text": "при обрыве", "author": "owner", "clientId": "c-123"}
        st1, r1, _ = self.req("POST", "/api/tasks/2/comments", b)
        st2, r2, _ = self.req("POST", "/api/tasks/2/comments", b)
        self.assertEqual((st1, st2), (201, 201))
        self.assertEqual(r1["item"]["id"], r2["item"]["id"])
        self.assertEqual(len([c for c in self.task(2)["comments"] if c["text"] == "при обрыве"]), 1, "FAIL-сценарий: дубль")
        self.assertEqual(self.req("POST", "/api/tasks/2/comments", {"text": "x", "clientId": "я" * 65})[0], 400)

    # --- гонка с клиентом API ---
    def test_bot_with_lock_and_server_edits_both_survive(self):
        path = self.tmp / "data.json"
        lock = self.tmp / "data.json.lock"
        stop = []

        def bot():
            for i in range(25):
                while True:
                    try:
                        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        break
                    except (FileExistsError, PermissionError):  # Windows — замок ещё «удаляется»
                        time.sleep(0.005)
                try:
                    d = json.loads(path.read_text("utf-8"))
                    d.setdefault("botMarks", []).append(i)
                    tmp = path.with_name("bot.tmp")
                    tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
                    os.replace(tmp, path)
                finally:
                    os.close(fd)
                    os.unlink(lock)
                time.sleep(0.01)
            stop.append(1)

        th = threading.Thread(target=bot)
        th.start()
        made = []
        for i in range(25):
            st, res, _ = self.req("POST", "/api/tasks", {"title": f"гонка {i}", "project": "zeta"})
            self.assertIn(st, (201, 409))
            if st == 201:
                made.append(res["item"]["id"])
        th.join(30)
        d = self.data()
        self.assertEqual(sorted(d.get("botMarks", [])), list(range(25)), "FAIL-сценарий: правки клиента API потеряны")
        ids = {t["id"] for t in d["tasks"]}
        self.assertTrue(set(made) <= ids, "правки страницы потеряны")
        self.assertGreaterEqual(len(made), 20)
        self.assertFalse(lock.exists())

    # --- / L1 / L3 ---
    def test_calendar_url_only_https_known_hosts(self):
        ok = ["https://calendar.google.com/calendar/ical/a%40group.calendar.google.com/private-x/basic.ics",
              "webcal://p01-caldav.icloud.com/published/2/abc", "https://outlook.office365.com/owa/calendar/x/reachcalendar.ics"]
        bad = ["https://127.0.0.1/x.ics", "https://localhost/x.ics", "https://169.254.169.254/latest", "https://192.168.1.1/a.ics",
               "https://evil.example.com/a.ics", "https://calendar.google.com.evil.com/a.ics", "https://u:p@calendar.google.com/a.ics",
               "https://calendar.google.com:8443/a.ics", "http://calendar.google.com/a.ics", "file:///C:/Windows/win.ini", "https://[abc/x"]
        for u in ok:
            S.normalize_url(u)
        for u in bad:
            with self.assertRaises(S.CalError, msg=u):
                S.normalize_url(u)
        env = os.environ.pop("MOYDEN_TEST_ICAL", None)
        try:
            with self.assertRaises(S.CalError):
                S.normalize_url("http://127.0.0.1:8080/x.ics")
        finally:
            if env is not None:
                os.environ["MOYDEN_TEST_ICAL"] = env
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": "https://[abc/x"})
        self.assertEqual(st, 400, "L1: кривой адрес — 400, а не 500")
        st, res, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": "https://169.254.169.254/latest"})
        self.assertEqual(st, 400)
        mask = S.Calendars.mask("https://p01-caldav.icloud.com/published/2/MTIzNDU2Nzg5MDEyMzQ1Njc4")
        self.assertNotIn("MTIz", mask, "L3: секретный сегмент iCloud не показывается")
        self.assertTrue(S.Calendars.mask("https://calendar.google.com/calendar/ical/x/private-y/basic.ics").endswith("/…/basic.ics"))

    def test_redirect_to_local_address_is_blocked(self):
        class Redir(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "https://169.254.169.254/latest/meta-data")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        port = free_port()
        srv = http.server.HTTPServer(("127.0.0.1", port), Redir)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cals, _ = self.app.calendars.load_cfg()
            t0 = time.monotonic()
            err = self.app.calendars.fetch_one({**cals[0], "url": f"http://127.0.0.1:{port}/a.ics"}, record=False)
            self.assertIsNotNone(err)
            self.assertIn("нужен секретный адрес", err, "R1: отказ именно из-за проверки перенаправления, а не из-за сети")
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_failed_check_does_not_spoil_working_calendar_status(self):
        st, _, _ = self.req("POST", "/api/calendars/connect", {"name": "Рабочий", "url": "https://evil.example.com/a.ics"})
        self.assertEqual(st, 400)
        self.assertFalse(any((v or {}).get("error") for v in self.app.calendars.status.values()), "L2")

    # --- цвет Google важнее calendars.local.json, даже если синхронизация устарела ---
    def write_sync(self, events, hours_ago=0):
        synced = (datetime.now(S.TZ) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        (self.tmp / "google-events.json").write_text(json.dumps({"synced_at": synced, "calendars": [
            {"id": "work", "name": "Рабочий", "color": "#E8833A"}, {"id": "personal", "name": "Личный", "color": "#4285F4"}],
            "events": events}, ensure_ascii=False), "utf-8")

    def test_google_color_wins_over_local_file(self):
        day = S.today()
        ev = [{"id": "a", "calendar": "work", "title": "встреча", "start": f"{day}T15:00:00+03:00", "end": f"{day}T15:30:00+03:00"}]
        for hours in (0, 72):  # свежая и устаревшая (3 суток) синхронизация
            self.write_sync(ev, hours)
            st, r, _ = self.req("GET", f"/api/events?from={day}&to={(datetime.fromisoformat(day) + timedelta(days=1)).date()}")
            names = {(c["name"], c["color"]) for c in r["calendars"]}
            self.assertNotIn(("Рабочий", "#4a6cf7"), names, f"синхронизация {hours} ч: локальный синий цвет победил")
            e = next(x for x in r["events"] if x["title"] == "встреча")
            self.assertEqual(r["calendars"][e["cal"]]["color"], "#E8833A")
            st, pub, _ = self.req("GET", "/api/calendars")
            self.assertEqual(next(c for c in pub["calendars"] if c["name"] == "Рабочий")["color"], "#E8833A")

    # --- L4, L5 ---
    def test_sync_yesterday_late_event_and_html_description(self):
        day = S.today()
        y = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
        self.write_sync([{"id": "late", "calendar": "work", "title": "вчера поздно", "start": f"{y}T23:00:00+03:00", "end": f"{day}T00:00:00+03:00"},
                         {"id": "h", "calendar": "work", "title": "с описанием", "start": f"{day}T10:00:00+03:00", "end": f"{day}T11:00:00+03:00",
                          "description": "<b>жирный</b><br>строка"}])
        st, r, _ = self.req("GET", f"/api/events?from={day}&to={(datetime.fromisoformat(day) + timedelta(days=1)).date()}")
        titles = [e["title"] for e in r["events"]]
        self.assertNotIn("вчера поздно", titles, "L5")
        self.assertEqual(next(e for e in r["events"] if e["title"] == "с описанием")["description"], "жирный\nстрока", "L4")

    # --- мусор от клиента API ---
    def test_unknown_state_counts_as_work_and_garbage_reminders(self):
        d = self.data()
        d["tasks"].append({"id": 990, "title": "состояние todo", "project": "zeta", "state": "todo", "due": "2020-01-01"})
        d["reminders"] = [5, "мусор", {"id": 3, "text": "ok", "date": S.today(), "time": "10:00", "done": False}]
        (self.tmp / "data.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        st, s, _ = self.req("GET", "/api/summary")
        self.assertIn(990, s["late"], "неизвестное состояние — «в работе», попадает в «Просрочено»")
        st, res, _ = self.req("POST", "/api/reminders", {"text": "новое", "date": S.today(), "time": "11:00"})
        self.assertEqual(st, 201, res)
        self.assertEqual(res["item"]["id"], 4)


if __name__ == "__main__":
    unittest.main()
