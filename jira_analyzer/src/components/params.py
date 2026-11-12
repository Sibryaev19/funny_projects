"""Параметры метрик, запрашиваемые у пользователя.

Метрика декларирует параметры списком Param(...) — поле params класса
(см. src/components/base.py). Из декларации web генерирует форму в модальном
окне, CLI принимает `--metrics 'stale_issues(stale_days=45),overview'`,
а конвейер перед построением приводит значения пользователя к объявленным
типам через resolve_params() (дефолты, приведение типов, required/неизвестные).

Типы полей (расширяемо, как типы блоков в blocks.py):
  number — число (приводится к int/float), в форме input type=number
  text   — строка как есть
  date   — дата YYYY-MM-DD (строка, формат проверяется), в форме календарь
  select — выбор одного значения из options
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

PARAM_TYPES = Literal["number", "text", "date", "select"]


class Param(BaseModel):
    """Объявление одного параметра метрики: подпись/тип поля для формы,
    дефолт, плейсхолдер (серый пример в пустом поле), обязательность."""

    name: str                                      # ключ в params: латиница/цифры/_
    label: str                                     # подпись поля в форме
    type: PARAM_TYPES = "text"
    default: Any = None                            # предзаполнение и значение при пустом поле
    placeholder: str = ""                          # пример-подсказка, напр. «пример: 2025-01-01»
    required: bool = False
    options: list[str] = Field(default_factory=list)   # варианты для select
    hint: str = ""                                 # пояснение под полем

# JSON-форма декларации для /api/metrics — Param.model_dump(), фронт
# строит из неё форму модального окна.


def resolve_params(decl: list[Param], values: dict | None) -> dict:
    """Значения пользователя -> чистый dict по декларации.

    Отсутствующим параметрам подставляются default; строки приводятся к типу
    (number -> int/float, date -> проверка формата YYYY-MM-DD, select ->
    значение из options). Неизвестные ключи и пустой required — ValueError
    с человекочитаемым текстом: конвейер покажет его как упавшую метрику
    (механизм errors из v3), задание при этом не роняется.
    """
    rest = dict(values or {})
    resolved: dict[str, Any] = {}
    for p in decl:
        v = rest.pop(p.name, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            if p.default is not None:
                resolved[p.name] = p.default
            elif p.required:
                raise ValueError(f"не задан обязательный параметр «{p.label}» ({p.name})")
            else:
                resolved[p.name] = None
            continue
        resolved[p.name] = _cast(p, v)
    if rest:
        raise ValueError("неизвестные параметры: " + ", ".join(sorted(rest)))
    return resolved


def _cast(p: Param, v: Any) -> Any:
    """Привести одно значение к типу параметра; ошибка — понятным текстом."""
    if p.type == "number":
        if isinstance(v, bool):
            raise ValueError(f"параметр «{p.label}» ({p.name}): ожидалось число, получено {v!r}")
        try:
            f = float(str(v).replace(",", "."))
        except ValueError:
            raise ValueError(
                f"параметр «{p.label}» ({p.name}): ожидалось число, получено {v!r}") from None
        if not math.isfinite(f):
            raise ValueError(f"параметр «{p.label}» ({p.name}): число должно быть конечным")
        return int(f) if f.is_integer() else f
    if p.type == "date":
        s = str(v).strip()
        try:
            dt.date.fromisoformat(s)
        except ValueError:
            raise ValueError(
                f"параметр «{p.label}» ({p.name}): ожидалась дата ГГГГ-ММ-ДД, "
                f"получено {v!r}") from None
        return s
    s = str(v).strip()
    if p.type == "select" and p.options and s not in p.options:
        raise ValueError(f"параметр «{p.label}» ({p.name}): допустимые значения — "
                         f"{', '.join(p.options)}")
    return s


def params_label(decl: list[Param], values: dict) -> str:
    """Краткая сводка значений для заголовка карточки метрики:
    «порог, дней: 45, дата с: 2025-01-01». Различает экземпляры одной метрики."""
    by_name = {p.name: p for p in decl}
    parts = []
    for name, v in values.items():
        p = by_name.get(name)
        if p is None or v is None:
            continue
        label = p.label[:1].lower() + p.label[1:] if p.label else name
        parts.append(f"{label}: {_human(v)}")
    return ", ".join(parts)


def _human(v: Any) -> str:
    """Числа без хвостового .0: 45 вместо 45.0; остальное — str()."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_decl(decl: list[Param]) -> str | None:
    """Проверка декларации целиком (тест-харнесс): имена валидны, не дублируются."""
    seen: set[str] = set()
    for p in decl:
        if not _NAME_RE.match(p.name):
            return f"{p.name!r}: имя параметра должно быть латиницей/цифрами/_, с буквы"
        if p.name in seen:
            return f"{p.name!r}: параметр объявлен дважды"
        if p.type == "select" and not p.options:
            return f"{p.name!r}: для select нужно перечислить options"
        seen.add(p.name)
    return None
