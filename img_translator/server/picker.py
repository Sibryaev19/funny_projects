"""Окно выбора файлов и папок на tkinter (мультивыбор, файлы и папки вперемешку).

Запускается в отдельном процессе: tkinter должен жить в главном потоке,
а эндпоинты FastAPI выполняются в пуле потоков.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk


def ask_paths(title: str = "Выбрать файлы и папки", start_dir: str | None = None) -> list[str]:
    """Открывает диалог; возвращает список выбранных путей (может быть пустым)."""
    state = {"current": os.path.expanduser(start_dir or "~"), "chosen": []}

    root = tk.Tk()
    root.title(title)
    root.geometry("680x440")
    root.attributes("-topmost", True)

    location = tk.StringVar(value=state["current"])
    listbox = tk.Listbox(root, selectmode="extended", activestyle="dotbox")

    def entries_of(path: str) -> list[tuple[str, bool]]:
        out = []
        try:
            names = sorted(os.listdir(path), key=str.lower)
        except OSError:
            return []
        for n in names:
            full = os.path.join(path, n)
            if os.path.isdir(full):
                out.append((n + "/", True))
            elif os.path.isfile(full):
                out.append((n, False))
        return out

    def refresh() -> None:
        state["current"] = os.path.normpath(state["current"])
        location.set(state["current"])
        listbox.delete(0, "end")
        for name, _is_dir in entries_of(state["current"]):
            listbox.insert("end", name)

    def enter(event=None) -> None:
        sel = listbox.curselection()
        if len(sel) != 1:
            return
        name, is_dir = entries_of(state["current"])[sel[0]]
        if is_dir:
            state["current"] = os.path.join(state["current"], name.rstrip("/"))
            refresh()

    def go_up() -> None:
        state["current"] = os.path.dirname(state["current"]) or "/"
        refresh()

    def go_location(event=None) -> None:
        path = os.path.expanduser(location.get())
        if os.path.isdir(path):
            state["current"] = path
        refresh()

    def ok(event=None) -> None:
        state["chosen"] = [
            os.path.join(state["current"], entries_of(state["current"])[i][0].rstrip("/"))
            for i in listbox.curselection()
        ]
        root.destroy()

    ttk.Label(root, text="Папка:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
    loc_entry = ttk.Entry(root, textvariable=location)
    loc_entry.bind("<Return>", go_location)
    loc_entry.grid(row=0, column=1, sticky="ew", padx=4)
    ttk.Button(root, text="↑ Вверх", command=go_up).grid(row=0, column=2, padx=4)
    listbox.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
    listbox.bind("<Double-Button-1>", enter)
    listbox.bind("<Return>", enter)
    ttk.Button(root, text="Отмена", command=root.destroy).grid(row=2, column=0, sticky="w", padx=4, pady=4)
    ttk.Button(root, text="ОК", command=ok).grid(row=2, column=2, sticky="e", padx=4, pady=4)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(1, weight=1)

    root.lift()
    root.focus_force()
    refresh()
    # grab только когда окно реально видно, иначе на Linux падает "grab failed"
    root.wait_visibility()
    root.grab_set()
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    return state["chosen"]


def _die_with_parent() -> None:
    """Ядро убьёт этот процесс, если умрёт родитель (даже по SIGKILL)."""
    try:
        import ctypes
        import signal

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG = 1
        if os.getppid() == 1:  # родитель уже умер до установки
            os._exit(1)
    except Exception:
        pass


def _dialog_worker(queue) -> None:
    """Точка входа дочернего процесса: диалог -> результат в очередь."""
    _die_with_parent()
    try:
        queue.put({"paths": ask_paths(), "error": None})
    except Exception as e:  # например, нет дисплея — не роняем сервер
        queue.put({"paths": [], "error": str(e)})
