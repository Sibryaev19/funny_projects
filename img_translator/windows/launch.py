"""Единая точка входа для windows/*.bat.

Команды:
  transmitter — сервер передатчика + открыть страницу в браузере
  receiver    — приёмник (настройки из .env; доп. аргументы пробрасываются)
  test        — прогон tests/run_all.py
  preflight   — проверка готовности машины (windows/preflight.py)

Запускается интерпретатором из .venv (см. .bat-файлы рядом);
дочерние процессы поднимаются через sys.executable.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> int:
    return subprocess.call([sys.executable, *args], cwd=str(ROOT))


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "transmitter":
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
        return _run(["-m", "uvicorn", "server.app:app", "--port", "8000"])
    if cmd == "receiver":
        return _run(["-m", "receiver.main", *rest])
    if cmd == "test":
        return _run(["tests/run_all.py"])
    if cmd == "preflight":
        return _run([str(ROOT / "windows" / "preflight.py")])
    print(f"неизвестная команда: {cmd}\nкоманды: transmitter | receiver | test | preflight")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
