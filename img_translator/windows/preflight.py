"""Проверка готовности Windows-машины к приёму кадров.

Импорты, тестовый снимок экрана, сведения о мониторах, вердикт.
Выходной код 0 = всё готово, 1 = есть проблемы.
"""
from __future__ import annotations

import sys
from pathlib import Path

SHOT_PATH = Path(__file__).parent / "preflight_shot.png"

ok = True


def check(name: str, fn) -> None:
    global ok
    try:
        fn()
        print(f"ok: {name}")
    except Exception as e:  # noqa: BLE001 — любая поломка = вердикт "проблема"
        ok = False
        print(f"ПРОБЛЕМА: {name}: {e}")


def check_imports() -> None:
    __import__("numpy")
    __import__("PIL.Image")
    __import__("mss")
    __import__("tkinter")


def check_capture() -> None:
    import mss
    import numpy as np
    from PIL import Image

    with mss.MSS() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(mon)
        img = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
        if int(img.max()) == 0:
            raise RuntimeError("снимок полностью чёрный — захват экрана запрещён/сломан")
        Image.fromarray(img[:, :, ::-1]).save(SHOT_PATH)
        print(f"   primary-монитор: {shot.width}x{shot.height}, всего мониторов: {len(sct.monitors) - 1}")
        print(f"   тестовый снимок: {SHOT_PATH} (гляньте глазами)")


def main() -> int:
    print("=== Preflight img_translator ===")
    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    check("импорты (numpy, Pillow, mss, tkinter)", check_imports)
    check("захват экрана (mss)", check_capture)
    print()
    if ok:
        print("ВСЁ ГОТОВО — можно запускать start_transmitter.bat и start_receiver.bat")
    else:
        print("ЕСТЬ ПРОБЛЕМЫ — смотри сообщения выше")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
