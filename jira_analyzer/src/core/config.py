"""Загрузка конфигурации: .env + config/*.json.

Приоритет значений: .env > settings.json > значения по умолчанию.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> dict[str, str]:
    """Парсит .env: KEY=VALUE, строки с # — комментарии, кавычки снимаются."""
    env_path = path or PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class JiraInstance:
    url: str
    pat: str | None
    auth: str  # "bearer" | "none"

    @property
    def is_anonymous(self) -> bool:
        return self.auth == "none"


@dataclass
class FetchSettings:
    delay: float = 0.3          # пауза между запросами, сек
    max_retries: int = 5        # число ретраев после первой попытки
    timeout: float = 30.0       # таймаут запроса, сек
    backoff_step: float = 1.0   # база прогрессивной задержки: attempt * step
    page_size: int = 100        # задач на поисковый запрос
    expand_batch: int = 50      # задач в пачке changelog/comments
    with_comments: bool = False  # комментарии выключены по умолчанию (тяжело на больших проектах)
    date_from: str | None = None  # YYYY-MM-DD, фильтр JQL по created
    date_to: str | None = None


@dataclass
class AppConfig:
    data_dir: Path
    jira: dict[str, JiraInstance]     # ключ типа ("jira1") -> инстанс
    fetch: FetchSettings
    fields: list[str]                 # стандартные поля для выгрузки
    custom_fields: dict[str, str | None]  # id кастомных полей (epic_link и т.п.)


def _resolve_fetch_settings(env: dict[str, str], raw: dict) -> FetchSettings:
    f = raw.get("fetch", {})

    def pick(json_key: str, env_key: str, default):
        # .env имеет приоритет над settings.json
        return env[env_key] if env_key in env else f.get(json_key, default)

    return FetchSettings(
        delay=float(pick("delay", "FETCH_DELAY", 0.3)),
        max_retries=int(pick("max_retries", "FETCH_MAX_RETRIES", 5)),
        timeout=float(pick("timeout", "FETCH_TIMEOUT", 30)),
        backoff_step=float(pick("backoff_step", "FETCH_BACKOFF_STEP", 1.0)),
        page_size=int(pick("page_size", "FETCH_PAGE_SIZE", 100)),
        expand_batch=int(pick("expand_batch", "FETCH_EXPAND_BATCH", 50)),
        with_comments=bool(pick("with_comments", "FETCH_WITH_COMMENTS", False)),
        date_from=pick("date_from", "FETCH_DATE_FROM", None) or None,
        date_to=pick("date_to", "FETCH_DATE_TO", None) or None,
    )


def load_config(jira_type: str, env_path: Path | None = None) -> AppConfig:
    """Собирает конфиг: секция .env по типу jira + json-конфиги проекта."""
    env = load_env(env_path)
    settings_raw = load_json(PROJECT_ROOT / "config" / "settings.json")
    fields_raw = load_json(PROJECT_ROOT / "config" / "fields.json")

    key = jira_type.upper()
    if f"{key}_URL" not in env:
        known = sorted({k.split("_")[0] for k in env if k.endswith("_URL")})
        raise KeyError(f"В .env нет секции {key}_*. Найдены секции: {known or 'ничего'}")

    # токен ищем по одному из суффиксов: <KEY>_TOKEN или <KEY>_PAT
    pat = env.get(f"{key}_TOKEN") or env.get(f"{key}_PAT") or None
    jira = JiraInstance(
        url=env[f"{key}_URL"].rstrip("/"),
        pat=pat,
        auth=(env.get(f"{key}_AUTH") or "bearer").lower(),
    )
    if jira.auth == "bearer" and not jira.pat:
        raise ValueError(f"{key}: auth=bearer, но {key}_TOKEN/{key}_PAT не задан (или .env не создан)")

    data_dir = Path(env.get("DATA_DIR") or settings_raw.get("data_dir") or "data")
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    return AppConfig(
        data_dir=data_dir,
        jira={jira_type.lower(): jira},
        fetch=_resolve_fetch_settings(env, settings_raw),
        fields=list(fields_raw["standard_fields"]),
        custom_fields=fields_raw.get("custom_fields", {}),
    )
