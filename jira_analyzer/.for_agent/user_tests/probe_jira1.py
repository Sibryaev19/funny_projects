# -*- coding: utf-8 -*-
"""PROBE #1: разведка корпоративной Jira перед первым полноценным запуском.

Запуск на рабочей машине (Windows):
    python probe_jira1.py

Зависимости: ТОЛЬКО стандартная библиотека (ничего ставить не надо).

Что проверяет:
  1. PAT-авторизация (Bearer) — работает ли токен
  2. Тип инстанса (Server/Cloud) и версия
  3. Количество задач в проекте + разбивка по годам создания
  4. ID custom field "Epic Link" (нужен для построения дерева epic -> задача)
  5. Доступен ли отдельный эндпоинт /changelog (полная история у урезанных задач)
  6. Какой максимальный page_size сервер реально отдаёт (100/200/500/1000)

Результат — текст в консоль. Пришли его вывод в чат целиком.

Токен и адрес берутся из .env проекта (секция JIRA1_*), если файл найден рядом
(скрипт ищет .env вверх по папкам). Иначе — заполни блок НАСТРОЙКИ ниже.
"""
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

try:  # чтобы русские буквы печатались в консоли Windows
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ==================== НАСТРОЙКИ ====================
PROJECT = "КЛЮЧ_ПРОЕКТА"                      # напр. "PROJ" — заполни обязательно
JIRA_URL = ""                                 # пусто = взять из .env (JIRA1_URL)
PAT = ""                                      # пусто = взять из .env (JIRA1_PAT)
# Если корпоративный сертификат самоподписанный и будет ошибка SSL — поставь True:
INSECURE_SSL = False
# ===================================================


def find_env(start: Path):
    """Ищет .env вверх по дереву папок от места запуска скрипта."""
    for d in [start] + list(start.parents):
        candidate = d / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env_values(path: Path):
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


_env_path = find_env(Path(__file__).resolve().parent)
if _env_path:
    _env = load_env_values(_env_path)
    # первый корпоративный инстанс: префиксы JIRA / JIRA1 (что найдётся)
    JIRA_URL = JIRA_URL or _env.get("JIRA_URL", "") or _env.get("JIRA1_URL", "")
    PAT = PAT or _env.get("JIRA_TOKEN", "") or _env.get("JIRA1_PAT", "")
    print(f".env найден: {_env_path}")
else:
    print(".env не найден — использую блок НАСТРОЙКИ")

if not JIRA_URL or not PAT:
    print("\nОШИБКА: не задан JIRA_URL или PAT.")
    print("Заполни JIRA1_URL и JIRA1_PAT в .env проекта (или блок НАСТРОЙКИ в этом файле).")
    sys.exit(1)

ctx = None
if INSECURE_SSL:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE


def get(path, params=None):
    url = JIRA_URL + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + PAT,
        "Accept": "application/json",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.time() - t0
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return e.code, {"_body": body}, time.time() - t0
    except Exception as e:
        return -1, {"_body": repr(e)}, time.time() - t0


def section(title):
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


section("1. Проверка авторизации")
code, data, _ = get("/rest/api/2/myself")
if code == 200:
    print(f"OK (200): токен работает, пользователь: {data.get('displayName')} ({data.get('name', '')})")
else:
    print(f"ОШИБКА {code}: {data.get('_body', '')}")
    print("Дальнейшие проверки бессмысленны — разберись с токеном/доступом.")
    sys.exit(1)

section("2. Тип и версия инстанса")
code, data, _ = get("/rest/api/2/serverInfo")
if code == 200:
    print(f"Jira {data.get('version')} | тип: {data.get('deploymentType')} | {data.get('baseUrl')}")
else:
    print(f"ОШИБКА {code} (не критично): {data.get('_body', '')}")

section(f"3. Объём проекта {PROJECT}")
code, data, dt = get("/rest/api/2/search",
                     {"jql": f"project = {PROJECT}", "maxResults": 0})
if code != 200:
    print(f"ОШИБКА {code}: {data.get('_body', '')} — проверь ключ проекта")
    sys.exit(1)
total = data.get("total")
print(f"Всего задач: {total} (запрос {dt:.2f}с)")

year = 2012  # от какого года считать разбивку
current = 2026
while year <= current:
    nxt = year + 1
    _, d, _ = get("/rest/api/2/search",
                  {"jql": f'project = {PROJECT} AND created >= "{year}-01-01" '
                          f'AND created < "{nxt}-01-01"', "maxResults": 0})
    t = d.get("total", 0)
    if t:
        print(f"  {year}: {t}")
    year = nxt

section("4. Epic Link и другие кастомные поля")
code, fields, _ = get("/rest/api/2/field")
if code == 200:
    interesting = ("epic", "story point", "sprint")
    for f in fields:
        if not f.get("custom"):
            continue
        name = (f.get("name") or "").lower()
        if any(k in name for k in interesting):
            print(f"  {f['id']} = \"{f['name']}\" (type={f.get('schema', {}).get('type')})")
else:
    print(f"ОШИБКА {code}: {data.get('_body', '')}")

section("5. Эндпоинт /changelog")
code, data, _ = get(f"/rest/api/2/search",
                    {"jql": f"project = {PROJECT} ORDER BY created ASC", "maxResults": 1,
                     "fields": "summary"})
first_key = data.get("issues", [{}])[0].get("key") if code == 200 else None
if first_key:
    code, data, _ = get(f"/rest/api/2/issue/{first_key}/changelog", {"maxResults": 1})
    print(f"тест на {first_key}: HTTP {code} -> "
          + ("эндпоинт ДОСТУПЕН (полная догрузка будет работать)" if code == 200
             else f"недоступен: {str(data.get('_body', ''))[:120]}"))
else:
    print("не удалось получить первую задачу проекта")

section("6. Максимальный page_size")
for size in (100, 200, 500, 1000):
    code, data, dt = get("/rest/api/2/search",
                         {"jql": f"project = {PROJECT}",
                          "maxResults": size, "fields": "summary"})
    got = len(data.get("issues", []))
    limit = data.get("maxResults")
    print(f"  запросили {size}: вернулось {got} задач, maxResults в ответе = {limit} "
          f"({dt:.2f}с)")

print("\n===== ГОТОВО. Пришли этот вывод целиком в чат. =====")
