"""Сборка установщика одной командой:  .venv\\Scripts\\python.exe build_installer.py
Сначала собирает программу (build_app.py), затем установщик со встроенной программой: _сборка\\MoyDenSetup-<версия>.exe
(имя латиницей — так файл без искажений загружается в GitHub Releases).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
OUT = HERE / "_сборка"
if "--skip-app" not in sys.argv:  # программа уже собрана — только установщик
    subprocess.run([sys.executable, str(HERE / "build_app.py")], check=True)
pay = OUT / "payload"
pay.mkdir(exist_ok=True)
shutil.copyfile(OUT / "dist" / "MoyDen.exe", pay / "MoyDen.exe")
shutil.copyfile(HERE / "мой_день.ico", pay / "мой_день.ico")
PyInstaller.__main__.run([str(HERE / "установщик.py"), "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "MoyDenSetup",
                          "--icon", str(HERE / "мой_день.ico"), "--add-data", f"{pay};payload",
                          "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "build-setup"), "--specpath", str(OUT)])
ver = re.search(r'(?m)^VERSION = "([\d.]+)"', (HERE / "установщик.py").read_text("utf-8")).group(1)
dst = OUT / f"MoyDenSetup-{ver}.exe"
shutil.copyfile(OUT / "dist" / "MoyDenSetup.exe", dst)
print("готово:", dst, f"{dst.stat().st_size / 1e6:.1f} МБ")
