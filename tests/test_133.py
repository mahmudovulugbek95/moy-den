"""значки рабочего стола не остаются под окном обоев — чистая логика wallpaper.plan_icons (без Проводника).
Запуск: python -m unittest tests.test_133"""
import re
import sys
import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEV))
import wallpaper as WPM  # noqa: E402

WIN = (120, 0, 2560, 1392)      # окно обоев пользователя (полоса 120)
STRIP = (0, 0, 120, 1392)
SP = (75, 91)                    # шаг сетки значков при 96 dpi
ic = lambda i, name, pos, rect: {"i": i, "name": name, "pos": list(pos), "rect": list(rect)}
OWNER = [ic(0, "Новая папка", (18, 2), (0, 5, 75, 66)), ic(1, "Лист Microsoft Excel.xlsx", (18, 93), (0, 96, 75, 172)),
         ic(2, "Мой день", (18, 184), (0, 187, 75, 248)), ic(3, "Корзина", (2493, 1276), (2475, 1279, 2550, 1340))]


class Icons133(unittest.TestCase):
    def test_owner_recycle_bin_goes_to_first_free_cell_of_strip(self):
        moves, left = WPM.plan_icons(OWNER, WIN, STRIP, SP)
        self.assertEqual([(m[0]["name"], m[1]) for m in moves], [("Корзина", [18, 275])])  # под «Мой день», та же сетка
        self.assertEqual(left, [])

    def test_icons_in_strip_are_not_touched(self):
        moves, left = WPM.plan_icons(OWNER[:3], WIN, STRIP, SP)
        self.assertEqual((moves, left), ([], []))

    def test_icon_partly_under_window_is_moved(self):  # рамка пересекает край окна (x 100..175) — значок частично закрыт
        moves, _ = WPM.plan_icons(OWNER[:3] + [ic(3, "Отчёт.pdf", (118, 2), (100, 5, 175, 66))], WIN, STRIP, SP)
        self.assertEqual([m[0]["name"] for m in moves], ["Отчёт.pdf"])
        self.assertLessEqual(moves[0][1][0] + 57, 120)

    def test_two_icons_get_two_different_cells(self):
        under = [ic(3, "А", (500, 500), (482, 503, 557, 564)), ic(4, "Б", (900, 500), (882, 503, 957, 564))]
        moves, _ = WPM.plan_icons(OWNER[:3] + under, WIN, STRIP, SP)
        self.assertEqual([m[1] for m in moves], [[18, 275], [18, 366]])

    def test_full_strip_reports_left_over_instead_of_overlapping(self):  # FAIL-сценарий: мест нет — не класть поверх, а сообщить
        full = [ic(k, f"з{k}", (18, 2 + 91 * k), (0, 5 + 91 * k, 75, 66 + 91 * k)) for k in range(15)]
        moves, left = WPM.plan_icons(full + [ic(15, "Корзина", (2493, 1276), (2475, 1279, 2550, 1340))], WIN, STRIP, SP)
        self.assertEqual(moves, [])
        self.assertEqual([x["name"] for x in left], ["Корзина"])

    def test_new_cell_never_overlaps_taller_label(self):  # подпись в 2 строки (Excel 96..172) — следующая клетка не налезает
        moves, _ = WPM.plan_icons(OWNER, WIN, STRIP, SP)
        new_rect = [2475 - 2493 + 18, 1279 - 1276 + 275, 2550 - 2493 + 18, 1340 - 1276 + 275]
        for o in OWNER[:3]:
            self.assertFalse(WPM._cross(new_rect, o["rect"]), o["name"])

    def test_version_in_about(self):  # версия поднята (реестр, манифест, «О программе»)
        src = (DEV / "app.py").read_text("utf-8")
        self.assertRegex(src, r'VERSION = "1\.0\.0"')
        self.assertIn("версия {VERSION}", src)
        self.assertRegex((DEV / "установщик.py").read_text("utf-8"), r'(?m)^VERSION = "1\.0\.0"')  # реестр и манифест — та же версия

    def test_page_defers_render_while_mouse_pressed(self):  # причина дефекта ☆: перерисовка между нажатием и отпусканием
        html = (DEV / "дашборд.html").read_text("utf-8")
        self.assertRegex(html, r"function scheduleRender\(\)\{\s*if\(pressing\(\)\)\{ pending=true; armPress\(\); return; \}")  # поведение — обои-134.js
        self.assertTrue(re.search(r'addEventListener\("pointerdown",\(\)=>\{ downAt=performance\.now\(\); \},true\)', html))


if __name__ == "__main__":
    unittest.main()
