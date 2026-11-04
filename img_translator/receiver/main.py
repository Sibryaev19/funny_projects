"""CLI приёмника: снимает область экрана, собирает кадры, распаковывает zip.

Пример:
    python -m receiver.main --grid 128 96 --colors 16 --fps 4
Без --region запустятся «ножницы»; без --out результат ляжет в
OUT_DIR/<время>/ (OUT_DIR из .env, по умолчанию received/).
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

from protocol import encoder as E
from protocol.envconfig import load_env
from protocol.vision import VisionError, read_frame, find_frame_region
from receiver.capture import open_capture
from receiver.snip import select_region

REGION_HINT = "x,y,w,h — например 100,100,640,480"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="receiver",
        description="Приёмник визуального канала img_translator",
    )
    p.add_argument("--grid", nargs=2, type=int, default=[128, 96], metavar=("W", "H"),
                   help="сетка кадра в ячейках (по умолчанию 128 96)")
    p.add_argument("--colors", type=int, default=16, choices=E.PALETTE_ORDER,
                   help="число цветов палитры (по умолчанию 16)")
    p.add_argument("--fps", type=float, default=4.0,
                   help="снимков в секунду (по умолчанию 4)")
    p.add_argument("--capture", choices=["auto", "mss", "wayland"], default="auto",
                   help="способ захвата экрана (auto: wayland-сессия => портал)")
    p.add_argument("--debug", action="store_true",
                   help="сохранять неудачные снимки и статистику в /tmp/imgtr_debug")
    p.add_argument("--region", type=str, default=None, metavar=REGION_HINT,
                   help="область экрана; без флага запустятся ножницы")
    p.add_argument("--out", type=str, default=None,
                   help="папка результата; по умолчанию OUT_DIR из .env (или received/) + <дата-время>/")
    p.add_argument("--timeout", type=float, default=0,
                   help="секунд до сдачи; 0 = без лимита, пока не соберёт всё "
                        "(прервать Ctrl+C); по умолчанию 0")
    return p.parse_args(argv)


def parse_region(s: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(v) for v in s.split(","))
        if w <= 0 or h <= 0:
            raise ValueError
        return x, y, w, h
    except ValueError:
        raise SystemExit(f"--region: ожидалось {REGION_HINT}, получено {s!r}")


def grab_rgb(sct, region: tuple[int, int, int, int]) -> np.ndarray:
    """Снимок области -> HxWx3 RGB (mss отдаёт BGR)."""
    x, y, w, h = region
    shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
    bgr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
    return np.ascontiguousarray(bgr[:, :, ::-1])


def _dump_debug(debug_dir: Path, img: np.ndarray, w: int, h: int, palette: int, err: Exception) -> None:
    """Сохранить снимок и статистику путаницы цветов для разбора."""
    import PIL.Image

    from protocol.vision import _cell_centers, _sample_cells, magenta_mask, find_frame_bbox

    debug_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(debug_dir.glob("fail*.png"))) + 1
    PIL.Image.fromarray(img).save(debug_dir / f"fail{n}.png")
    stats: dict = {}
    try:
        x0, y0, bw, bh = find_frame_bbox(magenta_mask(img))
        xs = _cell_centers(x0, w, bw / w, img.shape[1])
        ys = _cell_centers(y0, h, bh / h, img.shape[0])
        cells = _sample_cells(img, xs, ys, bw / w)
        pal = np.array(E.PALETTES[palette], np.int32)
        for row in cells:
            for c in row.astype(np.int32):
                d = ((pal - c) ** 2).sum(1)
                i, j = np.argsort(d)[:2]
                key = (int(i), round(float(d[j] - d[i])))
                stats[key] = stats.get(key, 0) + 1
    except Exception as e:  # статистика — не причина падать
        (debug_dir / "stats_error.txt").write_text(str(e))
    lines = [f"{k[0]}: запас {k[1]} — {v} ячеек" for k, v in
             sorted(stats.items(), key=lambda kv: kv[0][1])[:20]]
    (debug_dir / f"fail{n}.txt").write_text(
        f"ошибка: {err}\nпалитра {palette}, сетка {w}x{h}\n"
        "ближайший цвет: запас до второго — число ячеек\n" + "\n".join(lines)
    )


def receive(
    region: tuple[int, int, int, int],
    grid: tuple[int, int],
    palette: int,
    fps: float,
    timeout: float,
    on_progress=None,
    capture=None,
    debug_dir: Path | None = None,
) -> bytes:
    """Цикл приёма: возвращает склеенные данные всех кадров.

    on_progress(got, total, note) вызывается при изменении прогресса.
    capture: объект с grab(region)->RGB ndarray и close(); по умолчанию
    выбирается автоматически (mss или портал).
    """
    w, h = grid
    period = 1.0 / max(fps, 0.1)
    grids: dict[int, list] = {}
    total = None
    misses = 0
    start = time.time()
    if capture is None:
        capture = open_capture("auto", fps=fps)
    try:
        while True:
            if timeout > 0 and time.time() - start > timeout:
                break
            t0 = time.time()
            img = capture.grab(region)
            try:
                meta = read_frame(img, w, h, palette)
                idx, total = meta["index"], meta["total"]
                if idx not in grids:
                    E.decode_frame(meta["grid"])  # CRC кадра
                    grids[idx] = meta["grid"]
                    if on_progress:
                        on_progress(len(grids), total, "")
                    misses = 0
            except (VisionError, ValueError) as e:
                misses += 1
                if on_progress and misses % max(1, int(fps)) == 0:
                    on_progress(len(grids), total or 0, str(e))
                if debug_dir is not None and misses <= 3:
                    _dump_debug(debug_dir, img, w, h, palette, e)
            if total is not None and len(grids) >= total:
                return E.decode_frames(list(grids.values()))
            time.sleep(max(0.0, period - (time.time() - t0)))
    finally:
        if capture is not None and hasattr(capture, "close"):
            capture.close()
    raise TimeoutError(
        f"за {timeout:.0f}с собрано {len(grids)} из {total or '?'} кадров"
    )

def unpack(data: bytes, out_arg: str | None) -> Path:
    """Проверяет zip и распаковывает; возвращает папку результата."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"собранные данные ({len(data)} байт) не являются валидным zip: {e}") from e
    bad = zf.testzip()
    if bad is not None:
        raise ValueError(f"архив повреждён (CRC не сошёлся в {bad})")
    if out_arg:
        out = Path(out_arg)
    else:
        # база из .env (OUT_DIR), иначе received/; внутри — папка с датой-временем
        base = load_env().get("OUT_DIR", "").strip() or "received"
        out = Path(os.path.expanduser(base)) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    zf.extractall(out)
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    w, h = args.grid
    try:
        E.validate_grid(w, h)
    except ValueError as e:
        raise SystemExit(f"--grid: {e}")

    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        print("note: Wayland-сессия — захват через портал (появится диалог согласия).")

    cap = open_capture(args.capture, fps=args.fps)
    if args.region:
        region = parse_region(args.region)
    else:
        # автопоиск: рамку ищем на всём кадре потока (передатчик должен работать)
        import PIL.Image

        snap_path = cap.snapshot_path()
        snap = np.array(PIL.Image.open(snap_path).convert("RGB"))
        try:
            region = find_frame_region(snap)
            print(f"рамка найдена автоматически: {region[0]},{region[1]} "
                  f"{region[2]}x{region[3]}px из кадра {snap.shape[1]}x{snap.shape[0]}")
        except VisionError as e:
            print(f"автопоиск рамки не удался ({e}); выберите область мышью")
            region = select_region(background_path=snap_path)
    if region is None:
        print("область не выбрана — выход")
        cap.close()
        return 1
    print(f"область: {region[0]},{region[1]} {region[2]}x{region[3]}px | "
          f"сетка {w}x{h} | цветов {args.colors} | fps {args.fps:g}")

    def progress(got: int, total: int, note: str):
        line = f"\rкадров: {got}/{total or '?'}, осталось: {max(0, (total or 0) - got)}"
        if note:
            line += f"  [{note}]"
        sys.stdout.write(line + " " * 8)
        sys.stdout.flush()

    try:
        data = receive(region, (w, h), args.colors, args.fps, args.timeout,
                       progress, capture=cap,
                       debug_dir=Path("/tmp/imgtr_debug") if args.debug else None)
    except KeyboardInterrupt:
        print("\nостановлено пользователем")
        return 130
    except (TimeoutError, VisionError) as e:
        print(f"\nприём не удался: {e}")
        return 1
    print(f"\nданные собраны: {len(data)} байт, проверяю zip...")

    try:
        out = unpack(data, args.out)
    except ValueError as e:
        print(f"ошибка: {e}")
        return 1
    files = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"готово: {files} файлов -> {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
