"""Публичная версия «Мой день»: первый запуск с пустыми данными, места под календари, пояс компьютера, погода без зашитого города,
свои порт / метка сервера / ID уведомлений / ключ реестра, своё окно — по номеру процесса, установщик не ставится рядом с другой сборкой.
Запуск: python -m unittest tests.test_public -v
Против другой версии кода: set MOYDEN_SRC=<папка с server.py, app.py, установщик.py>
"""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from argparse import Namespace
from ctypes import wintypes as W
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("MOYDEN_SRC") or HERE.parent)
sys.path.insert(0, str(SRC))


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SRC / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


with mock.patch.dict(os.environ):  # модуль сервера здесь — с поясом компьютера (без MOYDEN_TZ проверок)
    os.environ.pop("MOYDEN_TZ", None)
    S = load("server_pub", "server.py")
I = load("installer_pub", "установщик.py")


class FirstRun(unittest.TestCase):
    def test_missing_data_becomes_empty_doc(self):
        d = Path(tempfile.mkdtemp()) / "новая папка" / "data.json"
        self.assertTrue(S.ensure_data(d), "data.json создан при первом запуске")
        doc, _ = S.Store(d).load()
        self.assertEqual((doc["projects"], doc["tasks"], doc["employees"], doc["reminders"], doc["eventLinks"]), ([], [], [], [], {}),
                         "новый пользователь получает пустую программу")
        self.assertEqual((doc["schema"], doc["nextId"]), (S.SCHEMA, 1))

    def test_existing_data_untouched(self):  # FAIL-сценарий: первый запуск не должен затирать чужие данные
        d = Path(tempfile.mkdtemp()) / "data.json"
        d.write_text('{"projects": [], "tasks": [{"id": 1, "title": "моя"}], "employees": []}', "utf-8")
        before = d.read_bytes()
        self.assertFalse(S.ensure_data(d))
        self.assertEqual(d.read_bytes(), before)

    def test_server_main_creates_data(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(S, "Server", side_effect=OSError("порт занят")):  # дальше создания данных сервер не идёт
            rc = S.main(["--data", str(tmp / "data.json"), "--log", str(tmp / "server.log"), "--settings", str(tmp / "settings.json"),
                         "--calendars", str(tmp / "calendars.local.json"), "--cache", str(tmp / "кэш"), "--weather-cache", str(tmp / "wx.json")])
        self.assertEqual(rc, 3)
        self.assertTrue((tmp / "data.json").is_file())


class CalendarSlots(unittest.TestCase):
    def test_slots_without_config_and_connect_writes_file(self):
        tmp = Path(tempfile.mkdtemp())
        cal = S.Calendars(tmp / "calendars.local.json", tmp / "кэш", 300)
        cals, err = cal.load_cfg()
        self.assertIsNone(err)
        self.assertEqual([(c["name"], c["url"]) for c in cals], [("Рабочий", ""), ("Личный", "")], "места есть, адресов нет")
        r = cal.connect({"name": "Личный", "url": ""})  # отключить пустой — файл создаётся со слотами
        self.assertEqual(r["connected"], False)
        raw = json.loads((tmp / "calendars.local.json").read_text("utf-8"))
        self.assertEqual([(c["name"], c["ical_url"]) for c in raw["calendars"]], [("Рабочий", ""), ("Личный", "")])

    def test_test_flag_ignored_in_built_program(self):  # MOYDEN_TEST_ICAL не открывает localhost у собранной программы
        with mock.patch.dict(os.environ, {"MOYDEN_TEST_ICAL": "1"}):
            self.assertTrue(S.normalize_url("http://127.0.0.1:9/x.ics"), "из исходников (проверки) — можно")
            with mock.patch.object(S.sys, "frozen", True, create=True):
                with self.assertRaises(S.CalError):
                    S.normalize_url("http://127.0.0.1:9/x.ics")

    def test_unknown_calendar_404(self):
        cal = S.Calendars(Path(tempfile.mkdtemp()) / "calendars.local.json", Path(tempfile.mkdtemp()), 300)
        with self.assertRaises(S.ApiError) as e:
            cal.connect({"name": "Чужой", "url": ""})
        self.assertEqual(e.exception.status, 404)


class Zone(unittest.TestCase):
    def test_local_zone_matches_computer(self):
        now = time.time()
        lt = time.localtime(now)
        self.assertEqual(datetime.fromtimestamp(now, S.TZ).utcoffset(), timedelta(seconds=lt.tm_gmtoff), "пояс программы = пояс компьютера")
        self.assertEqual(S.today(), time.strftime("%Y-%m-%d", lt))

    def test_moyden_tz_override(self):
        with mock.patch.dict(os.environ, {"MOYDEN_TZ": "Asia/Kolkata"}):
            tz, name = S._zone()
        self.assertEqual(name, "Asia/Kolkata")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(tz).utcoffset(), timedelta(hours=5, minutes=30))

    def test_bad_moyden_tz_falls_back(self):
        with mock.patch.dict(os.environ, {"MOYDEN_TZ": "Нет/Такого"}):
            tz, name = S._zone()
        self.assertEqual(name, "")
        self.assertEqual(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).astimezone(tz).utcoffset(),
                         datetime(2026, 1, 1, 12, tzinfo=timezone.utc).astimezone().utcoffset())


class Weather(unittest.TestCase):
    def test_no_city_by_default_no_requests(self):
        tmp = Path(tempfile.mkdtemp())
        w = S.Weather(tmp / "settings.json", tmp / "wx.json", url="http://127.0.0.1:9/never", refresh=1800, retry=120, max_age=3600)
        self.assertEqual(w.cities(), [], "без settings.json город не задан — в интернет за погодой не ходим")
        with mock.patch.object(S.urllib.request, "urlopen", side_effect=AssertionError("запрос погоды без города")):
            w.refresh_all()


class Identity(unittest.TestCase):
    def test_port_label_and_ids_differ_from_other_build(self):
        self.assertEqual((S.DEFAULT_PORT, I.DEFAULT_PORT, S.APP), (8648, 8648, "moy-den-app"))
        app_src = (SRC / "app.py").read_text("utf-8")
        self.assertIn('TOAST_ID = "MoyDen.App"', app_src)
        self.assertIn('"Local\\\\MoyDen.App" + INSTANCE', app_src)

    def test_app_port_default(self):  # мутант «порт другой сборки» в app.py ловится здесь
        A = load("app_pub", "app.py")
        if A.CFG.get("port"):
            self.skipTest("рядом с app.py лежит настройки-установки.json с портом")
        self.assertEqual((A.PORT, A.URL), (8648, "http://127.0.0.1:8648/"))


class OwnWindow(unittest.TestCase):
    """Своё окно ищется по номеру процесса: окно другой программы с тем же заголовком программа не трогает.
    Окна создаются СКРЫТЫМИ (без WS_VISIBLE) — на экране ничего не появляется."""
    CHILD = (
        "import ctypes,sys\nfrom ctypes import wintypes as W\nu=ctypes.WinDLL('user32',use_last_error=True)\n"
        "u.CreateWindowExW.restype=W.HWND\nu.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,ctypes.c_int,ctypes.c_int,"
        "ctypes.c_int,ctypes.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID]\n"
        "h=u.CreateWindowExW(0,'STATIC',sys.argv[1],0,0,0,10,10,None,None,None,None)\nprint('ready' if h else 'fail',flush=True)\nsys.stdin.read()\n")

    def setUp(self):
        self.A = load("app_pub_win", "app.py")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.CreateWindowExW.restype = W.HWND
        self.u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
        self.u.DestroyWindow.argtypes = [W.HWND]
        self.u.FindWindowW.restype = W.HWND
        self.title = f"Мой день (проверка окна {uuid.uuid4().hex[:8]})"
        self.child = subprocess.Popen([sys.executable, "-c", self.CHILD, self.title], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.child.wait, 10)
        self.addCleanup(self.child.stdin.close)
        self.assertEqual(self.child.stdout.readline().strip(), "ready", "окно другого процесса не создано")

    def test_finds_only_own_window(self):
        same = lambda h: h is not None and (int(h) & 0xFFFFFFFF) == (int(self.A.find_own(self.title)) & 0xFFFFFFFF)
        self.assertEqual(self.A.find_own(self.title), 0, "FAIL-сценарий: своего окна нет — чужое с тем же заголовком брать нельзя")
        self.assertTrue(self.u.FindWindowW(None, self.title), "окно другого процесса существует (FindWindow его видит)")
        own = self.u.CreateWindowExW(0, "STATIC", self.title, 0, 0, 0, 10, 10, None, None, None, None)
        self.assertTrue(own)
        try:
            self.assertTrue(same(own), "find_own вернул не своё окно")
        finally:
            self.u.DestroyWindow(own)
        self.assertEqual(self.A.find_own(self.title), 0, "после закрытия своего окна — снова 0, а не окно другого процесса")


class InstallerOtherBuild(unittest.TestCase):
    def args(self, tmp, **kw):
        a = dict(target=str(tmp / "Программа"), data=str(tmp / "Данные"), name="Мой день (тп)", regkey="MoyDenApp", port=8699, instance=None,
                 manifest=None, desktop_dir=str(tmp / "РС"), startup_dir=str(tmp / "Авто"), start_dir=str(tmp / "Пуск"),
                 replace_old=False, wallpaper_on=False, launch=False, uninstall=False, quiet=True, side_by_side_test=False)
        a.update(kw)
        return Namespace(**a)

    def refused(self, args, other=r"C:\Программа другой сборки"):
        with mock.patch.object(I, "other_build", return_value=other), \
                mock.patch.object(I, "folder", side_effect=lambda n: Path(r"C:\Настоящие") / n):
            with self.assertRaises(SystemExit) as e:
                I.install(args)
        self.assertFalse(Path(args.target).exists(), "ничего не скопировано")
        return e.exception.code

    def test_refuses_next_to_other_build(self):
        self.assertEqual(self.refused(self.args(Path(tempfile.mkdtemp()))), 8)

    def test_instance_alone_does_not_bypass(self):  # отдельный экземпляр без флага проверки — тоже отказ
        self.assertEqual(self.refused(self.args(Path(tempfile.mkdtemp()), instance="X")), 8)

    def test_regkey_of_other_build_refused(self):  # ключ MoyDen запрещён всегда — даже если другой сборки нет
        for key in ("MoyDen", "moyden", "MOYDEN", "MoyDEN"):  # реестр не различает регистр
            self.assertEqual(self.refused(self.args(Path(tempfile.mkdtemp()), regkey=key, instance="X", side_by_side_test=True), other=None), 7, key)

    def test_side_by_side_only_with_own_folders(self):
        tmp = Path(tempfile.mkdtemp())
        other = Path(tempfile.mkdtemp()) / "Другая сборка"  # папка другой сборки — вне папки проверки
        ok = dict(instance="X", side_by_side_test=True)
        with mock.patch.object(I, "folder", side_effect=lambda n: Path(r"C:\Настоящие") / n):
            self.assertTrue(I.side_by_side_ok(self.args(tmp, **ok), other))
            self.assertFalse(I.side_by_side_ok(self.args(tmp, instance="X"), other), "без флага")
            self.assertFalse(I.side_by_side_ok(self.args(tmp, side_by_side_test=True), other), "без отдельного экземпляра")
            self.assertFalse(I.side_by_side_ok(self.args(tmp, port=None, **ok), other), "без своего порта")
            self.assertFalse(I.side_by_side_ok(self.args(tmp, start_dir=None, **ok), other), "ярлыки «Пуска» — в настоящую папку")
            self.assertFalse(I.side_by_side_ok(self.args(tmp, startup_dir=str(Path(tempfile.mkdtemp()) / "Авто"), **ok), other), "папка вне проверки")
            self.assertFalse(I.side_by_side_ok(self.args(tmp, **ok), "(папка не указана)"), "папка другой сборки неизвестна")
            q = {k: "\\\\?\\" + str(tmp / v) for k, v in (("target", "Программа"), ("data", "Данные"), ("start_dir", "Пуск"),
                                                         ("startup_dir", "Авто"), ("desktop_dir", "РС"))}
            self.assertFalse(I.side_by_side_ok(self.args(tmp, **q, **ok), other), "пути с \\\\?\\ обходили бы сравнение — отказ")
            self.assertFalse(I.side_by_side_ok(self.args(other, **ok), other / "Программа"), "папка программы = папка другой сборки")
        with mock.patch.object(I, "folder", side_effect=lambda n: tmp / "Пуск" if n == "Programs" else Path(r"C:\Настоящие") / n):
            self.assertFalse(I.side_by_side_ok(self.args(tmp, **ok), other), "в папке проверки лежит настоящая папка «Пуска»")
        with mock.patch.object(I, "folder", side_effect=lambda n: tmp.parent if n == "StartMenu" else Path(r"C:\Настоящие") / n):
            self.assertFalse(I.side_by_side_ok(self.args(tmp, **ok), other), "папка проверки внутри настоящего «Главного меню»")
        with mock.patch.object(I, "folder", side_effect=OSError("нет ответа")):
            self.assertFalse(I.side_by_side_ok(self.args(tmp, **ok), other), "папки Windows не определились — отказ")
        self.assertEqual(self.refused(self.args(tmp, start_dir=None, **ok)), 8)

    def test_side_by_side_refuses_other_build_data(self):  # данные проверки = данные другой сборки — отказ
        tmp = Path(tempfile.mkdtemp())
        other = Path(tempfile.mkdtemp()) / "Другая сборка"
        other.mkdir()
        (other / "настройки-установки.json").write_text(json.dumps({"dataDir": str(tmp / "Данные")}), "utf-8")
        with mock.patch.object(I, "folder", side_effect=lambda n: Path(r"C:\Настоящие") / n):
            self.assertFalse(I.side_by_side_ok(self.args(tmp, instance="X", side_by_side_test=True), other))

    def test_folder_empty_answer_is_error(self):  # пустой ответ PowerShell — не «текущая папка»
        with mock.patch.object(I, "ps", return_value=""):
            with self.assertRaises(OSError):
                I.folder("Programs")

    def test_uninstall_refuses_other_build_manifest(self):  # удалитель не трогает папку и ярлыки другой сборки
        for regkey, other_is_here in (("MoyDen", False), ("moyden", False), ("MoyDenApp", True)):
            d = Path(tempfile.mkdtemp())
            (d / "настройки-установки.json").write_text("{}", "utf-8")
            (d / "Мой день.exe").write_bytes(b"x")
            (d / "установка.json").write_text(json.dumps({"name": "Мой день", "regkey": regkey, "target": str(d), "links": []}), "utf-8")
            with mock.patch.object(I, "other_build", return_value=str(d) if other_is_here else None), \
                    mock.patch.object(I, "stop_old") as stop, mock.patch.object(I.subprocess, "Popen") as popen:
                with self.assertRaises(SystemExit) as e:
                    I.uninstall(Namespace(manifest=str(d / "установка.json"), quiet=True))
            self.assertEqual(e.exception.code, 8, regkey)
            stop.assert_not_called()
            popen.assert_not_called()
            self.assertTrue((d / "Мой день.exe").is_file() and (d / "установка.json").is_file(), "файлы другой сборки на месте")

    def test_other_build_key_without_location(self):  # ключ есть, InstallLocation нет — всё равно другая сборка
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(I.winreg, "OpenKey", return_value=Key()), \
                mock.patch.object(I.winreg, "QueryValueEx", side_effect=OSError("нет значения")):
            self.assertEqual(I.other_build(), "(папка не указана)")

    def test_other_build_reads_only_its_key(self):
        seen = []

        def fake_open(root, path):
            seen.append(path)
            raise OSError("нет ключа")
        with mock.patch.object(I.winreg, "OpenKey", side_effect=fake_open):
            self.assertIsNone(I.other_build())
        self.assertEqual(seen, [I.UNINSTALL_ROOT + "\\MoyDen"])

    def test_parser_defaults(self):  # мутант «--regkey по умолчанию = MoyDen» ловится здесь
        a = I.parser().parse_args([])
        self.assertEqual((a.regkey, a.side_by_side_test, a.instance), ("MoyDenApp", False, None))

    def test_system_programs_by_full_path(self):  # подложенный powershell.exe в «Загрузках» не выполнится
        src = (SRC / "установщик.py").read_text("utf-8")
        self.assertNotRegex(src, r'subprocess\.(run|Popen)\(\[?f?["\']"?(powershell|taskkill|cmd)\b')
        for exe in (I.POWERSHELL, I.TASKKILL, I.CMD):
            self.assertTrue(exe.is_absolute() and exe.is_file(), exe)

    def test_default_folders(self):
        src = (SRC / "установщик.py").read_text("utf-8")
        self.assertNotIn("DEFAULT_DATA", src)  # прежний зашитый путь папки данных
        self.assertIn('DEFAULT_DIR = "MoyDen"', src)


if __name__ == "__main__":
    unittest.main()
