"""проверка значков рабочего стола (app.UI._icons_check, ) и код значков wallpaper.py — поведение на подделках
Проводника; COM-баланс position_items — на настоящем Проводнике только чтением (список пустой: ничего не переставляется).
Запуск: python -m unittest tests.test_134
"""
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

DEV = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(DEV))  # server.py
sys.path.insert(0, str(SRC))  # app.py и wallpaper.py проверяемой версии
import wallpaper as WPM  # noqa: E402
import app as A  # noqa: E402

RECT = (120, 0, 2440, 1392)
ICONS = [{"i": 0, "name": "Корзина", "pos": [2493, 1276], "rect": [2475, 1279, 2550, 1340]}]


def plan(ok=True, moved=True):
    return {"moved": [{"name": "Корзина", "from": [2493, 1276], "to": [18, 275], "ok": ok}] if moved else [], "left_over": [], "auto_arrange": False}


class FakeIcon:
    def __init__(self):
        self.msgs = []

    def notify(self, text, title=None):
        if len(text) > 256:  # как в pystray: поле szInfo — 256 символов
            raise ValueError(f"string too long ({len(text)}, maximum length 256)")
        self.msgs.append(text)


class IconsCheck134(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t134-", dir=os.environ.get("TEMP")))
        self.ui = A.UI(None)
        self.ui.wp, self.ui.wp_left, self.ui.icon = object(), 120, FakeIcon()
        self.calls = []
        self.p_data = mock.patch.object(A, "DATA", self.tmp)
        self.p_data.start()

    def tearDown(self):
        self.p_data.stop()

    def run_check(self, sleep_side=None, ok=True):
        def fake_plan(win, strip, apply=True):
            self.calls.append(("plan", apply))
            return plan(ok=ok)
        with mock.patch.object(A.time, "sleep", side_effect=sleep_side or (lambda s: None)), \
                mock.patch.object(A.WPM, "icons_to_strip", side_effect=fake_plan), mock.patch.object(A.WPM, "desktop_icons", return_value=ICONS):
            self.ui._icons_check(RECT)

    def test_wallpaper_turned_off_during_pause_no_moves(self):
        def off(_):
            self.ui.wp = None  # пользователь выключил обои за 5 с паузы
        self.run_check(off)
        self.assertEqual(self.calls, [], "после выключения обоев значки всё равно переставлялись")

    def test_wallpaper_recreated_during_pause_old_check_stops(self):
        self.ui.wp_gen = 1

        def recreate(_):
            self.ui.wp_gen = 2  # сторож пересоздал окно обоев: у нового включения своя проверка
        self.run_check(recreate)
        self.assertEqual(self.calls, [], "проверка старого включения работала с новым окном")

    def test_quitting_during_pause_no_moves(self):
        def quit_(_):
            self.ui.quitting = True
        self.run_check(quit_)
        self.assertEqual(self.calls, [])

    def test_failed_move_notifies_tray(self):
        self.run_check(ok=False)
        self.assertIn(("plan", True), self.calls)
        self.assertTrue(any("Не удалось переставить" in m and "Корзина" in m for m in self.ui.icon.msgs), self.ui.icon.msgs)

    def test_successful_move_no_failure_message(self):
        self.run_check(ok=True)
        self.assertFalse(any("Не удалось" in m for m in self.ui.icon.msgs), self.ui.icon.msgs)

    def test_long_icon_names_still_notify(self):
        long = {"moved": [{"name": "Договор MoyDen — финальная версия от заказчика %d.pdf" % i, "from": [1, 1], "to": [2, 2], "ok": False} for i in range(5)],
                "left_over": [], "auto_arrange": False}
        with mock.patch.object(A.time, "sleep"), mock.patch.object(A.WPM, "icons_to_strip", return_value=long),                 mock.patch.object(A.WPM, "desktop_icons", return_value=ICONS):
            self.ui._icons_check(RECT)
        self.assertTrue(self.ui.icon.msgs and self.ui.icon.msgs[0].startswith("Не удалось переставить"), self.ui.icon.msgs)
        self.assertLessEqual(len(self.ui.icon.msgs[0]), 256)

    def test_reserve_not_overwritten_in_same_second(self):
        layouts = iter([ICONS, ICONS + [{"i": 1, "name": "Excel", "pos": [2400, 600], "rect": [2380, 600, 2450, 660]}]])
        with mock.patch.object(A.time, "strftime", return_value="20261023-220000"):
            for _ in range(2):
                with mock.patch.object(A.time, "sleep"), mock.patch.object(A.WPM, "icons_to_strip", return_value=plan(ok=False)),                         mock.patch.object(A.WPM, "desktop_icons", return_value=next(layouts)):
                    self.ui._icons_check(RECT)
        files = sorted((self.tmp / "резерв").glob("значки-рабочего-стола-*.json"))
        self.assertEqual(len(files), 2, [f.name for f in files])
        first = self.tmp / "резерв" / "значки-рабочего-стола-20261023-220000.json"
        self.assertEqual(len(json.loads(first.read_text("utf-8"))), 1)  # первый резерв (исходные позиции) цел, второй — с суффиксом

    def test_late_closed_of_old_window_keeps_new(self):
        old, new = object(), object()
        self.ui.wp = new  # сторож пересоздал окно; «закрыто» старого пришло позже
        self.ui._wp_closed(old)
        self.assertIs(self.ui.wp, new)
        self.ui._wp_closed(new)
        self.assertIsNone(self.ui.wp)

    def test_same_layout_reserve_written_once(self):
        n = iter(range(100))
        with mock.patch.object(A.time, "strftime", side_effect=lambda f: f"2026102{next(n)}"):  # у каждой проверки своё время в имени файла
            self.run_check(ok=False)
            self.run_check(ok=False)
        files = sorted((self.tmp / "резерв").glob("значки-рабочего-стола-*.json"))
        self.assertEqual(len(files), 1, [f.name for f in files])
        self.assertEqual(json.loads(files[0].read_text("utf-8"))[0]["name"], "Корзина")

    def test_two_checks_never_move_at_the_same_time(self):
        state = {"in": 0, "max": 0}
        lock = threading.Lock()

        def slow_plan(win, strip, apply=True):
            with lock:
                state["in"] += 1
                state["max"] = max(state["max"], state["in"])
            time.sleep(0.15)  # настоящая пауза (A.time.sleep подменён только для 5 с ожидания)
            with lock:
                state["in"] -= 1
            return plan()
        real_sleep = time.sleep
        with mock.patch.object(A.time, "sleep", side_effect=lambda s: None if s == 5 else real_sleep(s)), \
                mock.patch.object(A.WPM, "icons_to_strip", side_effect=slow_plan), mock.patch.object(A.WPM, "desktop_icons", return_value=ICONS):
            th = [threading.Thread(target=self.ui._icons_check, args=(RECT,)) for _ in range(2)]
            [t.start() for t in th]
            [t.join(10) for t in th]
        self.assertEqual(state["max"], 1, "две проверки значков переносили одновременно")


class FakeK32:
    def __init__(self, alloc=0x10000):
        self.alloc, self.log = alloc, []

    def OpenProcess(self, *a):
        self.log.append("open"); return 0x55

    def VirtualAllocEx(self, *a):
        self.log.append("alloc"); return self.alloc

    def VirtualFreeEx(self, *a):
        self.log.append("free"); return 1

    def ReadProcessMemory(self, *a):
        self.log.append("read"); return 1

    def WriteProcessMemory(self, *a):
        self.log.append("write"); return 1

    def CloseHandle(self, h):
        self.log.append("close"); return 1


class FakeU:
    """user32 для списка значков: SendMessageW и SendMessageTimeoutW; hung=True — Проводник не отвечает."""
    def __init__(self, hung=False, list_pid=4242, shell_pid=4242, fail_write=False):
        self.hung, self.sent, self.list_pid, self.shell_pid = hung, [], list_pid, shell_pid

    def GetWindowThreadProcessId(self, hwnd, p):
        p._obj.value = self.shell_pid if hwnd == 0x99 else self.list_pid
        return 1

    def GetShellWindow(self):
        return 0x99

    def ClientToScreen(self, hwnd, p):
        return 1

    def SendMessageW(self, hwnd, msg, w, l):
        self.sent.append(msg)
        if self.hung:
            raise AssertionError("SendMessageW без таймаута к зависшему Проводнику — поток повис бы навсегда")
        return 1 if msg == WPM.LVM_GETITEMCOUNT else 0

    def SendMessageTimeoutW(self, hwnd, msg, w, l, flags, ms, res):
        self.sent.append(msg)
        if self.hung:
            return 0
        res._obj.value = 1 if msg == WPM.LVM_GETITEMCOUNT else 0
        return 1


class WallpaperIcons134(unittest.TestCase):
    def test_virtualallocex_failure_returns_empty_and_closes_handle(self):
        k, uu = FakeK32(alloc=None), FakeU()
        with mock.patch.object(WPM, "k32", k), mock.patch.object(WPM, "u", uu):
            out = WPM.desktop_icons(lv=0x77)
        self.assertEqual(out, [])
        self.assertEqual(uu.sent, [], "сообщения Проводнику ушли с пустым буфером")
        self.assertNotIn("write", k.log)
        self.assertIn("close", k.log)

    def test_hung_explorer_times_out_buffer_kept_handle_closed(self):
        """после таймаута Проводник может дописать в буфер — буфер не освобождается, дескриптор закрывается."""
        k, uu = FakeK32(), FakeU(hung=True)
        with mock.patch.object(WPM, "k32", k), mock.patch.object(WPM, "u", uu):
            with self.assertRaises(TimeoutError):
                WPM.desktop_icons(lv=0x77)
        self.assertNotIn("free", k.log)
        self.assertIn("close", k.log)

    def test_list_not_owned_by_shell_is_not_opened(self):
        """L1: окно SysListView32 подставлено другим процессом — память этого процесса не открывается."""
        k, uu = FakeK32(), FakeU(list_pid=777, shell_pid=4242)
        with mock.patch.object(WPM, "k32", k), mock.patch.object(WPM, "u", uu):
            self.assertEqual(WPM.desktop_icons(lv=0x77), [])
        self.assertNotIn("open", k.log)

    def test_failed_lvitem_write_never_sends_gettext(self):
        """L2: не записали свой LVITEMW — LVM_GETITEMTEXTW не шлём (Проводник взял бы адрес текста из мусора)."""
        k, uu = FakeK32(), FakeU()
        k.WriteProcessMemory = lambda *a: (k.log.append("write-fail"), 0)[1]
        with mock.patch.object(WPM, "k32", k), mock.patch.object(WPM, "u", uu):
            self.assertEqual(WPM.desktop_icons(lv=0x77), [])
        self.assertNotIn(WPM.LVM_GETITEMTEXTW, uu.sent)
        self.assertNotIn(WPM.LVM_GETITEMRECT, uu.sent)

    def test_normal_explorer_reads_icons(self):
        k, uu = FakeK32(), FakeU()
        with mock.patch.object(WPM, "k32", k), mock.patch.object(WPM, "u", uu):
            out = WPM.desktop_icons(lv=0x77)
        self.assertEqual(len(out), 1)
        self.assertEqual(k.log[-2:], ["free", "close"])

    def test_position_items_leaves_thread_com_balanced(self):
        """После position_items поток не остаётся в STA: CoInitializeEx(MTA) в том же потоке даёт S_OK, а не RPC_E_CHANGED_MODE.
        Настоящий Проводник, только чтение: список пустой — ни один значок не переставляется."""
        out = {}

        def body():
            out["placed"] = WPM.position_items([])
            hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
            out["hr"] = hr & 0xFFFFFFFF
            if hr >= 0:
                ctypes.windll.ole32.CoUninitialize()
        t = threading.Thread(target=body)
        t.start(); t.join(30)
        self.assertEqual(out.get("placed"), set())
        self.assertEqual(out.get("hr"), 0, f"поток остался в STA после position_items: 0x{out.get('hr', 0):08X} (нет CoUninitialize)")


if __name__ == "__main__":
    unittest.main()
