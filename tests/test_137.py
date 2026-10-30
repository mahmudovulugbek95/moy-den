"""отчество (middleName) через API; управление клавиатурой обоев (/api/app/wallpaper-focus, -release) и логика app.UI
на подделках; wallpaper.take_focus / activate на подделках Windows API.
Запуск: python -m unittest tests.test_137
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from argparse import Namespace
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
import server as S  # noqa: E402
import wallpaper as WPM  # noqa: E402
import app as A  # noqa: E402


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t137-", dir=os.environ.get("TEMP")))
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
        shutil.rmtree(self.tmp, ignore_errors=True)

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

    def emp(self, eid):
        return next(e for e in json.loads((self.tmp / "data.json").read_text("utf-8"))["employees"] if e["id"] == eid)


class MiddleName137(Base):
    def test_patch_saves_and_validates(self):
        st, res = self.req("PATCH", "/api/employees/4", {"middleName": "  Игоревич "})
        self.assertEqual(st, 200)
        self.assertEqual(self.emp(4).get("middleName"), "Игоревич", "FAIL-сценарий сервер молча игнорировал отчество")
        self.assertEqual(self.emp(4)["name"], "Влад", "полное имя для задач не меняется (имя + фамилия)")
        st, res = self.req("PATCH", "/api/employees/4", {"middleName": "О" * 41})
        self.assertEqual(st, 400)
        self.assertIn("Отчество", res["error"])
        self.assertEqual(self.emp(4).get("middleName"), "Игоревич", "неверное значение не записано")
        st, _ = self.req("PATCH", "/api/employees/4", {"middleName": 5})
        self.assertEqual(st, 400)
        st, _ = self.req("PATCH", "/api/employees/4", {"role": "арт-директор"})
        self.assertEqual(self.emp(4).get("middleName"), "Игоревич", "правка другого поля отчество не трогает")
        st, _ = self.req("PATCH", "/api/employees/4", {"middleName": ""})
        self.assertNotIn("middleName", self.emp(4), "пустое отчество не хранится")

    def test_create_with_middle_name_and_migration_keeps_it(self):
        st, res = self.req("POST", "/api/employees", {"firstName": "Марк", "lastName": "Орлов", "middleName": "Маркович младший"})
        self.assertEqual(st, 201)
        self.assertEqual(res["item"]["middleName"], "Маркович младший")
        st, res = self.req("POST", "/api/employees", {"firstName": "Ева", "lastName": "Б"})
        self.assertNotIn("middleName", res["item"])
        d = json.loads((self.tmp / "data.json").read_text("utf-8"))
        d["employees"][0]["middleName"] = "Егорович"  # поле вписано в data.json раньше сервера
        self.assertFalse(S.migrate(d) and "middleName" not in d["employees"][0])
        self.assertEqual(d["employees"][0]["middleName"], "Егорович", "миграция отчество не удаляет")


class FakeUI:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def wallpaper_focus(self, fid, hash_):
        self.calls.append(("focus", fid, hash_))
        return "wallpaper"

    def wallpaper_release(self):
        self.calls.append("release")
        return True


class WallpaperApi137(Base):
    def test_without_app_window(self):
        st, res = self.req("POST", "/api/app/wallpaper-focus", {"fid": "e-role", "hash": "#/people/4"})
        self.assertEqual(st, 409)

    def test_focus_release_and_validation(self):
        ui = self.app.ui = FakeUI()
        st, res = self.req("POST", "/api/app/wallpaper-focus", {"fid": "e-role", "hash": "#/people/4"})
        self.assertEqual((st, res.get("focus")), (200, "wallpaper"))
        self.assertEqual(ui.calls[-1], ("focus", "e-role", "#/people/4"))
        st, res = self.req("POST", "/api/app/wallpaper-release", {})
        self.assertEqual((st, res.get("released")), (200, True))
        n = len(ui.calls)
        for bad in ({"fid": "x\"];alert(1)//"}, {"fid": "a" * 81}, {"fid": 5}, {"hash": "javascript:alert(1)"}, {"hash": "#" + "x" * 200}):
            st, _ = self.req("POST", "/api/app/wallpaper-focus", bad)
            self.assertEqual(st, 200, "неверное поле не отключает передачу клавиатуры")
            self.assertEqual(ui.calls[-1], ("focus", "", ""), f"неверный ввод дошёл до программы: {bad}")
        n = len(ui.calls)
        st, _ = self.req("POST", "/api/app/wallpaper-focus", {"fid": "e-role"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403, "чужой сайт не управляет фокусом")
        self.assertEqual(len(ui.calls), n, "запрос чужого сайта до программы не дошёл")


class UiFocus137(unittest.TestCase):
    def setUp(self):
        self.ui = A.UI(None)
        self.ui.wp, self.ui.wp_hwnd, self.ui.wp_host, self.ui.win = object(), 111, 222, object()

    def test_focus_ok_keeps_previous_window_and_release_returns_it(self):
        with mock.patch.object(A.WPM, "take_focus", return_value=(True, 333, True)) as tf, mock.patch.object(A.WPM, "activate", return_value=True) as act:
            self.assertEqual(self.ui.wallpaper_focus("e-role", "#/people/4"), "wallpaper")
            tf.assert_called_once_with(111, 222)
            self.assertTrue(self.ui.wallpaper_release())
            act.assert_called_once_with(333, only_from=222)
            self.assertFalse(self.ui.wallpaper_release(), "второй раз возвращать нечего")
            self.assertEqual(act.call_count, 1)

    def test_desktop_was_active_clears_old_previous(self):
        self.ui.wp_prev = 444  # окно из прошлого щелчка
        with mock.patch.object(A.WPM, "take_focus", return_value=(True, None, True)), mock.patch.object(A.WPM, "activate") as act:
            self.ui.wallpaper_focus("e-role", "")
            self.assertIsNone(self.ui.wp_prev, "M3: был активен рабочий стол — возвращать клавиатуру некуда")
            self.assertFalse(self.ui.wallpaper_release())
            act.assert_not_called()
        self.ui.wp_prev = 444
        with mock.patch.object(A.WPM, "take_focus", return_value=(True, None, False)):
            self.ui.wallpaper_focus("e-role", "")
        self.assertEqual(self.ui.wp_prev, 444, "клавиатура уже была у обоев — прежнее окно не забывается")

    def test_busy_lock_returns_busy(self):
        self.ui.focus_lock.acquire()
        try:
            with mock.patch.object(A.WPM, "take_focus") as tf:  # замок занят другим запросом — ждём ≤ 2 с и отвечаем «занято»
                t0 = __import__("time").time()
                self.assertEqual(self.ui.wallpaper_focus("e-role", ""), "busy")
                self.assertLess(__import__("time").time() - t0, 3)
                tf.assert_not_called()
        finally:
            self.ui.focus_lock.release()

    def test_focus_failed_opens_window_with_field(self):
        with mock.patch.object(A.WPM, "take_focus", return_value=(False, 333, True)), mock.patch.object(self.ui, "show_field") as sf:
            self.assertEqual(self.ui.wallpaper_focus("e-role", "#/people/4"), "window")
            sf.assert_called_once_with("e-role", "#/people/4")

    def test_wallpaper_off(self):
        self.ui.wp = None
        with mock.patch.object(A.WPM, "take_focus") as tf:
            self.assertEqual(self.ui.wallpaper_focus("e-role", ""), "off")
            tf.assert_not_called()

    def test_show_field_js_is_escaped(self):
        js = []
        self.ui.win = type("W", (), {"evaluate_js": lambda s, x: js.append(x)})()
        with mock.patch.object(self.ui, "show"), mock.patch.object(A.WPM, "activate"), mock.patch.object(A.threading, "Thread") as th:
            self.ui.show_field('e-role"];x', "#/people/4")
            th.call_args.kwargs["target"]()
        self.assertIn(json.dumps('e-role"];x'), js[0], "значения подставляются строковыми литералами JSON")


class TakeFocus137(unittest.TestCase):
    """take_focus на подделке Windows API: порядок «рабочий стол активен → фокус окну обоев», присоединение ввода снимается."""

    def setUp(self):
        self.st = {"fg": 900, "focus": 901, "attached": set(), "log": []}
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
        st["focus_ok_from"] = 1  # с какой попытки SetFocus срабатывает

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
            st["tries"] = st.get("tries", 0) + 1
            if st["fg"] == 222 and st["tries"] >= st["focus_ok_from"]:  # фокус переходит, только когда активен хозяин обоев
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

    def test_desktop_activated_then_focus_to_webview(self):
        ok, prev, sw = WPM.take_focus(111, 222)
        self.assertTrue(sw)
        self.assertEqual((ok, prev), (True, 900))
        self.assertTrue(WPM.take_focus(111, 222)[2] is False, "второй вызов: клавиатура уже у обоев — не переключали")
        kinds = [x for x in self.st["log"] if x[0] in ("fg", "focus")]
        self.assertEqual(kinds, [("fg", 222), ("focus", 112)], "сначала рабочий стол активен, затем фокус элементу WebView2")
        self.assertEqual(self.st["attached"], set(), "присоединение ввода снято")

    def test_already_focused_does_nothing(self):
        self.st["fg"], self.st["focus"] = 222, 112
        self.assertEqual(WPM.take_focus(111, 222), (True, None, False))
        self.assertEqual(self.st["log"], [])

    def test_second_try_and_failure(self):
        self.st["focus_ok_from"] = 2
        self.assertEqual(WPM.take_focus(111, 222), (True, 900, True), "первая попытка не удалась — вторая отдаёт фокус")
        self.st.update(fg=900, focus=901, tries=0, focus_ok_from=99)
        self.assertEqual(WPM.take_focus(111, 222), (False, 900, True), "обе попытки неудачны — честный отказ (основное окно)")
        self.assertEqual(self.st["attached"], set())

    def test_desktop_was_active(self):
        self.st["fg"] = 222  # активен сам рабочий стол, фокус у значков
        self.assertEqual(WPM.take_focus(111, 222), (True, None, True))
        self.assertNotIn(("fg", 222), self.st["log"], "рабочий стол уже активен — SetForegroundWindow не нужен")

    def test_activate_refuses_hidden_or_minimized(self):
        self.st["fg"] = 222
        WPM.u.IsIconic.return_value = 1
        self.assertFalse(WPM.activate(333, only_from=222))
        WPM.u.IsIconic.return_value = 0
        WPM.u.IsWindowVisible.return_value = 0
        self.assertFalse(WPM.activate(333, only_from=222))
        self.assertEqual(self.st["fg"], 222)

    def test_activate_only_from_host(self):
        self.st["fg"] = 222
        self.assertTrue(WPM.activate(333, only_from=222))
        self.assertEqual(self.st["fg"], 333)
        self.st["fg"] = 900
        self.assertFalse(WPM.activate(333, only_from=222), "пользователь уже в другой программе — не трогаем")
        self.assertEqual(self.st["fg"], 900)
        self.assertEqual(self.st["attached"], set())


if __name__ == "__main__":
    unittest.main()
