"""1.3.13, ответ с ошибкой на запрос, тело которого сервер не прочитал (413, 403), — «мягкое» закрытие: клиент дошлёт тело
целиком и прочитает ответ (раньше Windows рвала соединение сбросом: 1–2 обрыва на 1000, ответ терялся). Плюс: дочитка ограничена по времени
и объёму; на ошибку с прочитанным телом (400) ответ без задержки.
Запуск: python -m unittest tests.test_1313"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE.parent
SRC = Path(os.environ.get("MOYDEN_SRC") or DEV)
sys.path.insert(0, str(SRC))
os.environ.setdefault("MOYDEN_NO_TOAST", "1")
import server as S  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Linger1313(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="moyden-1313-"))
        shutil.copyfile(HERE / "fixture-data.json", self.tmp / "data.json")
        shutil.copyfile(HERE / "fixture-settings.json", self.tmp / "settings.json")
        (self.tmp / "calendars.local.json").write_text('{"calendars": []}', "utf-8")
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

    def send_full(self, head, body):
        """Клиент шлёт заголовок и тело целиком (отдельным потоком), одновременно читает ответ до конца. Возвращает (ответ, ошибка отправки)."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=15)
        err = []

        def sender():
            try:
                s.sendall(head + body)
            except OSError as e:
                err.append(e)
        th = threading.Thread(target=sender)
        th.start()
        buf = b""
        try:
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        except OSError as e:
            err.append(e)
        th.join(20)
        s.close()
        return buf, err

    def parse(self, buf):
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        return lines[0].split(" ")[1] if lines and " " in lines[0] else None, json.loads(body.decode("utf-8")) if body else None

    def test_413_body_x200_sent_fully_response_read(self):
        """Тело ×200 предела (12,8 МБ) клиент досылает целиком без сброса и читает 413 — 20 раз подряд."""
        body = b"x" * (S.MAX_BODY * 200)
        head = (f"PATCH /api/tasks/2 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        bad = []
        for i in range(20):
            buf, err = self.send_full(head, body)
            code, res = self.parse(buf) if buf else (None, None)
            if err or code != "413" or not res or res.get("ok") is not False:
                bad.append((i, code, [type(e).__name__ + ": " + str(e)[:60] for e in err]))
        self.assertFalse(bad, f"FAIL соединение рвётся сбросом / ответ потерян: {bad[:5]}")

    def test_403_unread_body_sent_fully(self):
        """Запись не со страницы (нет Content-Type JSON) — 403 до чтения тела; тело 1 МБ клиент досылает без сброса."""
        body = b"y" * (1024 * 1024)
        head = (f"POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        bad = []
        for i in range(10):
            buf, err = self.send_full(head, body)
            code, _ = self.parse(buf) if buf else (None, None)
            if err or code != "403":
                bad.append((i, code, [type(e).__name__ for e in err]))
        self.assertFalse(bad, f"FAIL {bad[:5]}")

    def test_413_client_keeps_sending_after_response(self):
        """Клиент прочитал 413 и досылает остаток тела (браузер шлёт тело, не дожидаясь ответа): соединение не рвётся сбросом.
        На 1.3.12 сервер закрывал сокет с непрочитанными данными — досылка падала (WinError 10053/10054)."""
        bad = []
        for i in range(5):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            try:
                s.sendall((f"PATCH /api/tasks/2 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n"
                           f"Content-Length: {S.MAX_BODY * 40}\r\n\r\n").encode() + b"x" * 65536)
                buf = b""
                while True:
                    c = s.recv(65536)
                    if not c:
                        break
                    buf += c
                code, _ = self.parse(buf)
                err = None
                try:
                    for _ in range(20):
                        s.sendall(b"x" * 65536)
                        time.sleep(0.02)
                except OSError as e:
                    err = f"{type(e).__name__}: {e}"[:80]
                if code != "413" or err:
                    bad.append((i, code, err))
            finally:
                s.close()
        self.assertFalse(bad, f"FAIL досылка тела после ответа рвётся сбросом: {bad}")

    def test_400_read_body_no_delay(self):
        """Тело прочитано (не JSON) — 400 без дочитки: ответ и закрытие без задержки; дочитка вызывается только для непрочитанного тела."""
        calls, real = [], S.drain_after_error
        S.drain_after_error = lambda sock: calls.append(1) or real(sock)
        self.addCleanup(setattr, S, "drain_after_error", real)
        body = b"{not json"
        head = (f"POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(head + body)
            t0, buf = time.monotonic(), b""
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
            dt = time.monotonic() - t0
        code, res = self.parse(buf)
        self.assertEqual(code, "400")
        self.assertLess(dt, 1.0, "ответ на ошибку с прочитанным телом не должен ждать дочитки")
        time.sleep(0.3)
        self.assertEqual(calls, [], "тело прочитано — дочитки нет")
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:  # 413 по заявленной длине — дочитка есть
            s.sendall(f"PATCH /api/tasks/2 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: {S.MAX_BODY + 10}\r\n\r\n".encode())
            while s.recv(65536):
                pass
        time.sleep(0.3)
        self.assertEqual(len(calls), 1, "413 до чтения тела — одна дочитка")

    def test_drain_limited_by_time(self):
        """Клиент не шлёт тело и не закрывает соединение — дочитка кончается за LINGER_SEC."""
        a, b = socket.socketpair()
        try:
            t0 = time.monotonic()
            S.drain_after_error(a)
            dt = time.monotonic() - t0
            self.assertLess(dt, S.LINGER_SEC + 0.7)
            self.assertGreaterEqual(dt, S.LINGER_SEC - 0.1, "ждёт тело до предела, а не бросает сразу")
            self.assertLess(dt, 3.0, "дочитка не дольше 3 с при любой настройке")
            b.settimeout(2)
            self.assertEqual(b.recv(10), b"", "после ответа — FIN: клиент видит конец ответа")
        finally:
            a.close()
            b.close()

    def test_body_unread_logic(self):
        """Дочитка — только когда тело есть и не прочитано: прочитанное (400) и пустое — без неё; неверная длина — с ней."""
        class H:  # noqa: D401 — заглушка обработчика: только заголовки и признак чтения
            pass
        f = lambda cl, read: S.Handler.body_unread(type("H", (), {"headers": {"Content-Length": cl} if cl is not None else {}, "_body_read": read})())
        self.assertTrue(f("100", False))
        self.assertFalse(f("100", True), "тело прочитано — дочитывать нечего")
        self.assertFalse(f(None, False))
        self.assertFalse(f("0", False))
        self.assertTrue(f("abc", False), "неверная длина — тело могло прийти")

    def test_drain_limited_by_size(self):
        """Дочитка не больше LINGER_MAX: поток данных без конца не держит сервер."""
        a, b = socket.socketpair()
        old = S.LINGER_MAX
        S.LINGER_MAX = 200_000
        stop = threading.Event()

        def flood():
            try:
                while not stop.is_set():
                    b.sendall(b"z" * 65536)
            except OSError:
                pass
        th = threading.Thread(target=flood, daemon=True)
        th.start()
        try:
            t0 = time.monotonic()
            got = S.drain_after_error(a)
            dt = time.monotonic() - t0
            self.assertGreaterEqual(got, 200_000)
            self.assertLess(got, 200_000 + 65536 + 1)
            self.assertLess(dt, 1.0, "предел объёма, а не времени")
        finally:
            S.LINGER_MAX = old
            stop.set()
            a.close()
            b.close()
            th.join(3)


if __name__ == "__main__":
    unittest.main()
