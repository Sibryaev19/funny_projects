"""Парсинг .env (KEY=VALUE) — общий для сервера и приёмника."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
# Путь к .env по умолчанию; тесты могут подменять.
ENV_PATH = PROJECT_ROOT / ".env"


def parse_env(path: Path) -> dict[str, str]:
    """Простейший KEY=VALUE парсер: комментарии '#', пустые строки пропуск."""
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return data
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def load_env(path: Path | None = None) -> dict[str, str]:
    return parse_env(path or ENV_PATH)
