"""окно, спрятанное крестиком в трей, при следующем открытии показывает календарь «День» на сегодня — app.py после hide()
зовёт window.calHome() на той же странице (она не перезагружается); при выходе из программы — не зовёт; ошибка страницы — в журнал, не падение.
Запуск: python -m unittest tests.test_1311"""
import os
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
import app as A  # noqa: E402


class FakeWin:
    def __init__(self, fail=False):
        self.calls, self.fail, self.done = [], fail, threading.Event()

    def hide(self):
        self.calls.append(("hide",))

    def evaluate_js(self, js):
        self.calls.append(("js", js))
        self.done.set()
        if self.fail:
            raise RuntimeError("страница не готова")


class CalHome1311(unittest.TestCase):
    def test_closing_hides_and_resets_calendar(self):
        ui = A.UI(None)
        ui.win = FakeWin()
        self.assertFalse(ui.on_closing(), "крестик прячет окно, а не закрывает программу")
        self.assertTrue(ui.win.done.wait(3), "FAIL-сценарий страница не получила calHome — окно откроется в прежнем виде")
        self.assertEqual(ui.win.calls[0], ("hide",), "сначала окно прячется, потом календарь (пользователь не видит перерисовку)")
        self.assertEqual(ui.win.calls[1], ("js", "window.calHome&&window.calHome()"))

    def test_quit_does_not_touch_page(self):
        ui = A.UI(None)
        ui.win = FakeWin()
        ui.quitting = True
        self.assertTrue(ui.on_closing())
        self.assertFalse(ui.win.done.wait(0.5), "при выходе страницу не трогаем")
        self.assertEqual(ui.win.calls, [])

    def test_page_error_is_logged_not_raised(self):
        ui = A.UI(None)
        ui.win = FakeWin(fail=True)
        with self.assertLogs(A.log, "WARNING") as lg:
            self.assertFalse(ui.on_closing())
            self.assertTrue(ui.win.done.wait(3))
            for th in [t for t in threading.enumerate() if t.name == "cal-home"]:
                th.join(3)
        self.assertTrue(any("cal home" in m for m in lg.output), lg.output)


class FakeU32:
    def __init__(self, visible, iconic):
        self.visible, self.iconic = visible, iconic

    def FindWindowW(self, *a):
        return 111

    def IsWindowVisible(self, h):
        return self.visible

    def IsIconic(self, h):
        return self.iconic

    def IsZoomed(self, h):
        return 0

    def __getattr__(self, name):  # ShowWindow, SetForegroundWindow, GetWindowPlacement — без действия
        return lambda *a, **k: 0


class ShowWin(FakeWin):
    def show(self):
        self.calls.append(("show",))

    def restore(self):
        self.calls.append(("restore",))


class Reopen1311(unittest.TestCase):
    """все входы, которыми пользователь снова открывает окно, — календарь «День» на сегодня; видимое окно не трогаем."""
    def run_show(self, visible, iconic):
        from unittest import mock
        ui = A.UI(None)
        ui.win = ShowWin()
        with mock.patch.object(A, "u32", FakeU32(visible, iconic)), mock.patch.object(A, "find_own", lambda title: 111):
            ui.show()
        return ui.win

    def test_tray_open_hidden_window(self):  # крестик → трей «Открыть» / повторный запуск
        w = self.run_show(visible=0, iconic=0)
        self.assertTrue(w.done.wait(3), "FAIL-сценарий окно из трея открылось в прежнем виде")
        self.assertIn(("js", "window.calHome&&window.calHome()"), w.calls)

    def test_tray_open_minimized_window(self):  # свёрнуто → трей «Открыть»
        w = self.run_show(visible=1, iconic=1)
        self.assertTrue(w.done.wait(3), "FAIL-сценарий свёрнутое окно из трея — вид не сброшен")
        self.assertIn(("js", "window.calHome&&window.calHome()"), w.calls)

    def test_visible_window_kept(self):  # окно и так на экране — выбранный вид держится
        w = self.run_show(visible=1, iconic=0)
        self.assertFalse(w.done.wait(0.7), "видимое окно: вид не сбрасываем")

    def test_minimize_resets(self):  # свернули (кнопка, Win+D) → восстановление с панели задач
        ui = A.UI(None)
        ui.win = ShowWin()
        ui.on_minimized()
        self.assertTrue(ui.win.done.wait(3))
        self.assertEqual(ui.win.calls, [("js", "window.calHome&&window.calHome()")])

    def test_minimized_event_wired(self):
        src = (SRC / "app.py").read_text("utf-8")
        self.assertIn("ui.win.events.minimized += ui.on_minimized", src, "событие «свёрнуто» подключено к сбросу")


class DoneFrom1311(unittest.TestCase):
    """«ждём ответа» — сервер помнит, из какого состояния задачу выполнили."""
    def test_wait_done_back(self):
        import server as S
        doc = {"projects": [{"id": "p", "name": "П"}], "tasks": [{"id": 1, "project": "p", "title": "Т", "state": "wait"}, {"id": 2, "project": "p", "title": "У", "state": "work"}], "employees": []}
        S.patch_task(doc, 1, {"state": "done"})
        t1 = doc["tasks"][0]
        self.assertEqual((t1["state"], t1.get("doneFrom")), ("done", "wait"), "FAIL-сценарий прежнее состояние не запомнено")
        S.patch_task(doc, 1, {"state": "wait"})
        self.assertEqual(t1["state"], "wait")
        self.assertNotIn("doneFrom", t1, "вне done метки нет")
        S.patch_task(doc, 2, {"state": "done"})
        self.assertNotIn("doneFrom", doc["tasks"][1], "из «в работе» — без метки")
        S.patch_task(doc, 1, {"state": "done"}); S.patch_task(doc, 1, {"state": "done"})
        self.assertEqual(t1.get("doneFrom"), "wait", "повторное done метку не стирает")

    def test_finish_project_keeps_wait(self):
        import server as S
        src = __import__("inspect").getsource(S)
        self.assertIn('t["doneFrom"] = "wait"', src.split("def reopen_tasks")[0].split("closedOnFinish")[-1], "«Завершить проект» помнит «ждём ответа» у каждой задачи")
        self.assertIn('t.pop("doneFrom", None)', src.split("def reopen_tasks")[1].split("\ndef ")[0], "возврат проекта снимает метку")


if __name__ == "__main__":
    unittest.main()
