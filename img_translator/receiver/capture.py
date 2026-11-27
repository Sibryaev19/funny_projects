"""Захват экрана: mss (X11/Windows) или портал ScreenCast+PipeWire (Wayland).

На GNOME-Wayland X11-снимки через mss дают чёрный экран, поэтому бэкенд
`wayland` поднимает портал ScreenCast (один диалог согласия от пользователя)
и читает кадры из потока PipeWire, который гонит системный gst-launch
в jpeg-файлы во временной папке. `auto` сам выбирает рабочий вариант.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from protocol.vision import VisionError


class MSSCapture:
    """Снимок области через X11 (или GDI на Windows)."""

    def __init__(self):
        import mss

        self._mss = mss.MSS()
        self._snaps = Path(tempfile.mkdtemp(prefix="imgtr_snap_"))

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        import mss

        x, y, w, h = region
        try:
            shot = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        except mss.exception.ScreenShotError as e:
            raise VisionError(f"снимок экрана не удался: {e}") from e
        # mss .rgb — это BGRBGR..., несмотря на имя
        bgr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
        img = np.ascontiguousarray(bgr[:, :, ::-1])
        if int(img.max()) == 0:
            raise VisionError(
                "снимок полностью чёрный (Wayland?): X11-захват не видит экран; "
                "попробуйте --capture wayland"
            )
        return img

    def snapshot_path(self) -> str:
        """Полный снимок главного монитора -> путь к png (фон для ножниц)."""
        import PIL.Image

        mon = self._mss.monitors[1] if len(self._mss.monitors) > 1 else self._mss.monitors[0]
        shot = self._mss.grab(mon)
        bgr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
        PIL.Image.fromarray(bgr[:, :, ::-1]).save(self._snaps / "bg.png")
        return str(self._snaps / "bg.png")

    def close(self) -> None:
        for f in self._snaps.glob("*.png"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            self._snaps.rmdir()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class PortalCapture:
    """Кадры с экрана через xdg-desktop-portal (ScreenCast) + PipeWire.

    При open() показывается диалог согласия («поделиться экраном») — один раз.
    gst-launch пишет jpeg-кадры в tmp; grab() читает свежайший и вырезает область.
    """

    def __init__(self, fps: float = 5.0):
        self.fps = max(1.0, float(fps))
        self._dir = Path(tempfile.mkdtemp(prefix="imgtr_pw_"))
        self._proc: subprocess.Popen | None = None
        self._last_idx = -1
        self._last_mtime = 0.0
        self.size: tuple[int, int] | None = None
        self._token_file = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        ) / "img_translator" / "portal_restore_token"

    # --- диалог портала (dbus-next, asyncio) ---

    async def _start_session(self) -> int:
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        NAME = "org.freedesktop.portal.Desktop"
        PATH = "/org/freedesktop/portal/desktop"
        IFACE = "org.freedesktop.portal.ScreenCast"
        bus = await MessageBus().connect()
        sender = bus.unique_name[1:].replace(".", "_")
        counter = iter(range(1, 1000))

        async def call(member: str, signature: str, body: list):
            reply = await bus.call(
                Message(
                    destination=NAME, path=PATH, interface=IFACE,
                    member=member, signature=signature, body=body,
                )
            )
            if reply.message_type == MessageType.ERROR:
                raise VisionError(f"портал {member}: {reply.body[0]}")
            return reply.body

        async def call_with_response(method: str, arg_sig: str, args: list, extra_options: dict):
            """arg_sig/args — аргументы до options; handle_token уходит в options."""
            tok = f"imgtr{next(counter)}"
            req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{tok}"
            fut: asyncio.Future = asyncio.get_event_loop().create_future()

            def on_message(message: Message):
                if (
                    message.message_type == MessageType.SIGNAL
                    and message.interface == "org.freedesktop.portal.Request"
                    and message.member == "Response"
                    and message.path == req_path
                    and not fut.done()
                ):
                    fut.set_result(message.body)

            options = dict(extra_options)
            options["handle_token"] = Variant("s", tok)
            bus.add_message_handler(on_message)
            try:
                await call(
                    method, arg_sig + "a{sv}", [*args, options]
                )
                body = await asyncio.wait_for(asyncio.shield(fut), 180)
            finally:
                bus.remove_message_handler(on_message)
            response, results = body[0], body[1]
            if response != 0:
                what = {"CreateSession": "сессия", "SelectSources": "выбор источника",
                        "Start": "запуск"}[method]
                raise VisionError(f"портал: {what} отклонены (код {response})")
            return results

        def val(v):
            return v.value if isinstance(v, Variant) else v

        # restore_token: после первого согласия диалог больше не показывается
        restore = None
        try:
            restore = self._token_file.read_text().strip() or None
        except OSError:
            pass

        sess_tok = f"imgtr{next(counter)}"
        results = await call_with_response(
            "CreateSession", "", [],
            {"session_handle_token": Variant("s", sess_tok)},
        )
        session_handle = val(results["session_handle"])

        select_opts = {
            "types": Variant("u", 1),        # монитор
            "multiple": Variant("b", False),
            "cursor_mode": Variant("u", 1),  # скрывать курсор
            "persist_mode": Variant("u", 2), # хранить выбор постоянно
        }
        if restore:
            select_opts["restore_token"] = Variant("s", restore)
        await call_with_response("SelectSources", "o", [session_handle], select_opts)
        results = await call_with_response("Start", "os", [session_handle, ""], {})
        if "restore_token" in results:
            token = val(results["restore_token"])
            if token:
                self._token_file.parent.mkdir(parents=True, exist_ok=True)
                self._token_file.write_text(token)
        streams = val(results["streams"])
        node_id, props = streams[0]
        props = {k: val(v) for k, v in (props or {}).items()}
        if "size" in props:
            w, h = props["size"]
            self.size = (int(h), int(w))
        return int(node_id)

    def open(self) -> "PortalCapture":
        print("портал: подтвердите доступ к экрану в диалоге (один раз)...")
        node_id = asyncio.run(self._start_session())
        pipeline = [
            "gst-launch-1.0", "-q",
            "pipewiresrc", f"path={node_id}",
            "!", "videoconvert",
            "!", "videorate",
            "!", f"video/x-raw,framerate={int(self.fps)}/1",
            "!", "pngenc", "compression-level=1",
            "!", "multifilesink", f"location={self._dir}/f%06d.png", "max-files=8",
        ]
        try:
            self._proc = subprocess.Popen(
                pipeline, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except FileNotFoundError as e:
            raise VisionError(f"gst-launch-1.0 не найден: {e}") from e
        # дождаться первого кадра
        t0 = time.time()
        while time.time() - t0 < 30:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode(errors="replace")
                raise VisionError(f"поток PipeWire не запустился: {err.strip()[:300]}")
            if self._latest_file():
                return self
            time.sleep(0.2)
        raise VisionError("поток PipeWire: кадры не приходят (30с)")

    # --- чтение кадров ---

    def _latest_file(self) -> Path | None:
        files = sorted(self._dir.glob("f*.png"))
        return files[-1] if files else None

    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        import PIL.Image

        t0 = time.time()
        while time.time() - t0 < 2.0:
            f = self._latest_file()
            if f and (idx := int(f.stem[1:])) > self._last_idx:
                try:
                    img = np.array(PIL.Image.open(f).convert("RGB"))
                except OSError:  # файл ещё пишется
                    time.sleep(0.02)
                    continue
                self._last_idx = idx
                self.size = img.shape[:2]
                return self._crop(img, region)
            time.sleep(0.02)
        raise VisionError("новые кадры не приходят (поток остановился?)")

    @staticmethod
    def _crop(img: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
        x, y, w, h = region
        ih, iw = img.shape[:2]
        if y + h > ih or x + w > iw:
            raise VisionError(
                f"область {x}+{w}x{y}+{h} выходит за кадр {iw}x{ih} — "
                "выберите область внутри экрана"
            )
        return np.ascontiguousarray(img[y : y + h, x : x + w])

    def snapshot_path(self) -> str:
        """Свежайший кадр потока (весь монитор) — фон для ножниц."""
        t0 = time.time()
        while time.time() - t0 < 5:
            f = self._latest_file()
            if f:
                return str(f)
            time.sleep(0.1)
        raise VisionError("нет кадров потока для снимка-фона")

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for f in self._dir.glob("*.png"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            self._dir.rmdir()
        except OSError:
            pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()


def open_capture(mode: str = "auto", fps: float = 5.0):
    """Выбор бэкенда: auto — wayland-сессия => портал, иначе mss."""
    import sys as _sys

    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) and (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )
    if mode == "wayland":
        if not wayland:
            raise VisionError(
                "--capture wayland доступен только в сессии Wayland (Linux); "
                "на этой системе используется mss"
            )
        return PortalCapture(fps=fps).open()
    if mode == "mss":
        return MSSCapture()
    # auto в wayland-сессии => портал сразу
    if mode == "auto" and wayland:
        return PortalCapture(fps=fps).open()
    # auto на X-сессии/Windows: сначала mss, проверим живость кадром
    cap = MSSCapture()
    try:
        cap.grab((0, 0, 40, 40))
        return cap
    except VisionError:
        cap.close()
        if not _sys.platform.startswith("linux"):
            raise
        print("X11-захват не работает, переключаюсь на портал...")
        return PortalCapture(fps=fps).open()
