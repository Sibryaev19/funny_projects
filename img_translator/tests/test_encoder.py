"""Круговой тест протокола v3: байты -> кадры (палитры) -> сетки -> обратно."""

import copy
import io
import random
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol.encoder import (
    encode_frames,
    decode_frame,
    decode_frames,
    frame_capacity,
    validate_palette,
    validate_grid,
    PALETTES,
    PALETTE_ORDER,
    MAGENTA,
    VERSION,
)

GRIDS = [(32, 32), (128, 96), (256, 192)]


def roundtrip(data: bytes, w: int, h: int, palette: int) -> None:
    frames = encode_frames(data, w, h, palette)
    grids = [f.grid for f in frames]
    assert decode_frames(grids) == data, (
        f"{len(data)} байт, {len(frames)} кадров, палитра {palette}: не совпало"
    )
    cap = frame_capacity(w, h, palette)
    print(f"ok {w}x{h} {palette}цв: {len(data)} байт, {len(frames)} кадров ({cap} б/кадр)")


def make_zip_with_dirs() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dir/sub/file.txt", "привет" * 100)
        zf.writestr("dir/empty.bin", b"")
        zf.writestr("root.dat", bytes(range(256)) * 3)
    return buf.getvalue()


random.seed(42)
for palette in PALETTE_ORDER:
    w, h = (128, 96)
    roundtrip(b"", w, h, palette)
    roundtrip(b"hello", w, h, palette)
    # граница упаковки: длина, не кратная k-битовым группам
    roundtrip(random.randbytes(frame_capacity(w, h, palette) + 1), w, h, palette)
for w, h in GRIDS:
    roundtrip(random.randbytes(7000), w, h, 16)
roundtrip(make_zip_with_dirs(), 128, 96, 8)

# ёмкость растёт вдвое с удвоением палитры
caps = [frame_capacity(128, 96, p) for p in PALETTE_ORDER]
assert caps[0] < caps[1] < caps[2] < caps[3]
assert caps[1] == 2 * caps[0] - (caps[0] // 8 and caps[0] % 4) or caps[1] >= 2 * caps[0] - 1
print("ёмкости:", dict(zip(PALETTE_ORDER, caps)))

# все палитры: начинаются с белого/чёрного, не содержат мадженту
for n, pal in PALETTES.items():
    assert pal[0] == (255, 255, 255) and pal[1] == (0, 0, 0), n
    assert (255, 0, 255) not in pal, n
    assert len(pal) == n
print("палитры корректны (белый/чёрный первыми, мадженты нет)")

# структура кадра v3: рамка маджентой, версия, палитра в заголовке
f = encode_frames(b"test", 64, 48, 4)[0]
assert f.grid[0] == [MAGENTA] * 64 and f.grid[47] == [MAGENTA] * 64
assert f.as_dict()["rows"][0] == "M" * 64
assert f.palette == 4

# порча ячейки в реальных данных -> CRC ловит (значение уходит в другой цвет)
grid = copy.deepcopy(encode_frames(b"some payload", 64, 48, 8)[0].grid)
grid[3][20] = (grid[3][20] + 1) % 8
try:
    decode_frame(grid)
    raise AssertionError("битый payload не пойман")
except ValueError as e:
    assert "CRC" in str(e), e

# порча ячейки в нулевом заполнителе (за длиной payload) — допустима
grid = copy.deepcopy(encode_frames(b"hi", 64, 48, 4)[0].grid)
grid[40][30] = 3
assert decode_frame(grid) == b"hi"

# невалидные палитры отклоняются
for bad in (3, 5, 32, 0, -2):
    try:
        validate_palette(bad)
        raise AssertionError(f"палитра {bad} не отклонена")
    except ValueError:
        pass
# слишком маленькая сетка отклоняется
for bad in [(10, 10), (30, 20)]:
    try:
        validate_grid(*bad)
        raise AssertionError(f"{bad} не отклонена")
    except ValueError:
        pass

assert VERSION == 3
print("все тесты пройдены")
