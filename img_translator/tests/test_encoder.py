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

# --- потоковое кодирование: encode_at/build_frame ≡ encode_frames ---
# и чанкование, безопасное для 16-битного поля длины (сетки >64 КБ/кадр)
from protocol.encoder import (
    build_frame,
    encode_at,
    frame_count,
    payload_chunk_size,
)

random.seed(7)
for (w, h, p) in [(128, 96, 2), (64, 48, 4), (500, 250, 16), (512, 384, 16), (512, 512, 16)]:
    cap = payload_chunk_size(w, h, p)
    data = random.randbytes(cap * 3 + 123)   # 4 кадра, последний — обрезан
    frames = encode_frames(data, w, h, p)
    assert frame_count(len(data), w, h, p) == len(frames) == 4
    assert all(f.payload_len <= 0xFFFF for f in frames), "payload длиннее 16-битного поля"
    for i, f in enumerate(frames):
        assert encode_at(data, i, w, h, p).as_dict() == f.as_dict()
        assert build_frame(f.payload_len and data[i * cap:(i + 1) * cap] or b"",
                           i, len(frames), w, h, p).as_dict() == f.as_dict()
    # сборка из кусков (как теперь работает приёмник) == decode_frames
    parts = [decode_frame(f.grid) for f in frames]
    assert b"".join(parts) == decode_frames([f.grid for f in frames]) == data
    print(f"ok поток: {w}x{h}x{p}, {len(frames)} кадров, чанк {cap}")

# сетки, где ёмкость кадра > 0xFFFF: раньше длина молча обрезалась, теперь режем данные
assert frame_capacity(512, 384, 16) > 0xFFFF
assert payload_chunk_size(512, 384, 16) == 0xFFFF
# а при ёмкости <= 0xFFFF чанк не меняется (обратная совместимость настроек)
assert payload_chunk_size(500, 250, 16) == frame_capacity(500, 250, 16)
# пустые данные — ровно один кадр, в encode_at и encode_frames одинаково
assert frame_count(0, 128, 96, 2) == 1
assert encode_at(b"", 0, 128, 96, 2).as_dict() == encode_frames(b"", 128, 96, 2)[0].as_dict()
try:
    encode_at(b"abc", 5, 128, 96, 2)
    raise AssertionError("индекс вне диапазона не отклонён")
except ValueError:
    pass

print("все тесты пройдены")
