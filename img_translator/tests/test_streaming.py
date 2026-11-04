"""Стриминговая отдача кадров: сессии на диске + /api/frame.

Эндпоинты вызываются как обычные функции (без HTTP-слоя): кадры
с эндпоинта сравниваются с encode_frames, сборка — бит-в-байт
относительно zip-файла сессии на диске.
"""

import random
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

import server.app as app
from protocol.encoder import (
    MAGENTA,
    decode_frame,
    encode_frames,
)

W, H, PALETTE = 500, 250, 16


def rows_to_grid(rows: list[str]) -> list[list[int]]:
    return [[MAGENTA if ch == "M" else int(ch, 16) for ch in row] for row in rows]


def expect_http(fn, status: int):
    try:
        fn()
        raise AssertionError(f"ожидалась HTTPException {status}")
    except HTTPException as e:
        assert e.status_code == status, e.status_code


def main() -> None:
    rng = random.Random(5)
    src = Path(tempfile.mkdtemp(prefix="it_stream_"))
    payload = rng.randbytes(120_000)
    (src / "data.bin").write_bytes(payload)

    meta = app.transmit(app.TransmitRequest(paths=[str(src / "data.bin")], grid=[W, H], palette=PALETTE))
    assert meta["grid"] == [W, H] and meta["palette"] == PALETTE
    assert meta["total"] >= 3, "мало кадров для проверки"
    sid = meta["sid"]
    session = app._sessions[sid]
    zip_bytes = session["path"].read_bytes()
    assert zipfile.ZipFile(session["path"]).testzip() is None
    with zipfile.ZipFile(session["path"]) as zf:
        assert zf.read("data.bin") == payload

    frames = encode_frames(zip_bytes, W, H, PALETTE)
    assert len(frames) == meta["total"]
    parts = []
    for i in range(meta["total"]):
        got = app.frame(sid=sid, i=i)
        assert got["rows"] == frames[i].as_dict()["rows"], f"кадр {i} не совпал с encode_frames"
        parts.append(decode_frame(rows_to_grid(got["rows"])))
    assert b"".join(parts) == zip_bytes, "сборка кадров != zip сессии"
    print(f"ok: {meta['total']} кадров по запросу, {meta['zip_size']} байт, бит-в-байт")

    # индекс вне диапазона и протухшая/чужая сессия отклоняются
    expect_http(lambda: app.frame(sid=sid, i=meta["total"]), 400)
    expect_http(lambda: app.frame(sid="0" * 16, i=0), 410)
    session["created"] -= app.SESSION_TTL + 1
    expect_http(lambda: app.frame(sid=sid, i=0), 410)
    app._cleanup_sessions()
    assert sid not in app._sessions and not session["path"].exists(), "протухший файл не стёрт"
    print("ok: границы и TTL сессий")

    # новая передача — новая сессия, старая жива, пока не протухла
    m1 = app.transmit(app.TransmitRequest(paths=[str(src)], grid=[W, H], palette=PALETTE))
    m2 = app.transmit(app.TransmitRequest(paths=[str(src)], grid=[W, H], palette=PALETTE))
    assert m1["sid"] != m2["sid"]
    app._cleanup_sessions()
    assert app._sessions[m1["sid"]]["path"].exists()
    for m in (m1, m2):  # чистим за собой
        app._sessions[m["sid"]]["path"].unlink(missing_ok=True)
        del app._sessions[m["sid"]]
    print("ok: сосуществование сессий")

    print("СТРИМИНГ ПРОЙДЕН")


if __name__ == "__main__":
    main()
