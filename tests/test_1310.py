"""замок data.json.lock — старый замок, который нельзя удалить (только чтение), больше не крутит сервер вечно: в пределах wait — 409
с путём к замку; постоянный PermissionError без замка — свой текст «нет доступа»; папка на месте замка — свой текст; занятый замок — прежний
текст и путь. settings.json из 100 000 скобок и широта из 401 цифры не роняют /api/settings и погоду. помидоро.json — корректный JSON,
но не объект → копия .bad.
Запуск: python -m unittest tests.test_1310"""
import json
import os
import stat
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(HERE))
import server as S  # noqa: E402
from test_138 import to_trash, tmpdir  # noqa: E402


def store(tmp):
    st = S.Store(tmp / "data.json")
    (tmp / "data.json").write_text("{}", "utf-8")
    return st


def run_lock(st, wait, limit):
    """file_lock в отдельном потоке: на 1.3.9 ветка «старый замок не удаляется» крутится вечно — тест не должен виснуть вместе с ней."""
    box = {}

    def go():
        try:
            with st.file_lock(wait=wait):
                box["взят"] = True
        except S.ApiError as e:
            box["ошибка"] = e
        except BaseException as e:  # noqa: BLE001 — любое другое падение тоже итог
            box["иное"] = e
    th = threading.Thread(target=go, daemon=True)
    t0 = time.monotonic()
    th.start()
    th.join(limit)
    return box, th.is_alive(), time.monotonic() - t0


class Lock1310(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t1310l-")
        self.st = store(self.tmp)
        self.lock = self.tmp / "data.json.lock"

    def tearDown(self):
        if self.lock.exists() and self.lock.is_file():
            os.chmod(self.lock, stat.S_IWRITE | stat.S_IREAD)
        to_trash(self.tmp)

    def test_stale_lock_cannot_be_removed_is_409_with_path_in_time(self):
        self.lock.write_bytes(b"old")
        old = time.time() - 60
        os.utime(self.lock, (old, old))
        os.chmod(self.lock, stat.S_IREAD)  # «только чтение»: unlink даёт PermissionError
        try:
            box, alive, sec = run_lock(self.st, 0.5, 4.0)
        finally:
            os.chmod(self.lock, stat.S_IWRITE | stat.S_IREAD)  # на 1.3.9 поток после этого снимет замок и выйдет сам
        self.assertFalse(alive, "FAIL-сценарий вечный цикл под замком — запись не ответила за 4 с")
        self.assertIn("ошибка", box, f"ожидался 409, итог {box}")
        e = box["ошибка"]
        self.assertEqual(e.status, 409)
        self.assertIn(str(self.lock), e.message, "в тексте путь к замку")
        self.assertIn("не удаётся удалить", e.message)
        self.assertLess(sec, 2.0, "ответ в пределах срока ожидания")

    def test_permanent_permission_error_without_lock_says_no_access(self):
        def fake(p, *a, **k):
            raise PermissionError(13, "denied")
        with mock.patch.object(S.os, "open", fake):
            box, alive, sec = run_lock(self.st, 0.3, 3.0)
        self.assertFalse(alive)
        e = box.get("ошибка")
        self.assertIsNotNone(e, f"ожидался 409, итог {box}")
        self.assertEqual(e.status, 409)
        self.assertIn("нет доступа", e.message.lower(), "причина — нет доступа, а не «меняет другая программа»")
        self.assertNotIn("меняет другая программа", e.message)
        self.assertIn(str(self.lock), e.message)

    def test_directory_in_place_of_lock_is_409_with_path(self):
        self.lock.mkdir()
        try:
            box, alive, sec = run_lock(self.st, 0.3, 3.0)
        finally:
            self.lock.rmdir()  # пустая папка своего теста во временной папке
        self.assertFalse(alive)
        e = box.get("ошибка")
        self.assertIsNotNone(e, f"ожидался 409, итог {box}")
        self.assertEqual(e.status, 409)
        self.assertIn("папка", e.message.lower())
        self.assertIn(str(self.lock), e.message)

    def test_busy_fresh_lock_keeps_busy_text_and_path(self):
        self.lock.write_bytes(b"busy")
        box, alive, sec = run_lock(self.st, 0.3, 3.0)
        self.assertFalse(alive)
        e = box.get("ошибка")
        self.assertIsNotNone(e, f"ожидался 409, итог {box}")
        self.assertIn("меняет другая программа", e.message)
        self.assertIn(str(self.lock), e.message)
        self.assertTrue(self.lock.exists(), "чужой свежий замок не удалён")

    def test_stale_removable_lock_is_taken(self):
        self.lock.write_bytes(b"old")
        old = time.time() - 60
        os.utime(self.lock, (old, old))
        box, alive, sec = run_lock(self.st, 0.5, 3.0)
        self.assertTrue(box.get("взят"), f"старый обычный замок снимается, итог {box}")
        self.assertFalse(self.lock.exists(), "свой замок снят после записи")


class LockReview1310(unittest.TestCase):
    """дата замка из будущего; «нет доступа» — только если файла замка не было ни разу."""
    def setUp(self):
        self.tmp = tmpdir("t1310r-")
        self.st = store(self.tmp)
        self.lock = self.tmp / "data.json.lock"

    def tearDown(self):
        to_trash(self.tmp)

    def test_lock_from_future_is_stale(self):
        self.lock.write_bytes(b"future")
        fut = time.time() + 3600
        os.utime(self.lock, (fut, fut))
        box, alive, sec = run_lock(self.st, 0.5, 3.0)
        self.assertTrue(box.get("взят"), f"замок с датой из будущего — старый, итог {box}")

    def test_future_threshold_minus_60_s(self):
        """порог «из будущего» −60 с с двух сторон — +5 с (часы чуть впереди) — чужой свежий замок, +120 с — старый."""
        self.lock.write_bytes(b"skew")
        fut = time.time() + 5
        os.utime(self.lock, (fut, fut))
        box, alive, sec = run_lock(self.st, 0.3, 3.0)
        e = box.get("ошибка")
        self.assertIsNotNone(e, f"+5 с: замок свежий — ожидался 409, итог {box}")
        self.assertIn("меняет другая программа", e.message)
        self.assertTrue(self.lock.exists(), "+5 с: чужой свежий замок не удалён")
        fut = time.time() + 120
        os.utime(self.lock, (fut, fut))
        box, alive, sec = run_lock(self.st, 0.5, 3.0)
        self.assertTrue(box.get("взят"), f"+120 с: замок старый — снимается, итог {box}")
        self.assertFalse(self.lock.exists())

    def test_permission_after_existing_file_is_busy_not_no_access(self):
        calls = []

        def fake(p, *a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise FileExistsError(17, "exists")
            raise PermissionError(13, "delete pending")
        with mock.patch.object(S.os, "open", fake):
            box, alive, sec = run_lock(self.st, 0.3, 3.0)
        e = box.get("ошибка")
        self.assertIsNotNone(e, f"ожидался 409, итог {box}")
        self.assertIn("меняет другая программа", e.message, "файл замка был — это «занято», а не «нет доступа»")


class App1310(unittest.TestCase):
    def test_app_settings_survives_recursion(self):
        import app as A
        tmp = tmpdir("t1310a-")
        try:
            (tmp / "settings.json").write_text("[" * 100000, "utf-8")
            with mock.patch.object(A, "DATA", tmp):
                self.assertEqual(A.settings(), {}, "100 000 скобок — настройки по умолчанию, без RecursionError")
        finally:
            to_trash(tmp)


class Settings1310(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t1310s-")
        self.p = self.tmp / "settings.json"

    def tearDown(self):
        to_trash(self.tmp)

    def test_public_settings_survives_recursion(self):
        self.p.write_text("[" * 100000, "utf-8")
        r = S.public_settings(self.p)
        self.assertEqual(r["theme"], "light")
        self.assertEqual(r["wallpaper"], {"left": 110, "bottom": 48})

    def test_cities_survive_recursion_and_huge_latitude(self):
        w = S.Weather(self.p, self.tmp / "wx.json", url="http://127.0.0.1:9/none")
        self.p.write_text("{\"weather\":" + "[" * 100000, "utf-8")
        self.assertEqual(w.cities(), [dict(c) for c in S.DEFAULT_CITIES], "вложенность 100 000 — города по умолчанию, без RecursionError")
        self.p.write_text(json.dumps({"weather": {"cities": [{"name": "Плохой", "lat": int("9" * 401), "lon": 1},
                                                           {"name": "Найроби", "lat": -1.29, "lon": 36.82}]}}), "utf-8")
        names = [c["name"] for c in w.cities()]
        self.assertEqual(names, ["Найроби"], "город с широтой из 401 цифры пропущен, остальные целы (без OverflowError)")


class Pomo1310(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t1310p-")
        self.path = self.tmp / "помидоро.json"

    def tearDown(self):
        to_trash(self.tmp)

    def test_json_not_object_gives_bad_copy(self):
        for raw in ("[1,2,3]", "\"строка\"", "null", "42"):
            bad = self.path.with_name(self.path.name + ".bad")
            if bad.exists():
                bad.write_bytes(b"")
            self.path.write_text(raw, "utf-8")
            P = S.Pomodoro(self.path, data_fn=lambda: ({"tasks": [], "projects": []}, b""))
            self.assertTrue(bad.exists() and bad.read_text("utf-8") == raw, f"{raw}: нет копии .bad — первая запись затёрла бы файл")
            self.assertEqual(P.snapshot()["phase"], "work", "таймер с начала")

    def test_object_has_no_bad_copy(self):
        self.path.write_text(json.dumps({"v": 1, "phase": "short"}), "utf-8")
        S.Pomodoro(self.path, data_fn=lambda: ({"tasks": [], "projects": []}, b""))
        self.assertFalse(self.path.with_name(self.path.name + ".bad").exists(), "обычный файл — без .bad")


if __name__ == "__main__":
    unittest.main()
