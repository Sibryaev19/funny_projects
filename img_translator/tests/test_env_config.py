"""Дефолты интерфейса из .env: парсер, валидация, /api/config."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server.app as app_mod

TMP_ENV = Path(__file__).parent.parent / ".env_test_tmp"


def write_env(text: str) -> None:
    TMP_ENV.write_text(text, encoding="utf-8")
    app_mod.ENV_FILE = TMP_ENV.name


def cleanup() -> None:
    app_mod.ENV_FILE = ".env"
    TMP_ENV.unlink(missing_ok=True)


def main() -> None:
    try:
        # 1. нет файла — встроенные дефолты
        app_mod.ENV_FILE = "__нет_такого__.env"
        cfg = app_mod.load_ui_defaults()
        assert cfg == {
            "grid_w": 128, "grid_h": 96, "cell_px": 10, "colors": 2,
            "delay_ms": 500, "loop": True, "default_folder": None,
        }, cfg
        print("ok: без .env — встроенные дефолты")

        # 2. полный валидный .env
        write_env(
            "# комментарий\n"
            "GRID_W=192\n"
            "GRID_H=144\n"
            "CELL_PX=8\n"
            "COLORS=16\n"
            "DELAY_MS=400\n"
            "LOOP=false\n"
            'DEFAULT_FOLDER="tests"\n'
        )
        cfg = app_mod.load_ui_defaults()
        assert cfg == {
            "grid_w": 192, "grid_h": 144, "cell_px": 8, "colors": 16,
            "delay_ms": 400, "loop": False,
            "default_folder": str(Path(__file__).parent.parent / "tests"),
        }, cfg
        print("ok: полный валидный .env применён")

        # 3. кривые значения откатываются на дефолты
        write_env(
            "GRID_W=abc\nGRID_H=99999\nCELL_PX=1\nCOLORS=3\nDELAY_MS=-5\nLOOP=может\n"
        )
        cfg = app_mod.load_ui_defaults()
        assert cfg["grid_w"] == 128 and cfg["grid_h"] == 96, cfg
        assert cfg["cell_px"] == 10 and cfg["colors"] == 2, cfg
        assert cfg["delay_ms"] == 500 and cfg["loop"] is True, cfg
        print("ok: кривые значения → встроенные дефолты")

        # 4. несуществующая папка → default_folder=None
        write_env("DEFAULT_FOLDER=no/such/dir/here\n")
        cfg = app_mod.load_ui_defaults()
        assert cfg["default_folder"] is None, cfg
        print("ok: несуществующая папка → ничего не выбрано")

        # 5. парсер: кавычки, пробелы, строки без '='
        data = app_mod._parse_env(Path(__file__).parent / "__нет__.env")
        assert data == {}, data
        with tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        ) as f:
            f.write("  A = 1 \nB='два'\nC=\"три\"\nбез равно\n=пустый ключ\n\n")
            tmp = Path(f.name)
        try:
            data = app_mod._parse_env(tmp)
            assert data == {"A": "1", "B": "два", "C": "три"}, data
        finally:
            tmp.unlink()
        print("ok: парсер кавычки/пробелы/мусор")

        # 6. приёмник: unpack кладёт результат в OUT_DIR/<дата-время>/
        import io as _io
        import zipfile as _zipfile

        import protocol.envconfig as envconfig
        import receiver.main as receiver_main

        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "привет")
        tmp_env = TMP_ENV.with_name(".env_test_out")
        tmp_env.write_text("OUT_DIR=.for_agent/test/env_out\n", encoding="utf-8")
        envconfig.ENV_PATH = tmp_env
        try:
            out = receiver_main.unpack(buf.getvalue(), None)
            assert out.parent == Path(".for_agent/test/env_out"), out
            assert (out / "a.txt").read_text(encoding="utf-8") == "привет"
            print(f"ok: unpack → OUT_DIR/<дата>: {out}")
        finally:
            envconfig.ENV_PATH = envconfig.PROJECT_ROOT / ".env"
            tmp_env.unlink(missing_ok=True)
            import shutil as _shutil
            _shutil.rmtree(Path(".for_agent/test/env_out"), ignore_errors=True)

        # 7. приёмник: apply_env_defaults — только fps (сетка/палитра — auto)
        import receiver.main as receiver_main

        tmp_recv = TMP_ENV.with_name(".env_test_recv")
        tmp_recv.write_text("FPS=6\nGRID_W=200\nCOLORS=8\n", encoding="utf-8")
        envconfig.ENV_PATH = tmp_recv
        try:
            args = receiver_main.parse_args([])
            receiver_main.apply_env_defaults(args)
            assert args.fps == 6.0 and args.grid is None and args.colors is None, args
            args = receiver_main.parse_args(["--fps", "3"])
            receiver_main.apply_env_defaults(args)
            assert args.fps == 3.0, args
            tmp_recv.write_text("FPS=много\n", encoding="utf-8")
            args = receiver_main.parse_args([])
            receiver_main.apply_env_defaults(args)
            assert args.fps == 4.0, args
            print("ok: apply_env_defaults (fps из .env, CLI побеждает, кривое → 4.0)")
        finally:
            envconfig.ENV_PATH = envconfig.PROJECT_ROOT / ".env"
            tmp_recv.unlink(missing_ok=True)

        # 8. кэш калиброванной палитры: set_env_key + формат CAL_PALETTE
        cal = [(i * 16 % 256, i * 8 % 256, i * 4 % 256) for i in range(16)]
        tmp_cal = TMP_ENV.with_name(".env_test_cal")
        tmp_cal.write_text("GRID_W=192\n", encoding="utf-8")
        envconfig.set_env_key(tmp_cal, "CAL_PALETTE",
                              ";".join(f"({r},{g},{b})" for r, g, b in cal))
        loaded = envconfig.load_env(tmp_cal)
        assert loaded["GRID_W"] == "192", loaded  # остальное сохранилось
        parsed = [tuple(int(x) for x in p.strip("()").split(","))
                  for p in loaded["CAL_PALETTE"].split(";")]
        assert list(parsed) == cal, parsed
        envconfig.set_env_key(tmp_cal, "CAL_PALETTE", "(1,2,3)")  # замена, не дубль
        assert envconfig.load_env(tmp_cal)["CAL_PALETTE"] == "(1,2,3)"
        tmp_cal.unlink(missing_ok=True)
        print("ok: set_env_key / CAL_PALETTE (запись, замена, формат)")

        print("ВСЕ ТЕСТЫ ENV-ДЕФОЛТОВ ПРОЙДЕНЫ")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
