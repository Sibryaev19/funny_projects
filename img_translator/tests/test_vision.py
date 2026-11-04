"""Тесты protocol.vision: синтетические «скриншоты» -> decode -> исходные байты."""

import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol import encoder as E
from protocol.vision import VisionError, read_frame

def render(frame, palette: int, cell: float, off: tuple[int, int], noise: int, rng) -> np.ndarray:
    """Кадр -> «скриншот»: шумный пёстрый фон, кадр в off, ячейка cell px."""
    h, w = len(frame.grid), len(frame.grid[0])
    shape = (off[0] + int(h * cell) + 60, off[1] + int(w * cell) + 60)
    img = rng.integers(0, 255, shape + (3,), dtype=np.uint8)
    # фон из крупных пёстрых блоков — «чужой интерфейс»
    img = img.repeat(24, 0).repeat(24, 1)[: shape[0], : shape[1]]
    pal = E.PALETTES[palette]
    colors = np.array(list(pal) + [(255, 0, 255)], dtype=np.uint8)
    g = np.array(frame.grid, dtype=np.int64)
    g = np.where(g == E.MAGENTA, len(pal), g)  # рамка -> последний цвет
    block = colors[g]  # (h, w, 3), MAGENTA индексируем последним
    block = np.repeat(np.repeat(block, max(1, round(cell)), 0), max(1, round(cell)), 1)
    y0, x0 = off
    hh, ww = min(block.shape[0], shape[0] - y0), min(block.shape[1], shape[1] - x0)
    img[y0 : y0 + hh, x0 : x0 + ww] = block[:hh, :ww]
    if noise:
        img = img.astype(np.int16) + rng.integers(-noise, noise + 1, img.shape)
        img = np.clip(img, 0, 255).astype(np.uint8)
    return img


def try_stream(data: bytes, w, h, palette, cell, noise, seed=1):
    frames = E.encode_frames(data, w, h, palette)
    rng = np.random.default_rng(seed)
    grids = []
    for i, f in enumerate(frames):
        off = (30 + i * 17, 40 + i * 23)
        img = render(f, palette, cell, off, noise, rng)
        meta = read_frame(img, w, h, palette)
        assert meta["index"] == f.index and meta["total"] == f.total
        grids.append(meta["grid"])
    restored = E.decode_frames(grids)
    assert restored == data, f"{w}x{h} {palette}цв cell={cell} noise={noise}: байты не совпали"
    print(f"ok {w}x{h} {palette}цв cell={cell} noise={noise}: {len(data)} байт, {len(frames)} кадров")


def main() -> None:
    random.seed(7)
    rng = np.random.default_rng(7)

    # основные палитры/масштабы
    try_stream(b"hello receiver", 64, 48, 2, 9.0, 12)
    try_stream(bytes(random.randrange(256) for _ in range(3000)), 128, 96, 4, 7.3, 15)
    try_stream(bytes(random.randrange(256) for _ in range(9000)), 128, 96, 16, 5.5, 10)
    try_stream(bytes(random.randrange(256) for _ in range(1200)), 256, 192, 8, 3.2, 6)

    # нецелый pitch (масштаб окна «как попало»)
    try_stream(b"fractional pitch", 64, 48, 4, 6.7, 10)

    # пустая картинка -> внятная ошибка
    blank = np.full((400, 500, 3), 128, dtype=np.uint8)
    try:
        read_frame(blank, 64, 48, 2)
        raise AssertionError("пустой экран не отклонён")
    except VisionError as e:
        assert "рамка не найдена" in str(e), e

    # чужая сетка -> ошибка аспекта/заголовка
    f = E.encode_frames(b"x", 64, 48, 4)[0]
    img = render(f, 4, 8.0, (30, 40), 0, rng)
    for bad_grid in [(65, 48), (64, 49), (32, 24)]:
        try:
            read_frame(img, *bad_grid, 4)
            raise AssertionError(f"сетка {bad_grid} не отклонена")
        except VisionError:
            pass

    # чужая палитра -> ошибка с указанием фактической палитры
    img16 = render(E.encode_frames(b"y", 64, 48, 16)[0], 16, 8.0, (30, 40), 0, rng)
    try:
        read_frame(img16, 64, 48, 8)
        raise AssertionError("несовпадение палитры не поймано")
    except VisionError as e:
        assert "палитра" in str(e), e

    print("все тесты пройдены")


if __name__ == "__main__":
    main()
