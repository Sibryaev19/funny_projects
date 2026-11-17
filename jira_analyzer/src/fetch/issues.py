"""Выгрузка задач проекта: JQL + пагинация search-эндпоинта.

Потоковая модель: генератор выдаёт задачи по мере прихода страниц,
потребитель (run.py) пишет их в issues.jsonl — память не растёт с объёмом проекта.

Пагинация: страницы по startAt; если сервер обрезает глубину (лимит вида
jira.search.max.results) — пустая страница приходит раньше конца total.
Тогда диапазон по created делится пополам рекурсивно (проверено на Apache
Jira 10.3: startAt за границей total возвращает пусто БЕЗ ошибки). Ключи
дедуплицируются сквозно — границы окон и сдвиги выдачи на живом сервере
не порождают дублей.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator

from .client import JiraClient

log = logging.getLogger(__name__)

SEARCH_PATH = "rest/api/2/search"

# Мельче этого окна диапазон created не делим: если сервер режет пагинацию
# даже внутри часа, окно сдается с громким предупреждением.
MIN_WINDOW = dt.timedelta(hours=1)
_MAX_DEPTH = 40  # страховка от бесконечного деления


def _parse_date(value: str, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise ValueError(f"{name}: ожидалась дата ГГГГ-ММ-ДД, получено {value!r}") from None


def _parse_moment(value: str, name: str) -> dt.datetime:
    """Дата или момент с временем: «ГГГГ-ММ-ДД» или «ГГГГ-ММ-ДД ЧЧ:ММ».

    Момент нужен инкременту: нижняя граница = fetched_at прошлого коммита,
    а не полночь какого-то дня (иначе за сутки до коммита попадут дубли)."""
    text = value.strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return dt.datetime.combine(_parse_date(text, name), dt.time.min)
    except (ValueError, AttributeError):
        raise ValueError(f"{name}: ожидались дата ГГГГ-ММ-ДД "
                         f"или момент ГГГГ-ММ-ДД ЧЧ:ММ, получено {value!r}") from None


def validate_dates(date_from: str | None, date_to: str | None) -> None:
    """Даты фильтра выгрузки: YYYY-MM-DD (или момент с ЧЧ:ММ для инкремента),
    date_from не позже date_to.

    Единая точка валидации: web-эндпоинты оборачивают ValueError в 400,
    CLI/fetch получают понятный текст вместо кривого JQL.
    """
    lo = _parse_moment(date_from, "date_from") if date_from else None
    hi = (dt.datetime.combine(_parse_date(date_to, "date_to"), dt.time.min)
          if date_to else None)
    if lo and hi and lo > hi:
        raise ValueError(f"date_from ({date_from}) позже date_to ({date_to})")


DATE_FIELDS = ("created", "updated")


def build_jql(project_key: str, date_from: str | None = None,
              date_to: str | None = None, order: str = "created ASC",
              date_field: str = "created") -> str:
    """JQL основной выгрузки: ключ в кавычках, date_to включительно
    (дата без времени = полночь, поэтому верхняя граница строго < следующего дня).
    date_field — по какому полю фильтровать: created или updated."""
    if date_field not in DATE_FIELDS:
        raise ValueError(f"date_field: ожидалось одно из {DATE_FIELDS}, получено {date_field!r}")
    validate_dates(date_from, date_to)
    jql = f'project = "{project_key}"'
    if date_from:
        jql += f' AND {date_field} >= "{date_from}"'
    if date_to:
        nxt = _parse_date(date_to, "date_to") + dt.timedelta(days=1)
        jql += f' AND {date_field} < "{nxt.isoformat()}"'
    jql += f" ORDER BY {order}"
    return jql


class _State:
    """Сквозное состояние обхода всех окон: счётчик, дедуп, лимит."""

    def __init__(self, max_issues: int | None, start_at: int, seen: set | None):
        self.given = start_at
        self.seen = seen if seen is not None else set()
        self.max_issues = max_issues
        self.fallbacks = 0

    def full(self) -> bool:
        return self.max_issues is not None and self.given >= self.max_issues


def _ts(v: dt.datetime) -> str:
    return v.strftime("%Y/%m/%d %H:%M")


def iter_issues(
    client: JiraClient,
    project_key: str,
    fields: list[str],
    *,
    page_size: int = 100,
    max_issues: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "created",
    start_at: int = 0,
    seen: set | None = None,
    on_total=None,
) -> Iterator[dict]:
    """Генератор задач (issue-объекты как в API).

    start_at — позиция продолжения (возобновление выгрузки); seen — ключи,
    скачанные раньше (дедуп на стыках окон); on_total(total) вызывается,
    когда сервер сообщил общий размер выборки (прогресс в UI).
    date_field — по какому полю фильтровать: created или updated
    (updated — инкремент/период изменений; date_from может нести время).
    """
    if date_field not in DATE_FIELDS:
        raise ValueError(f"date_field: ожидалось одно из {DATE_FIELDS}, получено {date_field!r}")
    validate_dates(date_from, date_to)
    st = _State(max_issues, start_at, seen)
    lo = _parse_moment(date_from, "date_from") if date_from else dt.datetime(1970, 1, 1)
    hi = (dt.datetime.combine(_parse_date(date_to, "date_to"), dt.time.min)
          + dt.timedelta(days=1) if date_to else None)  # None = до текущего момента
    yield from _window(client, project_key, fields, lo, hi, page_size, st, on_total, 0,
                       date_field)
    if st.fallbacks:
        log.warning("Пагинация: %d раз(а) диапазон делился по %s "
                    "(сервер ограничивает глубину startAt)", st.fallbacks, date_field)


def _window(client: JiraClient, project_key: str, fields: list[str],
            lo: dt.datetime, hi: dt.datetime | None, page_size: int,
            st: _State, on_total, depth: int,
            date_field: str = "created") -> Iterator[dict]:
    """Выдать задачи окна [lo, hi); hi=None — без верхней границы."""
    jql = f'project = "{project_key}" AND {date_field} >= "{_ts(lo)}"'
    if hi is not None:
        jql += f' AND {date_field} < "{_ts(hi)}"'
    jql += " ORDER BY created ASC"
    params = {
        "jql": jql,
        "fields": ",".join(fields),
        "maxResults": min(page_size, st.max_issues) if st.max_issues else page_size,
        "startAt": 0,
    }
    pos = 0            # сырая позиция в окне (startAt следующей страницы)
    new_in_window = 0  # новых (не дедуплицированных) задач в окне
    while not st.full():
        data = client.get(SEARCH_PATH, params=params)
        batch = data.get("issues", [])
        total = data.get("total")
        if on_total is not None and callable(on_total) and total is not None:
            on_total(total)
        if not batch:
            if total is not None and pos >= total:
                return  # окно докачано
            # пустая страница раньше конца окна: сервер оборвал глубину пагинации
            # (даже если в окне были уже скачанные ключи — за границей cap могут
            # быть новые, поэтому продолжаем только делением диапазона)
            st.fallbacks += 1
            log.warning("Пагинация оборвана в окне [%s .. %s): %d из %s — делю по %s",
                        _ts(lo), _ts(hi) if hi else "…", new_in_window, total, date_field)
            yield from _split(client, project_key, fields, lo, hi, page_size, st, on_total,
                              depth, date_field)
            return
        for issue in batch:
            key = issue.get("key")
            if not key or key in st.seen:
                continue
            st.seen.add(key)
            st.given += 1
            new_in_window += 1
            yield issue
            if st.full():
                return
        pos += len(batch)
        if total is not None and pos >= total:
            return
        params["startAt"] = pos


def _split(client: JiraClient, project_key: str, fields: list[str],
           lo: dt.datetime, hi: dt.datetime | None, page_size: int,
           st: _State, on_total, depth: int,
           date_field: str = "created") -> Iterator[dict]:
    """Разделить окно пополам и выкачать обе половины по очереди."""
    if hi is None:  # открытая верхняя граница — текущий момент + сутки
        hi = dt.datetime.now() + dt.timedelta(days=1)
    mid = lo + (hi - lo) / 2
    if hi - lo <= MIN_WINDOW or depth >= _MAX_DEPTH:
        log.error("Не удалось докачать окно [%s .. %s): сервер режет пагинацию даже "
                  "внутри часа — выгрузка будет неполной, уменьши диапазон дат",
                  _ts(lo), _ts(hi))
        return
    yield from _window(client, project_key, fields, lo, mid, page_size, st, on_total, depth + 1,
                       date_field)
    yield from _window(client, project_key, fields, mid, hi, page_size, st, on_total, depth + 1,
                       date_field)
