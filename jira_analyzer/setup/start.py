"""Установка окружения jira_analyzer: venv + зависимости + проверка .env.

Запуск:
    Windows: setup.bat (двойной клик)  — bat сам найдёт подходящий python
    вручную: python setup/start.py     — тем python'ом, который считаешь подходящим

Что делает:
  1. Находит подходящий python 3.11+ (перебор кандидатов с фактическим запуском:
     первым в PATH может стоять неподходящий, например Anaconda 3.9)
  2. Создаёт .venv в корне проекта; битый .venv (python не запускается)
     распознаётся и пересоздаётся
  3. Ставит зависимости из requirements.txt (pip при необходимости поднимается
     ensurepip'ом интерпретатором venv, не базовым — иначе pip уйдёт «в пользователя»)
  4. Проверяет .env; если нет — создаёт копию .env.example
  5. Контрольный импорт ключевых библиотек

Диагностика окружения без изменений: setup.bat --selftest
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
MIN_PY = (3, 11)

IS_WINDOWS = sys.platform.startswith("win")


# ---------------------------------------------------------------- консоль

def _force_utf8_console() -> None:
    """Русские сообщения в консоли Windows: кодовая страница 65001 + UTF-8 потоки."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ------------------------------------------------- поиск подходящего python

def _run_probe(cmd: list[str], args: list[str] | None = None) -> tuple[int, str]:
    """Запустить команду, вернуть (код возврата, stdout+stderr). Без вывода наружу."""
    try:
        r = subprocess.run(
            [str(c) for c in cmd] + (args or []),
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_ROOT,
            errors="replace",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _probe_ok(cmd: list[str]) -> bool:
    """Команда вообще запускается? (битый путь/ярлык дают ненулевой код или таймаут)"""
    code, _ = _run_probe(cmd, ["-c", "pass"])
    return code == 0


def _version_of(cmd: list[str]) -> tuple[int, int] | None:
    """Версия интерпретатора или None, если не удалось узнать."""
    code, out = _run_probe(cmd, ["-c",
        "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    if code != 0:
        return None
    line = out.strip().splitlines()
    if not line:
        return None
    try:
        parts = [int(p) for p in line[-1].strip().split(".")]
        return (parts[0], parts[1])
    except (ValueError, IndexError):
        return None


def _candidate_interpreters() -> list[list[str]]:
    """Все разумные способы запустить python на этой машине, в порядке приоритета.

    Порядок в PATH ничего не значит (первым может стоять Anaconda 3.9),
    поэтому проверяем каждый фактическим запуском, а не «первый найденный».
    """
    cands: list[list[str]] = [
        ["py", "-3"],
        [sys.executable],  # тот, которым запущен сам установщик
        ["python"],
        ["python3"],
    ]
    bases = []
    if IS_WINDOWS:
        la = os.environ.get("LOCALAPPDATA")
        if la:
            bases.append(Path(la) / "Programs" / "Python")
        bases.append(Path("C:\\Program Files"))
        bases.append(Path("C:\\"))
        for base in bases:
            for ver in ("Python314", "Python313", "Python312", "Python311"):
                exe = base / ver / "python.exe"
                if exe.is_file():
                    cands.append([str(exe)])
    # уникальные
    seen, out = set(), []
    for c in cands:
        key = tuple(str(x).lower() for x in c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _suitable_interpreters() -> list[tuple[list[str], tuple[int, int]]]:
    """Кандидаты, которые реально запускаются и имеют версию 3.11+."""
    out = []
    for cmd in _candidate_interpreters():
        ver = _version_of(cmd)
        if ver is not None and ver >= MIN_PY:
            out.append((cmd, ver))
    return out


def _choose_interpreter() -> list[str]:
    """Подходящий интерпретатор; выйти с понятным сообщением, если нет."""
    suitable = _suitable_interpreters()
    if not suitable:
        print(f"[setup] Не найден Python {MIN_PY[0]}.{MIN_PY[1]}+ (проверены py -3, "
              f"python, python3 и стандартные пути установки).")
        print("        Установи Python 3.11+ с https://www.python.org/downloads/ "
              "и запусти setup.bat снова.")
        sys.exit(1)
    cmd, ver = suitable[0]
    if len(suitable) > 1:
        others = ", ".join(f"{c[-1]} ({v[0]}.{v[1]})" for c, v in suitable[1:])
        print(f"[setup] Подходящих интерпретаторов несколько: беру {cmd[-1]} "
              f"({ver[0]}.{ver[1]}); ещё: {others}")
    return cmd


# ------------------------------------------------------------------- venv

def _venv_python(venv: Path = VENV_DIR) -> Path:
    name = "python.exe" if IS_WINDOWS else "python"
    return venv / ("Scripts" if IS_WINDOWS else "bin") / name


def _venv_alive() -> bool:
    """venv существует и его python реально запускается.

    Битые venv после удаления/переустановки базового python дают ошибку
    запуска ('No Python at ...') — их надо пересоздавать, а не использовать.
    """
    py = _venv_python()
    return py.exists() and _probe_ok([str(py)])


def _ensure_venv(base_cmd: list[str]) -> None:
    if _venv_alive():
        print(f"[2/5] venv уже есть: {VENV_DIR}")
        return
    if _venv_python().exists():
        print("[2/5] Найден БИТЫЙ venv (его python не запускается — базовый "
              "python удаляли или переустанавливали). Пересоздаю.")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    print(f"[2/5] Создаю venv: {VENV_DIR}")
    code, out = _run_probe(base_cmd, ["-m", "venv", str(VENV_DIR)])
    if code != 0 or not _venv_alive():
        print(f"[setup] Не удалось создать venv (код {code}):\n{out}")
        sys.exit(1)
    # pip в свежем venv: поднять через ensurepip ИМЕННО интерпретатором venv —
    # ensurepip базового интерпретатора ставит pip «в пользователя», не в venv.
    venv_py = str(_venv_python())
    if not _pip_ok([venv_py]):
        print("[2/5] pip в venv нет — поднимаю через ensurepip")
        code, out = _run_probe([venv_py], ["-m", "ensurepip", "--upgrade"])
        if code != 0 or not _pip_ok([venv_py]):
            print(f"[setup] ensurepip не справился (код {code}):\n{out}")
            sys.exit(1)


def _pip_ok(cmd: list[str]) -> bool:
    code, _ = _run_probe(cmd, ["-m", "pip", "--version"])
    return code == 0


# ------------------------------------------------------------------- pip

def _pip_install() -> None:
    """Установка requirements в venv. Если venv-шный pip не сработал — поднять
    его заново через ensurepip интерпретатором venv и повторить.

    НЕ запускать pip базовым/запасным интерпретатором: зависимости ушли бы
    в базовое окружение, а не в venv. И НЕ оборачивать pip в python -c c
    inject_into_ssl: двойной патч ssl (наш поверх pip'овского) → RecursionError
    (pip>=24.2 сам доверяет хранилищу Windows через свой truststore)."""
    args = ["-m", "pip", "install", "--disable-pip-version-check",
            "-r", str(PROJECT_ROOT / "requirements.txt")]
    vpy = [str(_venv_python())]
    if _run_visible(vpy, args):
        return
    print("[setup] pip не сработал — поднимаю pip заново через ensurepip и повторяю")
    _run_probe(vpy, ["-m", "ensurepip", "--upgrade"])
    if not _run_visible(vpy, args):
        sys.exit(1)


def _run_visible(cmd: list[str], args: list[str]) -> bool:
    print("  $", " ".join(str(c) for c in cmd + args), flush=True)
    r = subprocess.run([str(c) for c in cmd] + args, cwd=PROJECT_ROOT)
    if r.returncode != 0:
        print(f"[setup] Команда завершилась с кодом {r.returncode}. Выше — её вывод.")
        return False
    return True


# --------------------------------------------------------------- selftest

def _selftest() -> int:
    """Диагностика окружения, ничего не меняет."""
    print("=" * 60)
    print("Selftest: диагностика окружения (ничего не меняю)")
    print("=" * 60)
    print(f"Запущен:          {sys.executable} "
          f"({sys.version_info.major}.{sys.version_info.minor}"
          f".{sys.version_info.micro})")
    ok = True
    any_good = False
    for cmd in _candidate_interpreters():
        ver = _version_of(cmd)
        mark = f"{ver[0]}.{ver[1]}" if ver else "не запускается"
        good = ver is not None and ver >= MIN_PY
        any_good = any_good or good
        print(f"  {'OK ' if good else '   '} {cmd[-1]:>12}: {mark}")
    ok = ok and any_good
    if _venv_alive():
        vpy = str(_venv_python())
        print(f"venv:             {VENV_DIR} — рабочий")
        code, out = _run_probe([vpy], ["-m", "pip", "--version"])
        print(f"pip в venv:       {out.strip().splitlines()[0] if code == 0 else 'нет!'}")
        code, out = _run_probe([vpy, "-c", "import truststore; print(truststore.__version__)"])
        print(f"truststore в venv:{'' if code == 0 else ' нет!'} {out.strip().splitlines()[0] if code == 0 else ''}")
    elif _venv_python().exists():
        print(f"venv:             {VENV_DIR} — БИТЫЙ (python не запускается)")
        ok = False
    else:
        print(f"venv:             нет ({VENV_DIR})")
    print("=" * 60)
    print("Selftest завершён." + ("" if ok else " (есть проблемы, см. выше)"))
    return 0 if ok else 1


# ------------------------------------------------------------------- main

def main() -> None:
    _force_utf8_console()
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())

    print("=" * 60)
    print("Настройка окружения jira_analyzer")
    print("=" * 60)

    # 1. интерпретатор: запущенный не обязан подходить — ищем реально подходящий
    launched_ok = sys.version_info >= MIN_PY
    if launched_ok:
        base = [sys.executable]
        print(f"[1/5] Python {sys.version_info.major}.{sys.version_info.minor}"
              f".{sys.version_info.micro} (запущен) — OK")
    else:
        print(f"[1/5] Запущен Python {sys.version_info.major}."
              f"{sys.version_info.minor} — ищу подходящий 3.11+")
        base = _choose_interpreter()
        print(f"[1/5] Выбран: {base[-1]}")

    # 2. venv (с пересозданием битого)
    _ensure_venv(base)

    # 3. зависимости
    print("[3/5] Устанавливаю зависимости (при повторном запуске — быстро)")
    _pip_install()

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
    if not _run_visible([str(_venv_python())], ["-c", code]):
        sys.exit(1)

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
