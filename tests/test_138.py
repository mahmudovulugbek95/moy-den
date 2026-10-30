"""таймер «Помидоро» (сервер — единственный писатель помидоро.json, отсчёт от отметки времени), API таймера, звук и
уведомление; (обои: без поля — без окна), (занятая программа — без AttachThreadInput), /4/5/7; ярлыки:
рабочего стола нет, старый — в Корзину, одна кнопка в панели задач (ID окна выводится из пути exe), подсказка «закрепить» — один раз.
Запуск: python -m unittest tests.test_138
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import winreg
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
os.environ.setdefault("MOYDEN_NO_TOAST", "1")  # тест не показывает уведомление на экране (страж в toast_run)
import server as S  # noqa: E402
import wallpaper as WPM  # noqa: E402
import app as A  # noqa: E402
import установщик as I  # noqa: E402

PS = lambda cmd: subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + cmd],
                                capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def make_lnk(path, target):
    q = lambda s: str(s).replace("'", "''")
    PS(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(path)}');$s.TargetPath='{q(target)}';$s.Save()")
    assert Path(path).exists(), path


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=os.environ.get("TEMP")))


def to_trash(p):
    """Тестовые данные — только в Корзину: SHFileOperationW с FOF_ALLOWUNDO, без зависимости от проверяемого кода."""
    import ctypes
    import ctypes.wintypes as W

    class OP(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("wFunc", W.UINT), ("pFrom", W.LPCWSTR), ("pTo", W.LPCWSTR), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", W.BOOL), ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", W.LPCWSTR)]
    if Path(p).exists():
        op = OP(None, 3, str(Path(p).resolve()) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))


# ---------------------------------------------------------------- таймер на сервере
T0 = datetime(2026, 10, 24, 10, 0, tzinfo=S.TZ).timestamp()


class Pomo138(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t138p-")
        self.path = self.tmp / "помидоро.json"
        self.doc = json.loads((HERE / "fixture-data.json").read_text("utf-8-sig"))
        self.doc["tasks"].append({"id": 500, "project": "zeta", "title": "Готовая задача", "state": "done", "due": "2026-10-20"})
        self.now = [T0]
        self.P = self.new()

    def tearDown(self):
        to_trash(self.tmp)

    def new(self):
        return S.Pomodoro(self.path, data_fn=lambda: (self.doc, b""), clock=lambda: self.now[0])

    def go(self, sec):
        self.now[0] += sec

    def test_start_counts_from_server_mark(self):
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["duration"], st["remaining"]), ("work", False, 1500, 1500))
        self.P.command({"action": "start"})
        self.go(600)
        st = self.P.snapshot()
        self.assertTrue(st["running"])
        self.assertAlmostEqual(st["remaining"], 900, places=2)
        self.assertEqual(st["endsAt"], round((T0 + 1500) * 1000), "окно считает остаток от отметки сервера, а не от своих таймеров")
        self.assertEqual(st["serverNow"], round((T0 + 600) * 1000))

    def test_pause_resume_keeps_elapsed(self):
        self.P.command({"action": "start"})
        self.go(300)
        st = self.P.command({"action": "pause"})
        self.assertEqual((st["running"], st["endsAt"]), (False, None))
        self.assertAlmostEqual(st["remaining"], 1200, places=2)
        self.go(1000)
        self.assertAlmostEqual(self.P.snapshot()["remaining"], 1200, places=2, msg="время паузы не идёт в зачёт")
        self.P.command({"action": "start"})
        self.go(200)
        self.assertAlmostEqual(self.P.snapshot()["remaining"], 1000, places=2)

    def test_work_end_counts_and_starts_break(self):  # перерыв сам не начинается — «Время вышло», кнопка «Перерыв»
        self.P.command({"action": "task", "task": 2})
        self.P.command({"action": "start"})
        self.go(1500.5)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["today"], st["task"], st["taskCount"], st["waiting"]), ("short", False, 1, 2, 1, True))
        self.assertEqual(st["expired"], {"from": "work", "at": round((T0 + 1500) * 1000)}, "время конца — момент конца, а не опроса")
        self.assertAlmostEqual(st["remaining"], 300, places=2, msg="перерыв ещё не начался")
        ev = self.P.tick()
        self.assertEqual([(e["from"], e["to"], e["task"]) for e in ev], [("work", "short", 2)])
        self.assertAlmostEqual(ev[0]["at"], T0 + 1500, places=3)
        self.assertEqual(self.P.tick(), [], "уведомление — ровно один раз")

    def test_break_end_waits_for_start(self):
        self.P.command({"action": "start"})
        self.go(1500)
        self.P.command({"action": "start"})  # «Перерыв» на карточке «Время вышло»
        self.go(300.2)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["remaining"], st["cycle"], st["waiting"]), ("work", False, 1500, 1, True))
        self.assertEqual([(e["from"], e["to"]) for e in self.P.tick()], [("short", "work")],
                         "конец помидора был 5 минут назад (программа не опрашивала) — о нём не сообщаем, о свежем конце перерыва — да")

    def test_fourth_work_long_break_then_cycle_resets(self):
        for _ in range(3):
            self.P.command({"action": "start"})
            self.go(1500)
            self.P.command({"action": "start"})  # перерыв — кнопкой
            self.go(300)
        self.assertEqual(self.P.snapshot()["cycle"], 3)
        self.P.command({"action": "start"})
        self.go(1500)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["duration"], st["today"], st["waiting"]), ("long", False, 900, 4, True))
        self.P.command({"action": "start"})
        self.go(900)
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["running"], st["cycle"], st["waiting"]), ("work", False, 0, True))

    def test_restart_continues_from_file(self):
        self.P.command({"action": "start"})
        self.go(100)
        self.P.snapshot()
        P2 = self.new()  # перезапуск программы (или окна): состояние из файла
        self.go(50)
        st = P2.snapshot()
        self.assertTrue(st["running"], "FAIL-сценарий: после перезапуска таймер остановился")
        self.assertAlmostEqual(st["remaining"], 1350, places=2, msg="FAIL-сценарий: перезапуск сбросил таймер на 25:00")

    def test_closed_past_end_counts_once_without_stale_alerts(self):
        self.P.command({"action": "task", "task": 3})
        self.P.command({"action": "start"})
        self.go(7200)  # программа была закрыта два часа
        P2 = self.new()
        st = P2.snapshot()
        self.assertEqual((st["phase"], st["running"], st["today"], st["taskCount"], st["waiting"]), ("short", False, 1, 1, True))
        self.assertEqual(P2.tick(), [], "о переходах двухчасовой давности не сообщаем")
        self.assertEqual(self.new().snapshot()["today"], 1, "помидор засчитан один раз")

    def test_reset_keeps_counters(self):
        self.P.command({"action": "start"})
        self.go(1500)
        st = self.P.command({"action": "reset"})  # сброс во время перерыва
        self.assertEqual((st["phase"], st["running"], st["remaining"], st["today"]), ("work", False, 1500, 1))
        self.P.command({"action": "start"})
        self.go(700)
        st = self.P.command({"action": "reset"})
        self.assertEqual((st["phase"], st["running"], st["remaining"], st["today"]), ("work", False, 1500, 1))

    def test_settings_validation_and_apply(self):
        self.P.command({"action": "start"})
        self.go(600)
        st = self.P.command({"action": "settings", "durations": {"work": 50, "short": 10, "long": 20, "every": 3}})
        self.assertEqual(st["durations"], {"work": 50, "short": 10, "long": 20, "every": 3})
        self.assertAlmostEqual(st["remaining"], 2400, places=2, msg="новая длительность текущего отрезка с учётом прошедшего")
        for bad in ({"work": 0}, {"work": 181}, {"short": "5"}, {"every": 1}, {"every": 13}, {"long": True}, {"short": 61}):
            with self.assertRaises(S.ApiError, msg=str(bad)) as cm:
                self.P.command({"action": "settings", "durations": bad})
            self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(S.ApiError):
            self.P.command({"action": "settings", "durations": [25]})
        self.assertEqual(self.P.snapshot()["durations"]["work"], 50, "неверная настройка ничего не записала")

    def test_task_binding_validation(self):
        self.assertEqual(self.P.command({"action": "task", "task": 2})["task"], 2)
        for bad in (99999, 500, "2", True, 2.5):
            with self.assertRaises(S.ApiError, msg=repr(bad)) as cm:
                self.P.command({"action": "task", "task": bad})
            self.assertEqual(cm.exception.status, 400)
            self.assertIn("Задача", cm.exception.message)
        self.assertEqual(self.P.snapshot()["task"], 2)
        self.assertIsNone(self.P.command({"action": "task", "task": None})["task"])

    def test_unknown_action(self):
        for body in ({"action": "skip"}, {}, {"action": 5}):
            with self.assertRaises(S.ApiError):
                self.P.command(body)

    def test_hostile_file_does_not_break_timer(self):  # валидный JSON с враждебными числами
        for bad in ('{"running": true, "startedAt": %d, "phase": "work"}' % int(T0 * 1000), '{"running": true, "startedAt": %f, "elapsed": 1e300}' % T0,
                    '{"running": true, "startedAt": %f, "elapsed": Infinity}' % T0, '{"running": true, "startedAt": -10000000, "days": {"2026-10-24": 2}}'):
            self.path.write_text(bad, "utf-8")
            P = self.new()
            st = P.snapshot()
            self.assertEqual((st["phase"], st["remaining"]), ("work", 1500), "битое время — отрезок с начала, без ошибки: " + bad)
            if "Infinity" not in bad and "1e300" not in bad:
                self.assertFalse(st["running"], "отметка в миллисекундах или до 1970 — таймер стоит: " + bad)
            self.assertEqual(P.command({"action": "reset"})["remaining"], 1500, "«Сброс» работает: " + bad)
        self.assertEqual(self.new().snapshot()["today"], 2, "счётчики из файла сохраняются")

    def test_shorten_mid_segment_ends_now_with_alert(self):  # укоротили 25 → 20 на 22-й минуте — конец «сейчас», со звуком
        self.P.command({"action": "start"})
        self.go(22 * 60)
        st = self.P.command({"action": "settings", "durations": {"work": 20}})
        self.assertEqual((st["phase"], st["today"]), ("short", 1))
        self.assertAlmostEqual(st["remaining"], 300, places=2, msg="перерыв начался сейчас, а не 2 минуты назад")
        self.assertEqual([(e["from"], e["to"]) for e in self.P.tick()], [("work", "short")])

    def test_save_failure_keeps_running_segment(self):  # : файл занят антивирусом — отрезок не сбрасывается
        self.P.command({"action": "start"})
        self.go(1500)
        with mock.patch.object(self.P, "_save", side_effect=PermissionError("занят")):
            st = self.P.snapshot()
            self.assertEqual((st["phase"], st["running"], st["today"], st["waiting"]), ("short", False, 1, True), "конец помидора засчитан, «Время вышло»")
            self.assertTrue(self.P.command({"action": "start"})["running"], "команда работает и без записи")
        self.P.command({"action": "pause"})
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["phase"], "short", "запись со следующим изменением")

    def test_foreign_day_keys_dropped(self):
        self.path.write_text('{"days": {"zzz": 5, "2026-10-24": 2}}', "utf-8")
        self.assertEqual(self.new().st["days"], {"2026-10-24": 2})

    def test_corrupt_file_gives_defaults(self):
        self.path.write_text("{битый", "utf-8")
        st = self.new().snapshot()
        self.assertEqual((st["phase"], st["running"], st["today"]), ("work", False, 0))

    def test_day_count_by_local_date_of_end(self):
        self.now[0] = datetime(2026, 10, 24, 23, 50, tzinfo=S.TZ).timestamp()
        self.P.command({"action": "start"})
        self.go(1500)  # закончился в 00:15 25.10 по Найроби
        self.assertEqual(self.P.snapshot()["today"], 1)
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["days"], {"2026-10-25": 1})

    def test_messages_literary(self):
        d = {"work": 25, "short": 5, "long": 15, "every": 4}
        m = S.pomodoro_message
        k = " — нажмите «Перерыв» на таймере."  # перерыв сам не начинается — текст называет кнопку
        self.assertEqual(m({"from": "work", "to": "short", "task": 2}, d, "Проверить макеты"),
                         "Помидор по задаче «Проверить макеты» закончился. Отдохните 5 минут" + k)
        self.assertEqual(m({"from": "work", "to": "short", "task": None}, dict(d, short=1)), "Помидор закончился. Отдохните 1 минуту" + k)
        self.assertEqual(m({"from": "work", "to": "short", "task": None}, dict(d, short=2)), "Помидор закончился. Отдохните 2 минуты" + k)
        self.assertEqual(m({"from": "work", "to": "long", "task": None}, d), "Помидор закончился. Пора на длинный перерыв: 15 минут" + k)
        self.assertEqual(m({"from": "work", "to": "long", "task": None}, dict(d, long=21)), "Помидор закончился. Пора на длинный перерыв: 21 минута" + k)
        self.assertEqual(m({"from": "short", "to": "work", "task": None}, d),
                         "Перерыв закончился. Нажмите «Работа» на таймере «Мой день», чтобы начать следующий помидор.")
        long_title = m({"from": "work", "to": "short", "task": 1}, d, "Т" * 300)
        self.assertLessEqual(len(long_title), 255, "уведомление трея — не длиннее 255 знаков")

    def test_run_loop_notifies_fresh_transition_once(self):
        self.P.command({"action": "start"})
        self.go(1500.2)
        got, stop = [], threading.Event()

        def notify(ev):
            got.append(ev)
            stop.set()
        threading.Thread(target=self.P.run, args=(notify,), kwargs={"interval": 0.01, "stop": stop}, daemon=True).start()
        self.assertTrue(stop.wait(3), "цикл таймера не сообщил о конце помидора")
        time.sleep(0.1)
        self.assertEqual([(e["from"], e["to"]) for e in got], [("work", "short")])


# ---------------------------------------------------------------- API таймера
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t138-")
        shutil.copyfile(HERE / "fixture-data.json", self.tmp / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", self.tmp / "settings.json")
        (self.tmp / "cal.json").write_text('{"calendars": []}', "utf-8")
        args = Namespace(data=str(self.tmp / "data.json"), calendars=str(self.tmp / "cal.json"), cache=str(self.tmp / "cache"), refresh=300,
                         settings=str(self.tmp / "settings.json"), weather_url="http://127.0.0.1:9/none", weather_cache=str(self.tmp / "wx.json"),
                         weather_refresh=1800, weather_retry=120, weather_max_age=3600, google_sync=str(self.tmp / "g.json"))
        self.app = S.App(args)
        self.port = free_port()
        self.srv = S.Server(("127.0.0.1", self.port), S.Handler)
        self.srv.app = self.app
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        to_trash(self.tmp)

    def req(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body or {}).encode() if method != "GET" else None,
                                   method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")


class PomoApi138(Base):
    def test_get_post_and_guards(self):
        st, res = self.req("GET", "/api/pomodoro")
        self.assertEqual(st, 200)
        self.assertEqual((res["phase"], res["running"], res["durations"]["work"]), ("work", False, 25))
        st, res = self.req("POST", "/api/pomodoro", {"action": "start"})
        self.assertEqual((st, res["running"]), (200, True))
        self.assertTrue((self.tmp / "помидоро.json").exists(), "состояние — в папке данных, рядом с data.json")
        st, res = self.req("POST", "/api/pomodoro", {"action": "task", "task": 2})
        self.assertEqual((st, res["task"]), (200, 2))
        st, res = self.req("POST", "/api/pomodoro", {"action": "nope"})
        self.assertEqual(st, 400)
        self.assertTrue(res["error"])
        st, _ = self.req("POST", "/api/pomodoro", {"action": "reset"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403, "чужой сайт таймером не управляет")
        st, res = self.req("GET", "/api/pomodoro")
        self.assertTrue(res["running"], "запрос чужого сайта ничего не изменил")
        data = json.loads((self.tmp / "data.json").read_text("utf-8"))
        self.assertNotIn("pomodoro", json.dumps(data, ensure_ascii=False), "data.json пользователя таймер не трогает")

    def test_app_notify_uses_task_title(self):
        got = []
        self.app.ui = type("U", (), {"pomodoro_alert": lambda s, t, **kw: got.append(t)})()
        at = time.time()
        self.app.pomodoro.st["waiting"] = {"from": "work", "at": at}  # сигнал — только пока «Время вышло» не подтверждено
        self.app.pomo_notify({"from": "work", "to": "short", "task": 2, "at": at})
        self.assertEqual(len(got), 1)
        self.assertIn("«Разбор референсов»", got[0])
        self.app.ui = None
        self.app.pomo_notify({"from": "short", "to": "work", "task": None, "at": time.time()})  # без окна — молча, без ошибки


# ---------------------------------------------------------------- программа: обои, окно, звук, подсказка
class Ui138(unittest.TestCase):
    def setUp(self):
        self.ui = A.UI(None)
        self.ui.wp, self.ui.wp_hwnd, self.ui.wp_host, self.ui.win = object(), 111, 222, object()

    def test_med1_failed_without_field_does_not_open_window(self):
        with mock.patch.object(A.WPM, "take_focus", return_value=(False, 333, True)), mock.patch.object(self.ui, "show_field") as sf:
            r = self.ui.wallpaper_focus("", "#/p/zeta")
            self.assertNotEqual(r, "window", "FAIL-сценарий щелчок не по полю открыл основное окно поверх всего")
            sf.assert_not_called()
            self.assertEqual(self.ui.wallpaper_focus("e-role", "#/people/4"), "window", "с полем — запасной путь как раньше")
            sf.assert_called_once_with("e-role", "#/people/4")

    def test_low2_release_with_busy_lock_keeps_previous_window(self):
        self.ui.wp_prev = 333
        self.ui.focus_lock.acquire()
        try:
            with mock.patch.object(A.WPM, "activate") as act:
                self.assertFalse(self.ui.wallpaper_release())
                act.assert_not_called()
        finally:
            self.ui.focus_lock.release()
        self.assertEqual(self.ui.wp_prev, 333, "FAIL-сценарий замок занят — окно для возврата потеряно")
        with mock.patch.object(A.WPM, "activate", return_value=True) as act:
            self.assertTrue(self.ui.wallpaper_release())
            act.assert_called_once_with(333, only_from=222)

    def test_low4_show_does_not_shrink_maximized_window(self):
        win = mock.MagicMock()
        self.ui.win = win
        fake = mock.MagicMock()
        fake.FindWindowW.return_value = 55
        fake.IsIconic.return_value = 0
        fake.IsZoomed.return_value = 1
        with mock.patch.object(A, "u32", fake), mock.patch.object(A, "find_own", lambda title: fake.FindWindowW(None, title)):
            self.ui.show()
        win.restore.assert_not_called()
        self.assertNotIn(mock.call(55, 9), fake.ShowWindow.call_args_list, "SW_RESTORE уменьшает развёрнутое окно")
        fake.IsIconic.return_value, fake.IsZoomed.return_value = 1, 0
        fake.GetWindowPlacement.side_effect = lambda h, p: setattr(p._obj, "flags", 0) or 1
        with mock.patch.object(A, "u32", fake), mock.patch.object(A, "find_own", lambda title: fake.FindWindowW(None, title)):
            self.ui.show()
        self.assertTrue(win.restore.called or mock.call(55, 9) in fake.ShowWindow.call_args_list, "свёрнутое окно разворачивается")
        win.reset_mock()
        fake.ShowWindow.reset_mock()
        fake.GetWindowPlacement.side_effect = lambda h, p: setattr(p._obj, "flags", 2) or 1  # свёрнуто из развёрнутого
        with mock.patch.object(A, "u32", fake), mock.patch.object(A, "find_own", lambda title: fake.FindWindowW(None, title)):
            self.ui.show()
        win.restore.assert_not_called()
        self.assertIn(mock.call(55, 3), fake.ShowWindow.call_args_list, "свёрнутое из развёрнутого — снова развёрнутым")

    def test_low7_hwnd_and_host_read_as_one_pair(self):
        ui = self.ui
        self.assertTrue(hasattr(ui, "_wp_pin"), "FAIL-сценарий окно обоев и его хозяин хранятся раздельно")
        base = type(ui)

        class Swap(base):  # сторож пересоздал обои ровно между чтением окна и чтением хозяина
            @property
            def wp_hwnd(self):
                v = base.wp_hwnd.fget(self)
                self._wp_pin = (911, 922)
                return v

            @wp_hwnd.setter
            def wp_hwnd(self, v):
                base.wp_hwnd.fset(self, v)
        ui.__class__ = Swap
        ui._wp_pin = (111, 222)
        seen = []
        with mock.patch.object(A.WPM, "take_focus", side_effect=lambda h, host: seen.append((h, host)) or (True, None, False)):
            ui.wallpaper_focus("e-role", "")
        self.assertIn(seen[0], [(111, 222), (911, 922)], f"окно и хозяин из разных включений обоев: {seen}")

    def test_alert_plays_sound_and_tray_notification(self):
        self.ui.icon = mock.MagicMock()
        with mock.patch.object(A, "winsound") as ws, mock.patch.object(A, "toast_run", return_value="не показан (код 1): проверка"):
            self.ui.pomodoro_alert("Помидор закончился. Отдохните 5 минут.")  # тост не показался — уведомление трея
        self.assertTrue(ws.PlaySound.called or ws.MessageBeep.called, "звук по окончанию отрезка")
        self.ui.icon.notify.assert_called_once_with("Помидор закончился. Отдохните 5 минут.", A.TITLE)

    def test_aumid_same_for_window_and_shortcut(self):
        self.assertIsNone(A.aumid_for(True, ""), "собранная программа: ID выводится из пути exe — как у ярлыка «Пуска» и закреплённого")
        self.assertIsNone(A.aumid_for(True, "проверочный экземпляр138"))
        self.assertEqual(A.aumid_for(False, ""), "MoyDen.App", "запуск из исходников (pythonw) — своя кнопка")

    def test_pin_hint_once_only_when_not_pinned(self):
        self.ui.icon = mock.MagicMock()  # подсказка — уведомлением из трея
        tmp = tmpdir("t138s-")
        try:
            with mock.patch.object(A, "STATE", tmp / "окно.json"), mock.patch.object(A, "FROZEN", True), \
                    mock.patch.object(A.WPM, "taskbar_pinned", return_value=False) as tp, mock.patch.object(self.ui, "_tray") as tr:
                self.assertTrue(self.ui.pin_hint())
                tr.assert_called_once()
                self.assertIn("Закрепить на панели задач", tr.call_args[0][0])
                self.assertFalse(self.ui.pin_hint(), "подсказка — один раз")
                self.assertEqual(tr.call_count, 1)
                tp.assert_called_once()
            (tmp / "окно.json").write_text("{}", "utf-8")
            with mock.patch.object(A, "STATE", tmp / "окно.json"), mock.patch.object(A, "FROZEN", True), \
                    mock.patch.object(A.WPM, "taskbar_pinned", return_value=True), mock.patch.object(self.ui, "_tray") as tr:
                self.assertFalse(self.ui.pin_hint(), "уже закреплена — подсказки нет")
                tr.assert_not_called()
            with mock.patch.object(A, "STATE", tmp / "окно2.json"), mock.patch.object(A, "FROZEN", False), \
                    mock.patch.object(self.ui, "_tray") as tr:
                self.assertFalse(self.ui.pin_hint(), "из исходников — без подсказки")
                tr.assert_not_called()
        finally:
            to_trash(tmp)


# ---------------------------------------------------------------- Windows API на подделке
class Wallpaper138(unittest.TestCase):
    def setUp(self):
        self.st = {"fg": 900, "focus": 901, "attached": set(), "log": [], "busy": set()}
        st = self.st
        fake = mock.MagicMock()
        fake.GetForegroundWindow.side_effect = lambda: st["fg"]
        fake.GetWindowThreadProcessId.side_effect = lambda h, p: {900: 9, 222: 2, 111: 1, 112: 1, 333: 3}.get(h, 7)
        fake.GetAncestor.side_effect = lambda h, f: 222 if h in (111, 112, 222) else h
        fake.GetWindow.side_effect = lambda h, c: 112 if h == 111 else None
        fake.IsChild.side_effect = lambda a, b: a == 111 and b == 112
        fake.IsWindow.return_value = fake.IsWindowVisible.return_value = 1
        fake.IsIconic.return_value = 0
        fake.IsHungAppWindow.return_value = 0

        def smto(h, msg, w, l, flags, ms, res):
            st["log"].append(("probe", h, msg, flags, ms))
            return 0 if h in st["busy"] else 1
        fake.SendMessageTimeoutW.side_effect = smto

        def gti(tid, p):
            p._obj.hwndFocus = st["focus"]
            return 1
        fake.GetGUIThreadInfo.side_effect = gti

        def attach(a, b, on):
            (st["attached"].add if on else st["attached"].discard)(b)
            st["log"].append(("attach" if on else "detach", b))
            return 1
        fake.AttachThreadInput.side_effect = attach

        def sfw(h):
            st["log"].append(("fg", h))
            st["fg"] = h
            return 1
        fake.SetForegroundWindow.side_effect = sfw

        def sf(h):
            st["log"].append(("focus", h))
            if st["fg"] == 222:
                st["focus"] = h
            return 0
        fake.SetFocus.side_effect = sf
        self.p = mock.patch.object(WPM, "u", fake)
        self.p.start()
        self.ps = mock.patch.object(WPM.time, "sleep")
        self.ps.start()

    def tearDown(self):
        self.p.stop()
        self.ps.stop()

    def test_med2_busy_program_no_attach_and_fallback(self):
        self.st["busy"].add(900)  # программа пользователя занята (1–4 с), IsHungAppWindow ещё 0
        t0 = time.time()
        ok, prev, sw = WPM.take_focus(111, 222)
        self.assertEqual((ok, prev, sw), (False, 900, True), "не ответила за 150 мс — сразу запасной путь")
        self.assertNotIn(("attach", 9), self.st["log"], "FAIL-сценарий ввод присоединён к занятой программе")
        self.assertNotIn(("fg", 222), self.st["log"])
        self.assertLess(time.time() - t0, 1)
        probe = [x for x in self.st["log"] if x[0] == "probe"][0]
        self.assertEqual((probe[1], probe[2]), (900, 0), "проба — WM_NULL окну программы")
        self.assertTrue(probe[3] & 0x0002, "SMTO_ABORTIFHUNG")
        self.assertLessEqual(probe[4], 150)

    def test_med2_responsive_program_normal_flow(self):
        ok, prev, sw = WPM.take_focus(111, 222)
        self.assertEqual((ok, prev, sw), (True, 900, True))
        self.assertIn(("attach", 9), self.st["log"])
        self.assertEqual(self.st["attached"], set())

    def test_med2_activate_busy_foreground_not_attached(self):
        self.st["busy"].add(900)
        WPM.activate(333)
        self.assertNotIn(("attach", 9), self.st["log"])
        self.assertEqual(self.st["attached"], set())

    def test_low5_window_on_other_virtual_desktop_not_activated(self):
        self.st["fg"] = 222
        with mock.patch.object(WPM, "_cloaked", return_value=True):
            self.assertFalse(WPM.activate(333, only_from=222), "окно другого виртуального стола — не переключаем")
        self.assertEqual(self.st["fg"], 222)
        with mock.patch.object(WPM, "_cloaked", return_value=False):
            self.assertTrue(WPM.activate(333, only_from=222))


class Shortcuts138(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t138l-")

    def tearDown(self):
        to_trash(self.tmp)

    def test_shortcut_target_and_pinned(self):
        exe = Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe"
        make_lnk(self.tmp / "Блокнот.lnk", exe)
        self.assertEqual(Path(WPM.shortcut_target(self.tmp / "Блокнот.lnk")).resolve(), exe.resolve())
        self.assertTrue(WPM.taskbar_pinned(exe, folder=self.tmp))
        self.assertFalse(WPM.taskbar_pinned(self.tmp / "Мой день.exe", folder=self.tmp))
        self.assertFalse(WPM.taskbar_pinned(exe, folder=self.tmp / "нет"))

    def test_recycle_moves_to_trash(self):
        p = self.tmp / f"корзина-т138-{os.getpid()}-{time.time_ns()}.lnk"
        make_lnk(p, Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe")
        self.assertTrue(I.recycle(p))
        self.assertFalse(p.exists())
        names = PS("(New-Object -ComObject Shell.Application).NameSpace(10).Items() | ForEach-Object { $_.Name }")
        self.assertIn(p.stem, names, "файл в Корзине, а не удалён насовсем")


class Installer138(unittest.TestCase):
    NAME, KEY = "Мой день (т138)", "MoyDen.T138"

    def setUp(self):
        self.tmp = tmpdir("t138i-")
        self.t, self.d = self.tmp / "Программа", self.tmp / "Данные"
        self.dirs = {k: self.tmp / k for k in ("Пуск", "РС", "Авто")}
        for p in [self.d, *self.dirs.values()]:
            p.mkdir()
        shutil.copyfile(HERE / "fixture-data.json", self.d / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", self.d / "settings.json")
        self.pay = self.tmp / "payload"
        self.pay.mkdir()
        (self.pay / "MoyDen.exe").write_bytes(b"MZ test 138")
        shutil.copyfile(DEV / "мой_день.ico", self.pay / "мой_день.ico")
        self.recycled = []

    def tearDown(self):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{I.UNINSTALL_ROOT}\{self.KEY}")
        except OSError:
            pass
        to_trash(self.tmp)

    def run_install(self):
        a = Namespace(target=str(self.t), data=str(self.d), name=self.NAME, regkey=self.KEY, port=8697, instance="т138", manifest=None,
                      desktop_dir=str(self.dirs["РС"]), startup_dir=str(self.dirs["Авто"]), start_dir=str(self.dirs["Пуск"]),
                      replace_old=False, wallpaper_on=False, launch=False, uninstall=False, quiet=True)

        def rec(p):
            self.recycled.append(Path(p))
            Path(p).unlink()
            return True
        with mock.patch.object(I, "PAYLOAD", self.pay), mock.patch.object(I, "recycle", side_effect=rec), \
                mock.patch.object(I, "other_build", return_value=None):  # компьютер проверки — как без другой сборки «Мой день»
            I.install(a)
        return json.loads((self.t / "установка.json").read_text("utf-8"))

    def test_no_desktop_link_and_old_one_to_trash(self):
        self.t.mkdir()
        desk = self.dirs["РС"] / f"{self.NAME}.lnk"
        make_lnk(desk, self.t / f"{self.NAME}.exe")  # ярлык прежней версии на рабочем столе
        m = self.run_install()
        self.assertFalse(desk.exists(), "FAIL-сценарий ярлык на рабочем столе есть")
        self.assertEqual(self.recycled, [desk], "старый ярлык — в Корзину, не насовсем")
        self.assertTrue((self.d / "резерв" / "ярлыки-до-установки" / f"Рабочий стол — {self.NAME} (до 1.0.0).lnk").exists(), "копия для отката")
        start = self.dirs["Пуск"] / f"{self.NAME}.lnk"
        self.assertTrue(start.exists(), "ярлык «Пуска» остаётся: от него зависит закрепление")
        self.assertEqual(Path(WPM.shortcut_target(start)).resolve(), (self.t / f"{self.NAME}.exe").resolve())
        self.assertTrue((self.dirs["Авто"] / f"{self.NAME}.lnk").exists())
        self.assertNotIn(str(desk), m["links"])
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{I.UNINSTALL_ROOT}\{self.KEY}") as k:
            self.assertEqual(winreg.QueryValueEx(k, "DisplayVersion")[0], "1.0.0")

    def test_foreign_desktop_link_kept(self):
        desk = self.dirs["РС"] / f"{self.NAME}.lnk"
        make_lnk(desk, Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe")
        self.run_install()
        self.assertTrue(desk.exists(), "чужой ярлык с тем же именем не трогаем")
        self.assertEqual(self.recycled, [])

    def test_no_desktop_link_on_clean_install(self):
        self.run_install()
        self.assertEqual(list(self.dirs["РС"].iterdir()), [])


class Versions138(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(A.VERSION, "1.0.0")
        self.assertEqual(I.VERSION, "1.0.0")

    def test_page_low3_low6_and_no_goriт(self):
        html = (SRC / "дашборд.html").read_text("utf-8")
        self.assertNotIn('wpKbd=j&&j.ok?j.focus==="wallpaper":null', html, "ответ «занято» не должен сбрасывать признак клавиатуры")
        self.assertNotIn('appPost("/api/app/wallpaper-release").catch(()=>{})', html, "ошибка запроса — в журнал, а не молча")
        self.assertNotRegex(html.lower(), r"горит|горящ", "23: слова «горит» в интерфейсе нет")
        self.assertIn("Срочных задач нет. Можно заняться важным.", html)


if __name__ == "__main__":
    unittest.main()
