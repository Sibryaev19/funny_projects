"""Проверка: tkinter работает в главном потоке отдельного spawn-процесса.

Именно на этом упала первая версия /api/pick: эндпоинт FastAPI выполняется
в пуле потоков, а tkinter обязан жить в главном потоке. Сама механика
"/api/pick -> spawn-процесс -> очередь" идентична этой проверке.
"""

import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _tk_probe(q):
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        q.put({"ok": True, "error": None})
    except Exception as e:
        q.put({"ok": False, "error": str(e)})


if __name__ == "__main__":
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_tk_probe, args=(q,))
    p.start()
    res = q.get(timeout=30)
    p.join(timeout=5)
    if p.is_alive():
        p.terminate()
    print("результат:", res)
    if not res["ok"]:
        sys.exit(f"tkinter в дочернем процессе не работает: {res['error']}")
    print("tkinter в spawn-процессе работает — механика /api/pick исправна")
