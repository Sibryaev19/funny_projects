"""Зрение приёмника: скриншот (RGB ndarray) -> grid индексов палитры.

Пайплайн: маска мадженты -> bbox рамки (рамка обязана быть сплошным кольцом
по периметру bbox) -> pitch = размер рамки / W (сетка задана параметрами)
-> сэмплирование центров ячеек -> ближайший цвет палитры -> grid для
protocol.encoder.decode_frame(s) с проверкой маркеров, заголовка и CRC.
"""

from __future__ import annotations

import numpy as np

from protocol import encoder as E

MAGENTA_RGB = (255, 0, 255)

# пороги «похоже на мадженту»: r и b высокие, g низкий, r≈b.
# под них не попадает ни один цвет палитр (максимум по r у прочих — 255 у
# белого, но у него g высокий; фиолетовый 122,0,190 отсекается по r).
_MIN_RB, _MAX_G, _MAX_RB_DIFF = 150, 110, 120


class VisionError(RuntimeError):
    """Кадр не прочитан: рамки нет / геометрия не та / параметры не совпали."""


def magenta_mask(img: np.ndarray) -> np.ndarray:
    r = img[..., 0].astype(np.int16)
    g = img[..., 1].astype(np.int16)
    b = img[..., 2].astype(np.int16)
    return (
        (r >= _MIN_RB)
        & (b >= _MIN_RB)
        & (g <= _MAX_G)
        & (np.abs(r - b) <= _MAX_RB_DIFF)
    )


def find_frame_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Bbox рамки по проекциям: строки/столбцы с высоким покрытием маджентой.

    Устойчиво к случайным маджента-пятнам на остальном экране: рамка —
    единственное место, где покрытие тянется длинными полосами.
    """
    row_cov = mask.mean(axis=1)
    col_cov = mask.mean(axis=0)
    peak = max(row_cov.max(), col_cov.max())
    if peak < 0.04:
        raise VisionError("маджента-рамка не найдена в области")
    rows = np.where(row_cov >= 0.5 * row_cov.max())[0]
    cols = np.where(col_cov >= 0.5 * col_cov.max())[0]
    y0, y1 = int(rows.min()), int(rows.max())
    x0, x1 = int(cols.min()), int(cols.max())
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if w < 16 or h < 16:
        raise VisionError(f"маджента-область слишком мала: {w}x{h}px")
    per = np.concatenate([
        mask[y0, x0 : x1 + 1], mask[y1, x0 : x1 + 1],
        mask[y0 : y1 + 1, x0], mask[y0 : y1 + 1, x1],
    ])
    if per.mean() < 0.85:
        raise VisionError("маджента найдена, но периметр рваный (это не рамка кадра)")
    return x0, y0, w, h


def find_frame_region(img: np.ndarray, pad_frac: float = 0.02) -> tuple[int, int, int, int]:
    """Автопоиск кадра на всём снимке: bbox мадженты + небольшой запас."""
    mask = magenta_mask(img)
    x0, y0, w, h = find_frame_bbox(mask)
    pad = max(3, int(pad_frac * max(w, h)))
    ih, iw = img.shape[:2]
    x = max(0, x0 - pad)
    y = max(0, y0 - pad)
    x1 = min(iw, x0 + w + pad)
    y1 = min(ih, y0 + h + pad)
    return x, y, x1 - x, y1 - y


def _cell_centers(origin: int, n: int, pitch: float, limit: int) -> np.ndarray:
    c = np.floor(origin + (np.arange(n) + 0.5) * pitch).astype(int)
    return np.clip(c, 0, limit - 1)


def _sample_cells(img: np.ndarray, xs: np.ndarray, ys: np.ndarray, pitch: float) -> np.ndarray:
    """Цвета ячеек: центральный пиксель; при крупной ячейке — среднее 4 точек."""
    h, w = len(ys), len(xs)
    if pitch >= 6:
        d = max(1, int(pitch * 0.2))
        acc = np.zeros((h, w, 3), dtype=np.int32)
        for dy, dx in ((-d, -d), (-d, d), (d, -d), (d, d)):
            yy = np.clip(ys + dy, 0, img.shape[0] - 1)
            xx = np.clip(xs + dx, 0, img.shape[1] - 1)
            acc += img[np.ix_(yy, xx)].astype(np.int32)
        return (acc // 4).astype(np.uint8)
    return img[np.ix_(ys, xs)]


def _ring_mask(w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    b = E.BORDER
    m[:b, :] = m[-b:, :] = True
    m[:, :b] = m[:, -b:] = True
    return m


def read_frame(
    img: np.ndarray,
    w: int,
    h: int,
    palette: int,
    min_pitch: float = 2.5,
) -> dict:
    """Один скриншот -> grid (индексы палитры + MAGENTA-рамка) + meta заголовка.

    Возвращает dict из _read_header (index, total, length, crc, palette).
    Бросает VisionError с человеческим объяснением.
    """
    try:
        E.validate_grid(w, h)
        E.validate_palette(palette)
    except ValueError as e:
        raise VisionError(str(e)) from e

    mask = magenta_mask(img)
    bx, by, bw, bh = find_frame_bbox(mask)
    pitch_w, pitch_h = bw / w, bh / h
    if abs(pitch_w - pitch_h) > 0.06 * max(pitch_w, pitch_h):
        raise VisionError(
            f"аспект рамки {bw}x{bh}px не подходит под сетку {w}x{h} "
            f"(шаг {pitch_w:.1f} против {pitch_h:.1f})"
        )
    if pitch_w < min_pitch:
        raise VisionError(
            f"ячейка слишком мелкая: {pitch_w:.1f}px — увеличьте кадр или уменьшите сетку"
        )
    pitch = (pitch_w + pitch_h) / 2

    # кольцо рамки должно быть маджентой в центре своих ячеек, середина — нет
    xs = _cell_centers(bx, w, pitch, img.shape[1])
    ys = _cell_centers(by, h, pitch, img.shape[0])
    sub_mask = mask[np.ix_(ys, xs)]
    ring = _ring_mask(w, h)
    if sub_mask[ring].mean() < 0.8:
        raise VisionError("рамка не покрывает свои ячейки — сдвиньте область или наведите точнее")
    if sub_mask[~ring].mean() > 0.1:
        raise VisionError("маджента внутри кадра — под рамку попало что-то лишнее")

    cells = _sample_cells(img, xs, ys, pitch)
    pal = np.array(E.PALETTES[palette], dtype=np.int32)  # int32: квадраты до 255²
    diff = cells[:, :, None, :].astype(np.int32) - pal[None, None, :, :]
    grid = np.argmin((diff * diff).sum(-1), axis=-1)

    border = ring
    grid[border] = E.MAGENTA
    grid_list = grid.tolist()

    try:
        meta = E._read_header(grid_list)
    except ValueError as e:
        raise VisionError(f"заголовок кадра не прочитан: {e}") from e
    if meta["palette"] != palette:
        raise VisionError(
            f"палитра в кадре — {meta['palette']} цветов, ожидалось {palette}"
        )
    meta["grid"] = grid_list
    return meta
