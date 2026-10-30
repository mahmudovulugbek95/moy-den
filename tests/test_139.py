"""мутанты (M1 сброс в длинном перерыве, M4 укорочение на паузе, M5 день по концу отрезка), устойчивость
помидоро.json (.bad, BOM, огромные числа, fsync), ветка «доступ запрещён» и флаг SMTO_BLOCK, activate не присоединяется к занятой цели,
wp_prev не теряется при неудачном возврате, show_field не меняет экран при набранной форме, recycle только для файлов, «наш» ярлык — по exe.
Запуск: python -m unittest tests.test_139"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import winreg
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(HERE))
import server as S  # noqa: E402
import wallpaper as WPM  # noqa: E402
import app as A  # noqa: E402
import установщик as I  # noqa: E402
from test_138 import to_trash, tmpdir, make_lnk  # noqa: E402

T0 = datetime(2026, 10, 24, 10, 0, tzinfo=S.TZ).timestamp()


class Pomo139(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("t139p-")
        self.path = self.tmp / "помидоро.json"
        self.doc = json.loads((HERE / "fixture-data.json").read_text("utf-8-sig"))
        self.now = [T0]
        self.P = self.new()

    def tearDown(self):
        to_trash(self.tmp)

    def new(self):
        return S.Pomodoro(self.path, data_fn=lambda: (self.doc, b""), clock=lambda: self.now[0])

    def go(self, s):
        self.now[0] += s

    def test_m1_reset_in_long_break_resets_cycle(self):
        for _ in range(4):
            self.P.command({"action": "start"})
            self.go(1500)
            if self.P.snapshot()["phase"] != "long":
                self.P.command({"action": "start"})  # перерыв — кнопкой
                self.go(300)
        self.assertEqual(self.P.snapshot()["phase"], "long")
        st = self.P.command({"action": "reset"})
        self.assertEqual((st["phase"], st["cycle"]), ("work", 0), "сброс в длинном перерыве — новый круг")

    def test_m4_shorten_on_pause_ends_on_start(self):
        self.P.command({"action": "start"})
        self.go(22 * 60)
        self.P.command({"action": "pause"})
        st = self.P.command({"action": "settings", "durations": {"work": 20}})
        self.assertEqual((st["phase"], st["running"], st["remaining"]), ("work", False, 0), "на паузе укороченный отрезок исчерпан")
        self.P.command({"action": "start"})
        st = self.P.snapshot()
        self.assertEqual((st["phase"], st["today"]), ("short", 1))

    def test_m5_day_by_segment_end_not_by_now(self):
        self.now[0] = datetime(2026, 10, 24, 23, 30, tzinfo=S.TZ).timestamp()
        self.P.command({"action": "start"})
        self.go(1500 + 2100)  # помидор кончился в 23:55 24.10, опрос — в 00:30 25.10
        self.P.snapshot()
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["days"], {"2026-10-24": 1})

    def test_hostile_numbers_bom_and_bad_copy(self):
        for raw in ('{"running": true, "startedAt": 1' + "0" * 500 + "}", '{"seq": 1' + "0" * 4400 + "}"):
            self.path.write_text(raw, "utf-8")
            st = self.new().snapshot()  # сервер стартует, таймер с начала
            self.assertEqual((st["phase"], st["running"]), ("work", False))
            self.assertTrue(self.path.with_name(self.path.name + ".bad").exists(), "копия испорченного файла для разбора")
        self.path.write_text(json.dumps({"seq": 10 ** 15, "days": {"2026-10-24": 3}}), "utf-8")
        self.assertEqual(self.new().snapshot()["seq"], 0, "огромный seq — с нуля")
        self.path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"days": {"2026-10-24": 3}}).encode())
        self.assertEqual(self.new().snapshot()["today"], 3, "BOM не обнуляет счётчики")

    def test_save_fsync(self):
        with mock.patch.object(S.os, "fsync") as fs:
            self.P.command({"action": "start"})
        self.assertTrue(fs.called, "запись помидоро.json — с fsync")


class Wallpaper139(unittest.TestCase):
    def setUp(self):
        st = self.st = {"fg": 900, "focus": 901, "attached": set(), "log": [], "busy": set()}
        f = mock.MagicMock()
        f.GetForegroundWindow.side_effect = lambda: st["fg"]
        f.GetWindowThreadProcessId.side_effect = lambda h, p: {900: 9, 222: 2, 111: 1, 112: 1, 333: 3}.get(h, 7)
        f.GetAncestor.side_effect = lambda h, x: 222 if h in (111, 112, 222) else h
        f.GetWindow.side_effect = lambda h, c: 112 if h == 111 else None
        f.IsChild.side_effect = lambda a, b: a == 111 and b == 112
        f.IsWindow.return_value = f.IsWindowVisible.return_value = 1
        f.IsIconic.return_value = f.IsHungAppWindow.return_value = 0

        def smto(h, m, w, l, flags, ms, r):
            st["log"].append(("probe", h, flags))
            return 0 if h in st["busy"] else 1
        f.SendMessageTimeoutW.side_effect = smto

        def gti(tid, p):
            p._obj.hwndFocus = st["focus"]
            return 1
        f.GetGUIThreadInfo.side_effect = gti
        f.AttachThreadInput.side_effect = lambda a, b, on: st["log"].append(("attach" if on else "detach", b)) or 1

        def sfw(h):
            st["fg"] = h
            return 1
        f.SetForegroundWindow.side_effect = sfw

        def sf(h):
            if st["fg"] == 222:
                st["focus"] = h
            return 0
        f.SetFocus.side_effect = sf
        self.ps = [mock.patch.object(WPM, "u", f), mock.patch.object(WPM.time, "sleep"), mock.patch.object(WPM, "_cloaked", return_value=False)]
        for p in self.ps:
            p.start()

    def tearDown(self):
        for p in self.ps:
            p.stop()

    def test_denied_is_not_busy(self):
        self.st["busy"].add(900)  # окно с правами администратора: WM_NULL отклонён (ERROR_ACCESS_DENIED), а не «занята»
        with mock.patch.object(WPM.ctypes, "get_last_error", return_value=5):
            ok, prev, sw = WPM.take_focus(111, 222)
        self.assertEqual((ok, prev), (True, 900), "«доступ запрещён» — не запасной путь, обои получают клавиатуру")
        self.assertNotIn(("attach", 9), self.st["log"], "к вводу окна с повышенными правами не присоединяемся")

    def test_probe_flags_block_and_abortifhung(self):
        WPM.take_focus(111, 222)
        flags = [x[2] for x in self.st["log"] if x[0] == "probe"]
        self.assertTrue(flags and all(fl & 0x1 and fl & 0x2 for fl in flags), f"SMTO_BLOCK|SMTO_ABORTIFHUNG: {flags}")

    def test_activate_busy_target_not_attached(self):
        self.st["fg"] = 222
        self.st["busy"].add(333)
        WPM.activate(333, only_from=222)
        self.assertNotIn(("attach", 3), self.st["log"], "к занятой программе-цели ввод не присоединяем")


class Ui139(unittest.TestCase):
    def setUp(self):
        self.ui = A.UI(None)
        self.ui.wp, self.ui.wp_hwnd, self.ui.wp_host, self.ui.win = object(), 111, 222, object()

    def test_release_failure_keeps_previous(self):
        self.ui.wp_prev = 333
        with mock.patch.object(A.WPM, "activate", return_value=False):
            self.assertFalse(self.ui.wallpaper_release())
        self.assertEqual(self.ui.wp_prev, 333, "возврат не удался — окно для возврата не теряется")

    def test_show_field_keeps_screen_when_form_typed(self):
        js = []
        self.ui.win = type("W", (), {"evaluate_js": lambda s, x: js.append(x)})()
        with mock.patch.object(self.ui, "show"), mock.patch.object(A.WPM, "activate"), mock.patch.object(A.threading, "Thread") as th:
            self.ui.show_field("e-role", "#/people/4")
            th.call_args.kwargs["target"]()
        self.assertIn("atTitle", js[0], "перед сменой экрана проверяется набранная форма задачи")
        self.assertNotIn("if(location.hash!==h)location.hash=h;", js[0], "экран меняется не безусловно")


class Installer139(unittest.TestCase):
    def test_recycle_only_files(self):
        d = tmpdir("t139r-")
        try:
            self.assertFalse(I.recycle(d), "папку recycle не трогает")
            self.assertTrue(d.exists())
        finally:
            to_trash(d)

    def test_desktop_link_ours_only_by_exe(self):
        tmp = tmpdir("t139i-")
        t, dd = tmp / "Программа", tmp / "Данные"
        dirs = {k: tmp / k for k in ("Пуск", "РС", "Авто")}
        for p in [t, dd, *dirs.values()]:
            p.mkdir()
        shutil.copyfile(HERE / "fixture-data.json", dd / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", dd / "settings.json")
        pay = tmp / "payload"
        pay.mkdir()
        (pay / "MoyDen.exe").write_bytes(b"MZ 139")
        shutil.copyfile(DEV / "мой_день.ico", pay / "мой_день.ico")
        name, key = "Мой день (т139)", "MoyDen.T139"
        desk = dirs["РС"] / f"{name}.lnk"
        make_lnk(desk, t / f"Удалить {name}.exe")  # ярлык в папку программы, но не на программу
        rec = []
        a = Namespace(target=str(t), data=str(dd), name=name, regkey=key, port=8697, instance="т139", manifest=None, desktop_dir=str(dirs["РС"]),
                      startup_dir=str(dirs["Авто"]), start_dir=str(dirs["Пуск"]), replace_old=False, wallpaper_on=False, launch=False, uninstall=False, quiet=True)
        try:
            with mock.patch.object(I, "PAYLOAD", pay), mock.patch.object(I, "recycle", side_effect=lambda p: rec.append(p) or True), \
                mock.patch.object(I, "other_build", return_value=None):  # компьютер проверки — как без другой сборки «Мой день»
                I.install(a)
            self.assertEqual(rec, [], "«наш» ярлык — только на exe программы")
            self.assertTrue(desk.exists())
        finally:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{I.UNINSTALL_ROOT}\{key}")
            except OSError:
                pass
            to_trash(tmp)


if __name__ == "__main__":
    unittest.main()


class LockPermission(unittest.TestCase):
    """1.3.9в в Windows только что снятый data.json.lock ещё «удаляется» — os.open(O_EXCL) даёт PermissionError."""
    def store(self):
        cls = next(v for v in vars(S).values() if isinstance(v, type) and "file_lock" in vars(v))
        st = cls.__new__(cls)
        st.path = Path(tempfile.mkdtemp(prefix="moyden-lock139-")) / "data.json"
        return st

    def test_permission_error_is_busy_then_lock(self):
        st, real, calls = self.store(), os.open, []

        def fake(p, *a, **k):
            if str(p).endswith(".lock") and len(calls) < 2:
                calls.append(1)
                raise PermissionError(13, "delete pending")
            return real(p, *a, **k)
        with mock.patch.object(S.os, "open", fake):
            with st.file_lock(wait=2.0):
                self.assertTrue(st.path.with_name("data.json.lock").exists(), "замок взят после двух PermissionError")
        self.assertEqual(len(calls), 2)
        self.assertFalse(st.path.with_name("data.json.lock").exists(), "замок снят")

    def test_permanent_permission_error_is_409_in_time(self):
        st = self.store()

        def fake(p, *a, **k):
            raise PermissionError(13, "denied")
        t0 = __import__("time").monotonic()
        with mock.patch.object(S.os, "open", fake):
            with self.assertRaises(S.ApiError):
                with st.file_lock(wait=0.3):
                    pass
        self.assertLess(__import__("time").monotonic() - t0, 2.0, "ожидание ограничено wait, без вечного цикла")
