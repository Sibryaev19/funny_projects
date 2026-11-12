"""Установка окружения jira_analyzer: venv + зависимости + проверка .env.

Запуск:
    Windows: setup.bat (двойной клик)  — bat сам найдёт python
    вручную: python setup/start.py     — тем python'ом, который считаешь подходящим

Что делает:
  1. Проверяет версию python (3.11+)
  2. Создаёт .venv в корне проекта (если ещё нет)
  3. Ставит зависимости из requirements.txt
  4. Проверяет .env; если нет — создаёт копию .env.example
  5. Контрольный импорт ключевых библиотек
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
MIN_PY = (3, 11)

IS_WINDOWS = sys.platform.startswith("win")


def _venv_python() -> Path:
    name = "python.exe" if IS_WINDOWS else "python"
    return VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / name


def _run(cmd: list[str]) -> None:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if r.returncode != 0:
        print(f"[setup] Команда завершилась с кодом {r.returncode}. Выше — её вывод.")
        sys.exit(r.returncode)


def main() -> None:
    print("=" * 60)
    print("Настройка окружения jira_analyzer")
    print("=" * 60)

    # 1. версия интерпретатора, которым нас запустили
    if sys.version_info < MIN_PY:
        print(f"[setup] Нужен Python {MIN_PY[0]}.{MIN_PY[1]}+, а запущен "
              f"{sys.version_info.major}.{sys.version_info.minor}. "
              f"Установи Python 3.11+ с https://python.org и перезапусти setup.bat")
        sys.exit(1)
    print(f"[1/5] Python {sys.version_info.major}.{sys.version_info.minor}"
          f".{sys.version_info.micro} — OK")

    # 2. venv
    venv_py = _venv_python()
    if venv_py.exists():
        print(f"[2/5] venv уже есть: {VENV_DIR}")
    else:
        print(f"[2/5] Создаю venv: {VENV_DIR}")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
        if not venv_py.exists():  # перестраховка для нестандартных случаев
            print(f"[setup] Не нашли python внутри venv: {venv_py}")
            sys.exit(1)

    # 3. зависимости
    print("[3/5] Устанавливаю зависимости (при повторном запуске — быстро)")
    _run([str(venv_py), "-m", "pip", "install", "--disable-pip-version-check", "-q",
          "-r", str(PROJECT_ROOT / "requirements.txt")])

    # 4. .env
    env_path = PROJECT_ROOT / ".env"
    example = PROJECT_ROOT / ".env.example"
    if env_path.exists():
        print("[4/5] .env на месте")
    elif example.exists():
        shutil.copy(example, env_path)
        print("[4/5] .env создан из шаблона! Открой его и заполни секцию ТОКЕНЫ "
              "(JIRA_TOKEN / JIRA_DELTA_TOKEN) и адреса корпоративных Jira.")
    else:
        print("[4/5] ВНИМАНИЕ: нет ни .env, ни .env.example — конфиг не проверяем")

    # 5. контрольный импорт
    print("[5/5] Контрольный импорт библиотек")
    code = ("import requests, pandas, openpyxl, fastapi; "
            "print('       requests', requests.__version__, '| pandas', pandas.__version__, "
            "'| fastapi', fastapi.__version__)")
    _run([str(venv_py), "-c", code])

    print("=" * 60)
    print("Готово! Дальше:")
    print('  тестовый прогон (открытая Apache Jira, без токена):')
    print("    .venv\\Scripts\\python main.py --project CURATOR --jira-type apache"
          if IS_WINDOWS else
          "    .venv/bin/python main.py --project CURATOR --jira-type apache")
    print('  рабочий прогон: заполнить .env, затем')
    print("    python main.py --project КЛЮЧ_ПРОЕКТА --jira-type jira")
    print("=" * 60)


if __name__ == "__main__":
    main()
