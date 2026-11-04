"""Передатчик-имитатор: FastAPI + системный выбор файлов + кодирование в кадры."""

from __future__ import annotations

import io
import os
import queue as _queue
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from protocol.encoder import (
    VERSION,
    PALETTES,
    PALETTE_ORDER,
    encode_frames,
    frame_capacity,
)
from protocol.envconfig import parse_env as _parse_env

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_GRID = (128, 96)
DEFAULT_PALETTE = 2
MAX_BYTES = 20 * 1024 * 1024  # щит на случай случайного выбора огромной папки

# Файл с дефолтами интерфейса (KEY=VALUE). Смена имени — только здесь.
ENV_FILE = ".env"

# Встроенные дефолты (применяются, когда .env нет или значение в нём кривое).
DEFAULTS = {
    "GRID_W": 128,
    "GRID_H": 96,
    "CELL_PX": 10,
    "COLORS": 2,
    "DELAY_MS": 500,
    "LOOP": True,
    "DEFAULT_FOLDER": "",
}

_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def _env_int(raw: dict[str, str], key: str) -> int:
    try:
        return int(raw[key])
    except (KeyError, ValueError):
        return DEFAULTS[key]


def _env_bool(raw: dict[str, str], key: str) -> bool:
    word = raw.get(key, "").strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return DEFAULTS[key]


def load_ui_defaults() -> dict:
    """Эффективные дефолты интерфейса: .env поверх встроенных значений."""
    raw = _parse_env(PROJECT_ROOT / ENV_FILE)
    folder = raw.get("DEFAULT_FOLDER", "").strip()
    if folder:
        path = Path(os.path.expanduser(folder))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            folder = ""
        else:
            folder = str(path)
    else:
        folder = ""
    w = _env_int(raw, "GRID_W")
    h = _env_int(raw, "GRID_H")
    if not 16 <= w <= 512:
        w = DEFAULTS["GRID_W"]
    if not 16 <= h <= 512:
        h = DEFAULTS["GRID_H"]
    cell = _env_int(raw, "CELL_PX")
    if not 2 <= cell <= 32:
        cell = DEFAULTS["CELL_PX"]
    colors = _env_int(raw, "COLORS")
    if colors not in PALETTE_ORDER:
        colors = DEFAULTS["COLORS"]
    delay = _env_int(raw, "DELAY_MS")
    if delay < 50:
        delay = DEFAULTS["DELAY_MS"]
    return {
        "grid_w": w,
        "grid_h": h,
        "cell_px": cell,
        "colors": colors,
        "delay_ms": delay,
        "loop": _env_bool(raw, "LOOP"),
        "default_folder": folder or None,
    }


app = FastAPI(title="img_translator transmitter")


class TransmitRequest(BaseModel):
    paths: list[str]
    grid: list[int] | None = None   # [w, h] ячеек
    palette: int | None = None      # число цветов: 2/4/8/16


def build_zip(paths: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            path = Path(os.path.expanduser(p))
            if not path.exists():
                raise HTTPException(400, f"путь не найден: {p}")
            if path.is_file():
                zf.write(path, path.name)
            elif path.is_dir():
                for f in sorted(path.rglob("*")):
                    if f.is_file():
                        zf.write(f, path.name / f.relative_to(path))
            else:
                raise HTTPException(400, f"не файл и не папка: {p}")
    return buf.getvalue()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/pick")
def pick() -> dict:
    import multiprocessing as mp

    from server.picker import _dialog_worker

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_dialog_worker, args=(queue,))
    proc.daemon = True  # сервер умер — окно выбора не остаётся висеть
    proc.start()
    result: dict = {"paths": [], "error": None}
    try:
        result = queue.get(timeout=3600)  # окно может висеть открытым долго
    except _queue.Empty:  # таймаут/сбой диалога — отдаём пустой список
        pass
    proc.join(timeout=5)
    if proc.is_alive():
        proc.terminate()
    if result.get("error"):
        raise HTTPException(500, f"окно выбора не открылось: {result['error']}")
    return {"paths": result.get("paths", [])}


def resolve_grid(grid: list[int] | None) -> tuple[int, int]:
    if grid is None:
        return DEFAULT_GRID
    if len(grid) != 2:
        raise HTTPException(400, "grid должен быть [ширина, высота]")
    w, h = grid
    if not (16 <= w <= 512 and 16 <= h <= 512):
        raise HTTPException(400, "сетка должна быть в пределах 16..512 по каждой стороне")
    return w, h


def resolve_palette(palette: int | None) -> int:
    if palette is None:
        return DEFAULT_PALETTE
    if palette not in PALETTE_ORDER:
        raise HTTPException(400, f"палитра должна быть одной из {PALETTE_ORDER}")
    return palette


@app.post("/api/transmit")
def transmit(req: TransmitRequest) -> dict:
    if not req.paths:
        raise HTTPException(400, "список путей пуст")
    w, h = resolve_grid(req.grid)
    palette = resolve_palette(req.palette)
    data = build_zip(req.paths)
    if len(data) > MAX_BYTES:
        raise HTTPException(400, f"архив слишком большой: {len(data)} байт")
    frames = encode_frames(data, w, h, palette)
    return {
        "version": VERSION,
        "grid": [w, h],
        "palette": palette,
        "zip_size": len(data),
        "frames": [f.as_dict() for f in frames],
    }


@app.get("/api/capacity")
def capacity(
    w: int = Query(...),
    h: int = Query(...),
    palette: int = Query(DEFAULT_PALETTE),
) -> dict:
    try:
        return {
            "w": w,
            "h": h,
            "palette": palette,
            "bytes_per_frame": frame_capacity(w, h, palette),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/palettes")
def palettes() -> dict:
    return {str(n): PALETTES[n] for n in PALETTE_ORDER}


@app.get("/api/config")
def ui_config() -> dict:
    return load_ui_defaults()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
