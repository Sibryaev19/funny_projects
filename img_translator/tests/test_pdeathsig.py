"""PDEATHSIG: диалог-процесс должен умирать вместе с родителем даже по SIGKILL."""

import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.picker import _dialog_worker

if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        print("пропущено: PR_SET_PDEATHSIG — только Linux")
        sys.exit(0)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_dialog_worker, args=(q,), daemon=True)
    p.start()
    print(p.pid, flush=True)
    time.sleep(120)  # тестовый скрипт убьют снаружи по SIGKILL
