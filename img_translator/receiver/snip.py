"""Выбор области экрана «ножницами».

Основной режим: фоном кладётся реальный снимок экрана (кадр из потока
захвата) — видно, что выбираешь, независимо от того, как композитор
обращается с прозрачностью tk-окна. Координаты пересчитываются из окна
в координаты потока (учитывается масштаб изображения).
"""

from __future__ import annotations

import tkinter as tk


def select_region(
    background_path: str | None = None,
    title: str = "Растяните рамку на кадре (Esc — отмена)",
) -> tuple[int, int, int, int] | None:
    result: dict = {"region": None}
    root = tk.Tk()
    root.title("Выбор области")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="black", cursor="crosshair")

    canvas = tk.Canvas(root, highlightthickness=0, bg="grey20")
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()
    root.update()

    img = None
    photo = None
    if background_path:
        import PIL.Image
        import PIL.ImageTk

        sw, sh = canvas.winfo_width(), canvas.winfo_height()
        if sw < 10:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        img = PIL.Image.open(background_path).convert("RGB")
        img = img.resize((sw, sh), PIL.Image.LANCZOS)
        photo = PIL.ImageTk.PhotoImage(img, master=root)
        canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.create_text(
        (canvas.winfo_width() or 800) // 2, 30,
        text=title, fill="#ff5cff", font=("sans", 16, "bold"),
    )

    state: dict = {"x": None, "y": None, "rect": None}

    def press(event):
        state["x"], state["y"] = event.x, event.y
        if state["rect"]:
            canvas.delete(state["rect"])

    def motion(event):
        if state["x"] is None:
            return
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            state["x"], state["y"], event.x, event.y,
            outline="#f0f", width=3,
        )
        w, h = abs(event.x - state["x"]), abs(event.y - state["y"])
        canvas.delete("size")
        canvas.create_text(
            (state["x"] + event.x) // 2, min(state["y"], event.y) - 12,
            text=f"{w}x{h}", fill="#f0f", tag="size", font=("monospace", 11),
        )

    def release(event):
        if state["x"] is None:
            return
        x0, y0 = state["x"], state["y"]
        w, h = abs(event.x - x0), abs(event.y - y0)
        if w >= 16 and h >= 16:
            cx, cy = min(x0, event.x), min(y0, event.y)
            if img:
                # координаты окна -> координаты снимка (масштаб)
                iw, ih = img.size
                cw, ch = canvas.winfo_width(), canvas.winfo_height()
                sx, sy = iw / cw, ih / ch
                result["region"] = (
                    round(cx * sx), round(cy * sy), round(w * sx), round(h * sy)
                )
            else:
                # без фона: окно на весь экран, считаем координаты экранными
                result["region"] = (
                    root.winfo_rootx() + cx, root.winfo_rooty() + cy, w, h
                )
        root.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", motion)
    canvas.bind("<ButtonRelease-1>", release)
    root.bind("<Escape>", lambda e: root.destroy())
    root.focus_force()
    root.mainloop()
    return result["region"]
