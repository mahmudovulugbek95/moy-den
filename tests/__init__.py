# Проверки «Мой день»: python -m unittest discover -s tests -p "test_*.py" -t .
import os

os.environ.setdefault("MOYDEN_TZ", "Africa/Nairobi")  # пояс проверок задают они сами (+03:00, без летнего времени), а не компьютер
