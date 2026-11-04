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


def set_env_key(path: Path, key: str, value: str) -> None:
    """Записать/заменить ключ в .env, сохранив остальное содержимое файла."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    prefix = key + "="
    out: list[str] = []
    done = False
    for ln in lines:
        if ln.strip().startswith(prefix):
            if not done:
                out.append(f'{prefix}"{value}"')
                done = True
            continue  # дубликаты ключа удаляем
        out.append(ln)
    if not done:
        out.append(f'{prefix}"{value}"')
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
