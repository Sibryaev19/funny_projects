"""Замер безопасных пределов канала: цветов x размер ячейки, реальный экран.

Tkinter-окно рисует кадры (как будет рисовать игра), настоящий портал-захват
снимает экран; для каждой конфигурации считаем долю чистых снимков (CRC ok).
ВНИМАНИЕ: при старте появится диалог портала — нажми «Поделиться» один раз.

Результат: таблица в консоль + limits_results.json рядом со скриптом.
"""

import io
import json
import random
import sys
import threading
import time
import zipfile
from pathlib import Path

import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from protocol import encoder as E
from protocol.vision import read_frame, VisionError
from receiver.capture import open_capture

GRID_W, GRID_H = 64, 48
PAYLOAD_BYTES = 800
PAUSE_MS = 500        # пауза между кадрами в окне
SNAPS_PER_CONFIG = 20 # снимков на конфигурацию
SETTLE_S = 0.4        # пауза после перерисовки окна
FPS_PIPELINE = 6

CELLS = [12, 10, 8, 6, 5, 4, 3, 2]
COLORS = [16, 8, 4, 2]


def make_data() -> bytes:
    random.seed(99)
    return random.randbytes(PAYLOAD_BYTES)


class TransmitterWindow:
    """Рисует текущую конфигурацию; конфигурации прилетают из очереди."""

    def __init__(self, root):
        self.root = root
        self.canvas = tk.Canvas(root, highlightthickness=0, bg="white")
        self.canvas.pack()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry("+60+80")
        self.frames: list = []
        self.palette: int = 2
        self.cell: int = 10
        self.idx = 0
        self.drawn_at = 0.0
        self.queue: list = []
        self.root.after(50, self.tick)

    def set_config(self, frames, palette, cell):
        self.queue.append((frames, palette, cell))

    def tick(self):
        if self.queue:
            self.frames, self.palette, self.cell = self.queue.pop(0)
            self.idx = 0
            self.redraw()
        if self.frames:
            self.draw(self.idx)
            self.idx = (self.idx + 1) % len(self.frames)
        self.root.after(PAUSE_MS, self.tick)

    def redraw(self):
        cw, ch = GRID_W * self.cell, GRID_H * self.cell
        self.root.geometry(f"{cw}x{ch}+60+80")
        self.canvas.config(width=cw, height=ch)
        self.root.update_idletasks()
        self.drawn_at = time.time()

    def draw(self, fi):
        c = self.canvas
        c.delete("all")
        g = self.frames[fi].grid
        cell = self.cell
        pal = E.PALETTES[self.palette]
        for y in range(GRID_H):
            row = g[y]
            for x in range(GRID_W):
                v = row[x]
                color = "#f0f" if v == -1 else "#%02x%02x%02x" % pal[v]
                c.create_rectangle(x * cell, y * cell, (x + 1) * cell,
                                   (y + 1) * cell, fill=color, outline="")


def measure(cap, w, h, palette, snaps, settle: float) -> dict:
    time.sleep(settle)
    ok = 0
    errs: dict[str, int] = {}
    pitches: list[float] = []
    seen = set()
    for _ in range(snaps):
        img = cap.grab((0, 0, 3840, 2160))
        try:
            meta = read_frame(img, w, h, palette, min_pitch=1.0)
            E.decode_frame(meta["grid"])  # CRC
            ok += 1
            seen.add(meta["index"])
            pitches.append(None)  # placeholder
        except (VisionError, ValueError) as e:
            key = str(e).split(":")[0][:60]
            errs[key] = errs.get(key, 0) + 1
        time.sleep(1.0 / FPS_PIPELINE)
    # реальный pitch замеряем отдельно на последнем снимке
    try:
        img = cap.grab((0, 0, 3840, 2160))
        from protocol.vision import magenta_mask, find_frame_bbox
        bx, by, bw, bh = find_frame_bbox(magenta_mask(img))
        pitch = round(bw / w, 2)
    except VisionError:
        pitch = 0.0
    return {"ok": ok, "snaps": snaps, "clean": ok / snaps,
            "frames_seen": len(seen), "pitch": pitch, "errors": errs}


def main():
    data = make_data()
    root = tk.Tk()
    win = TransmitterWindow(root)

    results = []
    cap = open_capture("auto", fps=FPS_PIPELINE)
    print("== захват поднят; жду первый чистый кадр (закрой диалог, если открыт) ==",
          flush=True)

    # калибровка: убеждаемся, что окно видно и читается
    frames0 = E.encode_frames(data, GRID_W, GRID_H, 2)
    win.set_config(frames0, 2, 12)
    root.update()
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            img = cap.grab((0, 0, 3840, 2160))
            meta = read_frame(img, GRID_W, GRID_H, 2, min_pitch=1.0)
            E.decode_frame(meta["grid"])
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("не дождались чистого кадра — окно не видно из захвата?")
    print("== калибровка ок, начинаю замеры ==", flush=True)

    try:
        for palette in COLORS:
            frames = E.encode_frames(data, GRID_W, GRID_H, palette)
            for cell in CELLS:
                win.set_config(frames, palette, cell)
                root.update()
                res = measure(cap, GRID_W, GRID_H, palette, SNAPS_PER_CONFIG, SETTLE_S)
                res.update({"palette": palette, "cell": cell})
                results.append(res)
                print(f"цветов {palette:2d} | ячейка {cell:2d}px | "
                      f"pitch на экране {res['pitch']:5.1f}px | "
                      f"чистых {res['ok']:2d}/{res['snaps']} "
                      f"({res['clean']*100:5.1f}%) | {res['errors'] or ''}",
                      flush=True)
    finally:
        cap.close()
        out = Path(__file__).parent / "limits_results.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(f"\nрезультаты: {out}")
        root.destroy()


if __name__ == "__main__":
    main()
