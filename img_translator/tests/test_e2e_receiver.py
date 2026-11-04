"""E2E цикла приёма без экрана: FakeCapture кормит receive() готовыми кадрами.

Проверяет весь конвейер приёмника: цикл, дедупликацию, прогресс, сборку,
zip-проверку и распаковку. (Реальный захват экрана проверяется вручную.)
"""

import io
import random
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from receiver.main import receive, unpack
from protocol.encoder import encode_frames
from test_vision import render

W, H, PALETTE, CELL = 64, 48, 4, 8


class FakeCapture:
    """Кадры «с экрана»: циклично показывает image[по времени]."""

    closed = False

    def __init__(self, images: list[np.ndarray], period: float):
        self.images = images
        self.period = period
        self.t0 = time.time()

    def grab(self, region) -> np.ndarray:
        i = int((time.time() - self.t0) / self.period) % len(self.images)
        return self.images[i]

    def close(self):
        self.closed = True


def make_zip() -> bytes:
    random.seed(11)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("привет.txt", "привет из кадров!" * 40)
        zf.writestr("dir/binary.bin", bytes(random.randrange(256) for _ in range(700)))
    return buf.getvalue()


def main():
    data = make_zip()
    frames = encode_frames(data, W, H, PALETTE)
    rng = np.random.default_rng(11)
    images = [render(f, PALETTE, CELL, (20, 30), 8, rng) for f in frames]
    region = (0, 0, images[0].shape[1], images[0].shape[0])
    print(f"zip: {len(data)} байт, кадров: {len(frames)}, «экран» {region[2]}x{region[3]}")

    notes = []
    cap = FakeCapture(images, period=0.4)
    got = receive(region, (W, H), PALETTE, fps=6, timeout=30,
                  on_progress=lambda g, t, n: notes.append((g, t, n)) and print(f"  кадров {g}/{t} {n}"),
                  capture=cap)
    assert got == data, "данные не совпали"
    assert cap.closed, "capture не закрыт"
    assert any(g == len(frames) for g, _, _ in notes), "прогресс не дошёл до конца"

    out = Path("/tmp/it_e2e_out")
    unpack(got, str(out))
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    print("файлы:", files)
    assert open(out / "привет.txt", encoding="utf-8").read() == "привет из кадров!" * 40

    # мусор на «экране» -> таймаут, а не ложный успех
    junk = FakeCapture([np.full((300, 300, 3), 90, np.uint8)], period=0.1)
    try:
        receive((0, 0, 300, 300), (W, H), PALETTE, fps=10, timeout=2, capture=junk)
        raise AssertionError("таймаут не сработал")
    except TimeoutError as e:
        print("таймаут ок:", e)

    print("E2E ПРИЁМНИКА ПРОЙДЕН")


if __name__ == "__main__":
    main()
