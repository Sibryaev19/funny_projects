"""Корневой conftest: путь проекта в sys.path (чтобы `pytest` работал
из любого каталога, а не только `python -m pytest` из корня)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
