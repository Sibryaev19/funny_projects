#!/usr/bin/env python3
"""
shit_downloader — качает публичную папку/файл с Яндекс.Диска целиком,
несмотря на отключённое «скачивание архивом» в веб-интерфейсе.

Использование:
    python3 ydisk_dl.py <ссылка_на_публичную_папку_или_файл> [папка_назначения]

Пример:
    python3 ydisk_dl.py https://disk.yandex.ru/d/xxxxxxxx лаба
"""

import argparse
import concurrent.futures as cf
import os
import sys
import time
from urllib.parse import quote

import requests

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

session = requests.Session()
session.headers.update({"User-Agent": UA})

MAX_WORKERS = 5      # параллельных скачиваний
RETRIES = 4


def api_get(url, **params):
    for attempt in range(RETRIES):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"  ! API {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"  ! сеть: {e}")
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API не ответил: {url}")


def sanitize(name):
    return name.replace("/", "_").replace("\\", "_").strip()


def strip_root(rpath, root_path):
    """Приводит путь элемента к пути относительно корня публичной папки."""
    p = rpath.replace("disk:", "", 1).lstrip("/")
    root = root_path.replace("disk:", "", 1).strip("/")
    if root and p.startswith(root + "/"):
        p = p[len(root) + 1:]
    return p


def list_public(public_key, path="", root_path=None):
    """Рекурсивно собирает [(rel_path, href, size)] всех файлов."""
    if root_path is None:
        root_path = path
    files = []
    offset = 0
    limit = 500
    dirs = []
    while True:
        params = {"public_key": public_key, "limit": limit,
                  "offset": offset, "sort": "name"}
        if path:
            params["path"] = path
        data = api_get(API, **params)
        emb = data.get("_embedded", {})
        items = emb.get("items", [])
        # путь текущей папки — база для относительных путей вложенных файлов
        cur = emb.get("path", path)
        for it in items:
            rel = strip_root(it["path"], root_path) or strip_root(cur, root_path)
            if it["type"] == "file":
                files.append((rel, it["file"], it.get("size", 0)))
            elif it["type"] == "dir":
                dirs.append(it["path"])
        total = emb.get("total", 0)
        offset += limit
        if offset >= total:
            break
    for d in dirs:
        files.extend(list_public(public_key, d, root_path))
    return files


def download(dest_root, rel, href, size):
    # санитизируем каждый компонент пути отдельно, чтобы не ломать разделители
    rel = "/".join(sanitize(part) for part in rel.split("/") if part)
    dest = os.path.join(dest_root, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) == size and size > 0:
        print(f"  = уже есть: {rel}")
        return 0
    tmp = dest + ".part"
    for attempt in range(RETRIES):
        try:
            with session.get(href, stream=True, timeout=60) as r:
                r.raise_for_status()
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1024 * 256):
                        f.write(chunk)
                        done += len(chunk)
            os.replace(tmp, dest)
            return done
        except Exception as e:
            print(f"  ! {rel}: {e} (попытка {attempt + 1}/{RETRIES})")
            time.sleep(2 * (attempt + 1))
    print(f"  ✗ НЕ СКАЧАЛОСЬ: {rel}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Качальщик публичных папок Яндекс.Диска")
    ap.add_argument("url", help="ссылка на публичный ресурс")
    ap.add_argument("dest", nargs="?", default="yandex_dump",
                    help="куда качать (по умолчанию ./yandex_dump)")
    args = ap.parse_args()

    print(f"Ссылка: {args.url}")
    meta = api_get(API, public_key=args.url)
    root_name = sanitize(meta.get("name") or "yandex")
    # если качаем одиночный файл
    if meta.get("type") == "file":
        dest_root = os.path.join(args.dest)
        tasks = [(meta["path"], meta["file"], meta.get("size", 0))]
    else:
        dest_root = os.path.join(args.dest, root_name)
        print("Обхожу папку...")
        tasks = list_public(args.url)
        if not tasks:
            sys.exit("Файлов не найдено (или ссылка не публичная).")

    total_bytes = sum(s for _, _, s in tasks)
    print(f"Файлов: {len(tasks)}, объём: {total_bytes / 1024 / 1024:.1f} МБ")
    print(f"Куда: {dest_root}")
    print("Структура:")
    for rel, _, _ in sorted(tasks):
        print(f"  {rel}")
    print()

    done_bytes = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(MAX_WORKERS) as ex:
        futures = {ex.submit(download, dest_root, *t): t for t in tasks}
        for fut in cf.as_completed(futures):
            done_bytes += fut.result()
            pct = done_bytes / total_bytes * 100 if total_bytes else 100
            speed = done_bytes / max(time.time() - t0, 1) / 1024
            print(f"  [{pct:5.1f}%] {speed:.0f} КБ/с", end="\r", flush=True)

    print(f"\nГотово: {len(tasks)} файлов, "
          f"{done_bytes / 1024 / 1024:.1f} МБ за {time.time() - t0:.0f}с -> {dest_root}")


if __name__ == "__main__":
    main()
