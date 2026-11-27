"""Запуск всего неинтерактивного тестового набора.

    python tests/run_all.py

Не входят сюда (запускаются руками):
  - test_pdeathsig.py — требует внешнего SIGKILL родителя;
  - limits_probe.py — интерактивный (диалог портала) и меряет реальный экран.
"""

import subprocess
import sys
import time
from pathlib import Path

TESTS = [
    "test_encoder.py",          # протокол: круговые тесты всех палитр
    "test_vision.py",           # зрение: синтетические скриншоты
    "test_e2e_receiver.py",     # цикл приёма без экрана (FakeCapture)
    "test_picker_process.py",   # tkinter в отдельном процессе
    "test_env_config.py",       # дефолты интерфейса из .env
    "test_streaming.py",        # стриминг кадров: сессии на диске + /api/frame
]

def main() -> int:
    here = Path(__file__).parent
    failed = []
    for name in TESTS:
        print(f"--- {name} ---", flush=True)
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, str(here / name)],
            cwd=str(here.parent),
        )
        status = "OK" if r.returncode == 0 else f"ПРОВАЛ (код {r.returncode})"
        print(f"=== {name}: {status} за {time.time()-t0:.1f}с ===\n", flush=True)
        if r.returncode != 0:
            failed.append(name)

    if failed:
        print(f"ПРОВАЛЕНО: {', '.join(failed)}")
        return 1
    print("все тесты пройдены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
