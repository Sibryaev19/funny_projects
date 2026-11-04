"""Протокол визуального канала (v3): байты -> сетка ячеек (кадры) -> обратно.

Кадр — сетка W x H ячеек. Значения ячейки:
  0..N-1 — индекс цвета палитры (данные/заголовок), MAGENTA (-1) — рамка.

Палитры (PALETTES, выбираются при кодировании): 2/4/8/16 цветов,
k = log2(N) бит на ячейку payload. Каждая палитра начинается с белого и
чёрного (индексы 0 и 1 — ими же печатаются служебные зоны), все — из
максимально различимых цветов и НЕ содержат мадженту: она зарезервирована
под рамку и данных не переносит. Добавление «игровой» палитры позже =
новый ID в PALETTE_ORDER, формат не меняется.

Служебные зоны (от края к центру):
  - маджента-рамка BORDER ячеек: дешифратор ищет её, чтобы найти кадр
    на экране при произвольном положении и размере окна игры;
  - тихая рамка MARGIN ячеек (белая): отбивает цвет рамки от данных;
  - маркеры-уголки FINDER x FINDER (ч/б кольца, QR-подобные) в трёх углах
    внутренней области: по ним измеряется размер ячейки (pitch = размер
    маркера в экранных пикселях / FINDER) и определяется ориентация;
  - заголовок: строки сразу под верхними маркерами, растровая укладка,
    всегда ч/б (1 бит/ячейка):
      версия протокола       4 бита
      ширина сетки W        12 бит
      высота сетки H        12 бит
      ID палитры             4 бита (индекс в PALETTE_ORDER)
      номер кадра           16 бит
      всего кадров          16 бит
      длина payload в байт. 16 бит
      CRC32 payload         32 бита
  - полезная нагрузка: все оставшиеся ячейки, растровый порядок,
    k бит на ячейку (MSBfirst); хвост добивается нулями (белые ячейки).

Внешнее представление строк кадра (as_dict): символ 'M' — рамка,
иначе hex-цифра — индекс палитры (0-15).
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass

VERSION = 3

BORDER = 2   # толщина маджента-рамки в ячейках
MARGIN = 1   # тихая (белая) рамка внутри мадженты
FINDER = 7   # размер ч/б маркера-уголка в ячейках

MAGENTA = -1     # sentinel рамки в grid
WHITE, BLACK = 0, 1  # индексы палитры для служебных зон

# Палитры данных: максимально различимые цвета, без мадженты (255,0,255).
# palette[0] — всегда белый, palette[1] — всегда чёрный.
PALETTE_ORDER = [2, 4, 8, 16]
PALETTES: dict[int, list[tuple[int, int, int]]] = {
    2: [
        (255, 255, 255), (0, 0, 0),
    ],
    4: [
        (255, 255, 255), (0, 0, 0), (228, 16, 24), (0, 70, 228),
    ],
    8: [
        (255, 255, 255), (0, 0, 0), (228, 16, 24), (0, 70, 228),
        (0, 154, 68), (255, 214, 0), (0, 188, 212), (255, 120, 0),
    ],
    16: [
        (255, 255, 255), (0, 0, 0), (228, 16, 24), (0, 70, 228),
        (0, 154, 68), (255, 214, 0), (0, 188, 212), (255, 120, 0),
        (128, 128, 128), (204, 204, 204), (0, 32, 128), (122, 0, 190),
        (130, 78, 0), (255, 145, 180), (170, 226, 135), (135, 195, 255),
    ],
}

# размеры полей заголовка в битах
F_VERSION, F_W, F_H, F_PALETTE, F_INDEX, F_TOTAL, F_LENGTH, F_CRC = (
    4, 12, 12, 4, 16, 16, 16, 32
)
HEADER_BITS = (
    F_VERSION + F_W + F_H + F_PALETTE + F_INDEX + F_TOTAL + F_LENGTH + F_CRC
)  # 112


@dataclass
class Frame:
    index: int
    total: int
    payload_len: int
    crc: int
    palette: int
    grid: list[list[int]]  # grid[y][x]: 0..N-1 (индекс палитры) или MAGENTA

    def as_dict(self) -> dict:
        rows = []
        for row in self.grid:
            rows.append("".join(
                "M" if v == MAGENTA else format(v, "x") for v in row
            ))
        return {
            "index": self.index,
            "total": self.total,
            "rows": rows,
        }


def _finder_pattern() -> list[list[int]]:
    p = [[WHITE] * FINDER for _ in range(FINDER)]
    for y in range(FINDER):
        for x in range(FINDER):
            on_ring = x in (0, FINDER - 1) or y in (0, FINDER - 1)
            in_core = 2 <= x <= FINDER - 3 and 2 <= y <= FINDER - 3
            p[y][x] = BLACK if (on_ring or in_core) else WHITE
    return p


_FINDER_PATTERN = _finder_pattern()


def _inner(w: int, h: int) -> tuple[int, int, int, int]:
    """Координаты внутренней области: x0, y0, x1, y1 (x1/y1 — исключительно)."""
    off = BORDER + MARGIN
    return off, off, w - off, h - off


def header_rows(w: int) -> int:
    x0, _, x1, _ = _inner(w, 0)
    return math.ceil(HEADER_BITS / (x1 - x0))


def validate_palette(palette: int) -> None:
    if palette not in PALETTE_ORDER:
        raise ValueError(
            f"палитра должна быть одной из {PALETTE_ORDER}, получено {palette}"
        )


def validate_grid(w: int, h: int) -> None:
    if w <= 0 or h <= 0 or w > 0xFFF or h > 0xFFF:
        raise ValueError(f"некорректный размер сетки: {w}x{h}")
    x0, y0, x1, y1 = _inner(w, h)
    inner_w, inner_h = x1 - x0, y1 - y0
    if inner_w < 2 * (MARGIN + FINDER) + 2:
        raise ValueError(f"сетка слишком узкая: ширина {w} недостаточна")
    top_used = MARGIN + FINDER + header_rows(w)
    bottom_used = MARGIN + FINDER
    if inner_h < top_used + bottom_used + 1:
        raise ValueError(f"сетка слишком низкая: высота {h} недостаточна")


def _reserved_cells(w: int, h: int) -> set[tuple[int, int]]:
    """Ячейки, не занятые полезной нагрузкой (рамки, маркеры, заголовок)."""
    reserved = set()
    x0, y0, x1, y1 = _inner(w, h)
    for cx, cy in ((x0, y0), (x1 - FINDER, y0), (x0, y1 - FINDER)):
        for dy in range(FINDER):
            for dx in range(FINDER):
                reserved.add((cx + dx, cy + dy))
    for y in range(y0 + MARGIN + FINDER, y0 + MARGIN + FINDER + header_rows(w)):
        for x in range(x0, x1):
            reserved.add((x, y))
    return reserved


def _bits(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def _to_int(bits: list[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def frame_capacity(w: int, h: int, palette: int = 2) -> int:
    """Сколько байт полезной нагрузки влезает в один кадр."""
    validate_palette(palette)
    validate_grid(w, h)
    payload_cells, _, _ = _layout(w, h)
    k = palette.bit_length() - 1
    return len(payload_cells) * k // 8


def _layout(w: int, h: int) -> tuple[set, list, list]:
    """(payload-ячейки, header-ячейки, позиции трёх маркеров)."""
    reserved = _reserved_cells(w, h)
    x0, y0, x1, y1 = _inner(w, h)
    header_cells = [
        (x, y)
        for y in range(y0 + MARGIN + FINDER, y0 + MARGIN + FINDER + header_rows(w))
        for x in range(x0, x1)
    ]
    payload = [
        (x, y)
        for y in range(y0, y1)
        for x in range(x0, x1)
        if (x, y) not in reserved
    ]
    finders = ((x0, y0), (x1 - FINDER, y0), (x0, y1 - FINDER))
    return payload, header_cells, finders


def encode_frames(
    data: bytes,
    w: int = 128,
    h: int = 96,
    palette: int = 2,
) -> list[Frame]:
    """Режет данные на кадры заданной сетки и палитры."""
    validate_palette(palette)
    validate_grid(w, h)
    cap = frame_capacity(w, h, palette)
    if cap <= 0:
        raise ValueError("сетка слишком мала для полезной нагрузки")
    chunks = [data[i : i + cap] for i in range(0, len(data), cap)] or [b""]
    total = len(chunks)
    if total > 0xFFFF:
        raise ValueError("слишком много кадров: уменьшите данные или увеличьте сетку")

    payload_cells, header_cells, finders = _layout(w, h)
    k = palette.bit_length() - 1
    palette_id = PALETTE_ORDER.index(palette)

    frames = []
    for idx, chunk in enumerate(chunks):
        grid = [[WHITE] * w for _ in range(h)]
        # маджента-рамка
        for x in range(w):
            for y in range(h):
                if x < BORDER or y < BORDER or x >= w - BORDER or y >= h - BORDER:
                    grid[y][x] = MAGENTA
        # маркеры
        for cx, cy in finders:
            for dy in range(FINDER):
                for dx in range(FINDER):
                    grid[cy + dy][cx + dx] = _FINDER_PATTERN[dy][dx]

        crc = zlib.crc32(chunk) & 0xFFFFFFFF
        header = (
            _bits(VERSION, F_VERSION)
            + _bits(w, F_W)
            + _bits(h, F_H)
            + _bits(palette_id, F_PALETTE)
            + _bits(idx, F_INDEX)
            + _bits(total, F_TOTAL)
            + _bits(len(chunk), F_LENGTH)
            + _bits(crc, F_CRC)
        )
        for (x, y), bit in zip(header_cells, header):
            grid[y][x] = bit

        # payload: биты -> группы по k -> индексы палитры
        bitstream: list[int] = []
        for byte in chunk:
            bitstream.extend(_bits(byte, 8))
        values: list[int] = []
        for i in range(0, len(bitstream), k):
            group = bitstream[i : i + k]
            values.append(_to_int(group + [0] * (k - len(group))))
        values.extend([WHITE] * (len(payload_cells) - len(values)))
        for (x, y), v in zip(payload_cells, values):
            grid[y][x] = v

        frames.append(Frame(idx, total, len(chunk), crc, palette, grid))
    return frames


# --- приёмная сторона (круговые тесты и будущий режим 2) ---

def _extract_by_border(grid: list[list[int]]) -> list[list[int]]:
    """Находит маджента-рамку, проверяет её и возвращает кадр как есть
    (рамка по самому краю: bbox рамки = весь кадр)."""
    h, w = len(grid), len(grid[0])
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if grid[y][x] == MAGENTA:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError("маджента-рамка не найдена")
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if (x0, y0) != (0, 0) or (x1, y1) != (w - 1, h - 1):
        raise ValueError("рамка не по краю кадра: неверные границы")
    for y in range(h):
        for x in range(w):
            near_edge = x < BORDER or y < BORDER or x >= w - BORDER or y >= h - BORDER
            if near_edge != (grid[y][x] == MAGENTA):
                raise ValueError("нарушена целостность маджента-рамки")
    return grid


def _read_header(grid: list[list[int]]) -> dict:
    """Читает заголовок, проверяет W/H, палитру и маркеры."""
    h, w = len(grid), len(grid[0])
    payload_cells, header_cells, finders = _layout(w, h)
    for cx, cy in finders:
        for dy in range(FINDER):
            for dx in range(FINDER):
                if grid[cy + dy][cx + dx] != _FINDER_PATTERN[dy][dx]:
                    raise ValueError("маркер-уголок не найден/искажён")

    hb = [grid[y][x] for x, y in header_cells]
    pos = 0

    def take(n: int) -> int:
        nonlocal pos
        v = _to_int(hb[pos : pos + n])
        pos += n
        return v

    version = take(F_VERSION)
    if version != VERSION:
        raise ValueError(f"неизвестная версия протокола: {version}")
    hw = take(F_W)
    hh = take(F_H)
    if (hw, hh) != (w, h):
        raise ValueError(f"размер в заголовке ({hw}x{hh}) не совпал с фактическим ({w}x{h})")
    palette_id = take(F_PALETTE)
    if palette_id >= len(PALETTE_ORDER):
        raise ValueError(f"неизвестный ID палитры: {palette_id}")
    palette = PALETTE_ORDER[palette_id]
    idx = take(F_INDEX)
    total = take(F_TOTAL)
    length = take(F_LENGTH)
    crc = take(F_CRC)
    if not 0 <= length <= frame_capacity(w, h, palette):
        raise ValueError("некорректная длина payload")
    return {
        "index": idx,
        "total": total,
        "length": length,
        "crc": crc,
        "palette": palette,
        "payload_cells": payload_cells,
    }


def decode_frame(grid: list[list[int]]) -> bytes:
    """Восстанавливает payload одного кадра (с проверками рамки и CRC)."""
    grid = _extract_by_border(grid)
    meta = _read_header(grid)
    k = meta["palette"].bit_length() - 1
    n_cells = -(-meta["length"] * 8 // k)  # ceil
    bits: list[int] = []
    for x, y in meta["payload_cells"][:n_cells]:
        bits.extend(_bits(grid[y][x], k))
    out = bytearray()
    for i in range(meta["length"]):
        out.append(_to_int(bits[i * 8 : i * 8 + 8]))
    if zlib.crc32(bytes(out)) & 0xFFFFFFFF != meta["crc"]:
        raise ValueError("CRC не сошёлся")
    return bytes(out)


def decode_frames(grids: list[list[list[int]]]) -> bytes:
    """Собирает данные из кадров (в любом порядке)."""
    parts: dict[int, bytes] = {}
    total = None
    for g in grids:
        meta = _read_header(_extract_by_border(g))
        total = meta["total"]
        parts[meta["index"]] = decode_frame(g)
    if total is None or len(parts) != total:
        raise ValueError(f"не все кадры получены: {len(parts)}/{total}")
    return b"".join(parts[i] for i in range(total))
