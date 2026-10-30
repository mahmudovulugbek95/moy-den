"""Сборка программы одной командой:  .venv\\Scripts\\python.exe build_app.py

Собирает app.py (вместе с server.py и wallpaper.py) в один exe со значком: _сборка\\dist\\MoyDen.exe.
Страница дашборд.html и значок зашиваются в exe: программа показывает свою страницу, а не ту, что лежит в папке данных.
data.json и настройки в exe не зашиваются.
Пересобирать нужно после правок server.py, app.py, wallpaper.py или дашборд.html. Приложение перед сборкой закройте (трей → Выход).
"""
import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
OUT = HERE / "_сборка"

PyInstaller.__main__.run([
    str(HERE / "app.py"), "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "MoyDen",
    "--icon", str(HERE / "мой_день.ico"), "--paths", str(HERE),
    "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "build"), "--specpath", str(OUT),
    "--collect-all", "icalendar", "--collect-all", "recurring_ical_events", "--collect-all", "x_wr_timezone",
    "--collect-data", "tzdata", "--collect-all", "webview", "--hidden-import", "pystray._win32",
    "--add-data", f"{HERE / 'дашборд.html'};.", "--add-data", f"{HERE / 'мой_день.ico'};.",
])
exe = OUT / "dist" / "MoyDen.exe"
print("готово:", exe, f"{exe.stat().st_size / 1e6:.1f} МБ")
sys.exit(0)
