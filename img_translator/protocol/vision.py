"""Зрение приёмника: скриншот (RGB ndarray) -> grid индексов палитры.

Пайплайн: маска мадженты -> bbox рамки -> определение сетки:
- задана (w, h) — pitch = рамка/W, сверка с заголовком;
- не задана — перебор кандидатов W (16..512) с проверкой аспекта рамки,
  маркеров 7x7 и заголовка (маркеры/шапка ч/б — палитра не нужна).
Палитра читается из заголовка (либо сверяется с ожидаемой). Далее:
сэмплирование центров ячеек -> ближайший цвет палитры -> grid для
protocol.encoder с проверкой CRC.
"""

from __future__ import annotations

import numpy as np
import zlib

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


def _try_config(
    img: np.ndarray,
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    w: int,
    h: int,
    min_pitch: float,
) -> dict:
    """Проверить кандидата (w, h): аспект, покрытие рамкой, маркеры, заголовок.

    Возвращает {w, h, xs, ys, pitch, cells, meta}. Маркеры и шапка читаются
    по яркости — палитра на этом этапе не нужна. Бросает VisionError.
    """
    bx, by, bw, bh = bbox
    pitch_w, pitch_h = bw / w, bh / h
    if abs(pitch_w - pitch_h) > 0.06 * max(pitch_w, pitch_h):
        raise VisionError(
            f"аспект рамки {bw}x{bh}px не подходит под сетку {w}x{h} "
            f"(шаг {pitch_w:.1f} против {pitch_h:.1f})"
        )
    if min(pitch_w, pitch_h) < min_pitch:
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
    lum = cells.astype(np.int32) @ np.array([299, 587, 114], dtype=np.int32)
    bits = (lum < 128000).astype(np.int8)  # 1 = тёмная (чёрная) ячейка
    bits[ring] = E.MAGENTA
    try:
        # внутри: маркеры 7x7, версия, W/H из шапки против факта, палитра
        meta = E._read_header(bits.tolist())
    except ValueError as e:
        raise VisionError(f"маркеры/заголовок: {e}") from e
    return {"w": w, "h": h, "xs": xs, "ys": ys, "pitch": pitch, "cells": cells, "meta": meta}


def detect_config(
    img: np.ndarray,
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    min_pitch: float = 2.5,
) -> dict:
    """Подобрать (w, h) по рамке: кандидаты из аспекта, проверка содержимым.

    Допуск по аспекту нарочно шире полуячейки: bbox рамки может ошибаться
    до ~половины шага (обрезка, нецелый масштаб). Отсев ложных кандидатов
    делают маркеры 7x7 и заголовок, а не аспект.
    """
    bx, by, bw, bh = bbox
    seen: set[tuple[int, int]] = set()
    cands: list[tuple[float, int, int]] = []
    for w in range(16, 513):
        pitch = bw / w
        if pitch < min_pitch * 0.8:  # заведомо мелкая ячейка — дальше только меньше
            break
        fh = bh / pitch
        # оба соседних H: round может уйти в соседнюю ячейку из-за погрешности bbox
        for h in {int(fh), round(fh), int(fh) + 1}:
            if not (16 <= h <= 512) or (w, h) in seen:
                continue
            if abs(fh - h) > 0.75:
                continue  # рамка должна делиться на целое число ячеек (±погрешность bbox)
            seen.add((w, h))
            cands.append((abs(fh - h), w, h))
    if not cands:
        raise VisionError(f"рамка {bw}x{bh}px не делится ни на одну сетку 16..512")
    cands.sort()
    errors: list[str] = []
    for _, w, h in cands:
        try:
            return _try_config(img, mask, bbox, w, h, min_pitch)
        except VisionError as e:
            errors.append(f"{w}x{h}: {e}")
    raise VisionError("сетка не определена; " + "; ".join(errors[:3]))


def _payload_crc_ok(grid: np.ndarray, meta: dict, palette: int) -> bool:
    """CRC payload пере-классифицированной сетки против header-meta."""
    k = palette.bit_length() - 1
    bits: list[int] = []
    for x, y in meta["payload_cells"]:
        bits.extend(E._bits(int(grid[y, x]), k))
    out = bytearray()
    for i in range(meta["length"]):
        out.append(E._to_int(bits[i * 8 : i * 8 + 8]))
    return zlib.crc32(bytes(out)) & 0xFFFFFFFF == meta["crc"]


def measured_palette_from_strip(
    cells: np.ndarray, palette: int
) -> list[tuple[int, int, int]]:
    """Палитра как её реально рисует экран: сэмплы с полосы калибровки.

    Полоса — 2 строки под заголовком, ячейка с номером p несёт палитру
    [p % N] (так её заполняет encoder). Индексы известны, позиции известны —
    никакой кластеризации и сопоставлений. Для индекса без сэмплов
    (крошечная сетка) оставляем встроенный эталон.
    """
    if palette not in E.PALETTES:
        raise VisionError(f"неизвестная палитра: {palette}")
    hh, ww = cells.shape[:2]
    _, _, _, strip = E._layout(ww, hh)
    n = palette
    acc = np.zeros((n, 3), dtype=np.int64)
    cnt = np.zeros(n, dtype=np.int64)
    for pidx, (x, y) in enumerate(strip):
        i = pidx % n
        acc[i] += cells[y, x].astype(np.int64)
        cnt[i] += 1
    builtin = E.PALETTES[palette]
    measured: list[tuple[int, int, int]] = []
    for i in range(n):
        if cnt[i]:
            measured.append(tuple(int(v) for v in (acc[i] / cnt[i]).round()))
        else:
            measured.append(tuple(int(v) for v in builtin[i]))
    return measured


def read_frame(
    img: np.ndarray,
    w: int | None = None,
    h: int | None = None,
    palette: int | None = None,
    min_pitch: float = 2.5,
    palette_override: list[tuple[int, int, int]] | None = None,
    allow_calibrate: bool = True,
) -> dict:
    """Один скриншот -> grid (индексы палитры + MAGENTA-рамка) + meta заголовка.

    w/h/palette не заданы — определяются автоматически (рамка + заголовок).
    Заданы — используются как ожидания для сверки. palette_override —
    измеренные на этом экране цвета (кэш калибровки). allow_calibrate:
    при неудаче CRC payload кластеризовать цвета кадра и повторить
    классификацию по измеренным центроидам. Бросает VisionError.
    """
    if (w is None) != (h is None):
        raise VisionError("задайте сетку целиком (--grid W H) или не задавайте вовсе")
    if palette is not None:
        try:
            E.validate_palette(palette)
        except ValueError as e:
            raise VisionError(str(e)) from e

    mask = magenta_mask(img)
    bbox = find_frame_bbox(mask)
    if w is None:
        cfg = detect_config(img, mask, bbox, min_pitch)
        w, h = cfg["w"], cfg["h"]
    else:
        try:
            E.validate_grid(w, h)
        except ValueError as e:
            raise VisionError(str(e)) from e
        cfg = _try_config(img, mask, bbox, w, h, min_pitch)
        if palette is not None and cfg["meta"]["palette"] != palette:
            raise VisionError(
                f"палитра в кадре — {cfg['meta']['palette']} цветов, ожидалось {palette}"
            )

    meta = dict(cfg["meta"])
    palette_eff = palette if palette is not None else meta["palette"]
    cells = cfg["cells"]

    def classify(pal: np.ndarray) -> np.ndarray:
        diff = cells[:, :, None, :].astype(np.int32) - pal[None, None, :, :]
        grid = np.argmin((diff * diff).sum(-1), axis=-1).astype(np.int8)
        grid[_ring_mask(w, h)] = E.MAGENTA
        return grid

    pal = (
        np.array(palette_override, dtype=np.int32)
        if palette_override is not None
        else np.array(E.PALETTES[palette_eff], dtype=np.int32)
    )
    grid = classify(pal)
    meta["grid"] = grid.tolist()

    if allow_calibrate:
        try:
            E.decode_frame(meta["grid"])  # CRC payload; заголовок уже проверен
        except ValueError:
            # шапку не перечитываем: она прочитана по яркости надёжно.
            # Калибровка: измеряем реальные цвета с полосы кадра
            # (известные индексы палитры на известных местах) и повторяем
            # классификацию payload по ним.
            measured = measured_palette_from_strip(cells, palette_eff)
            grid2 = classify(np.array(measured, dtype=np.int32))
            if not _payload_crc_ok(grid2, meta, palette_eff):
                raise VisionError("CRC не сошёлся, калибровка полосой не спасла")
            meta["grid"] = grid2.tolist()
            meta["calibrated_palette"] = measured

    meta["config"] = {"w": w, "h": h, "palette": palette_eff, "pitch": cfg["pitch"]}
    return meta
