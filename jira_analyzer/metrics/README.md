# metrics/ — метрики-компоненты

Каждая метрика — **один python-файл в этой папке** с классом-наследником
`BaseMetric`. Проект подхватывает файл сам при старте сервера/CLI: декораторы,
регистрация в списках и правки конфигов не нужны. Положили файл → перезапустили
`python -m src.web.app` (или новый запуск CLI) → метрика появилась в интерфейсе.

Проверить свои метрики без полного прогона:

    python main.py --check-metrics                # на синтетических данных
    python main.py --check-metrics --project CURATOR   # на реальной выгрузке

Эта инструкция написана так, чтобы по ней метрику мог написать человек
или нейросеть, не зная остального проекта.

---

## 1. Минимальный рабочий пример

```python
# metrics/by_type.py
from src.components.base import BaseMetric


class ByType(BaseMetric):
    id = "by_type"                      # уникальный: латиница, цифры, _
    label = "Задачи по типам"           # заголовок блока в интерфейсе
    description = "сколько задач каждого типа в выгрузке"

    def build(self, tables, opts):
        counts = tables["issues"]["type"].value_counts()
        return [
            self.pie_from_counts(counts, title="Доля типов", top=10),
            self.bar_from_counts(counts),
            self.table(["Тип", "Задач"], counts.items()),
        ]
```

Файл заполнен — всё остальное (веб-рендер, графики, HTML-отчёт, Excel, тест)
проект делает сам.

## 2. Что получает build()

```python
def build(self, tables, opts) -> list:
```

- `tables` — словарь из **трёх pandas.DataFrame** (нормализованная выгрузка):

| ключ        | что это | колонки |
|-------------|---------|---------|
| `issues`    | задачи, 1 строка = 1 задача | `key` (str), `summary`, `type`, `status`, `status_category` ("To Do"/"In Progress"/"Done"), `priority`, `assignee`, `reporter` (имена, могут быть NaN), `created`, `updated`, `resolved_at`, `duedate` (**Timestamp, UTC, tz-aware, NaT если нет**), `resolution`, `components` (str, "" если нет), `parent`, `n_subtasks` (int), `timespent_h` (float) |
| `events`    | все события changelog | `key`, `time` (Timestamp UTC), `author`, `field` (напр. "status"), `from`, `to` |
| `comments`  | комментарии | `key`, `author`, `created`. **Может быть пустым** (комментарии по умолчанию не выгружаются) — всегда проверяйте `if ev.empty` / `len()`. |

- `opts` — `MetricOpts` с параметрами **вашего экземпляра** метрики: значения
  читаются через `opts.get("имя")`, ключи = имена `Param` из `params`
  (см. раздел 8 «Параметры метрики»). Дефолты уже подставлены, типы приведены.

Объём: до ~136k задач. Работайте векторно (методы pandas), без питоновских
циклов по строкам — иначе задание в вебе подвиснет.

## 3. Что возвращает build()

**Список из 1–5 блоков** (не DataFrame!). Блоки создаются хелперами `self.*`
— они сами превращают данные в JSON-safe вид (даты → строки, NaN → null,
numpy → python). Рисованием занимается интерфейс: метрика только описывает данные.

| хелпер | что рисует | типичные аргументы |
|--------|------------|--------------------|
| `self.table(columns, rows, *, title, note, align, collapsed)` | таблицу из готовых строк | `align` — список `"left"/"right"/"center"` по колонкам |
| `self.table_from_df(df, *, title, note, max_rows, collapsed)` | таблицу из DataFrame | даты/NaN конвертируются сами |
| `self.bar(labels, series, *, title, horizontal, stacked, note)` | столбчатую | `series` — `{"имя серии": [значения], ...}`; `labels` — подписи оси |
| `self.bar_from_counts(counts, *, title, horizontal=True, ...)` | столбчатую из `value_counts()` | индекс → подписи |
| `self.line(x, series, *, title, note)` | линейную (динамика во времени) | `x` — подписи оси (даты строками) |
| `self.pie(items, *, title, note)` | круговую | `items` — `{имя: значение}` или пары |
| `self.pie_from_counts(counts, *, title, top=10, ...)` | круговую из `value_counts()` | `top` — остальное в сектор «остальные» |
| `self.list_block(items, *, title, note)` | список строк | |

Общие именованные аргументы у всех: `title` (подзаголовок блока),
`note` (серый пояснительный текст), `collapsed=True` (блок стартует свёрнутым —
**делайте так для таблиц, которые могут быть длинными**).

Полные определения типов — `src/components/blocks.py` (pydantic-модели:
`TableBlock`, `BarBlock`, `LineBlock`, `PieBlock`, `ListBlock`; их можно
использовать и напрямую). Одинаковые названия серий/секторов объединяются
в легенду; подписи оси длинные не страшно — интерфейс обрежет.

## 4. Правила и типовые ошибки

1. **Не падайте на пустых данных**: `issues` может быть пустым, `events`/`comments`
   — пустые при дефолтной выгрузке. Пусто → верните `[self.list_block(["нет данных"])]`.
2. **Не возвращайте DataFrame** — только блоки (исключение из build() уронит
   только вашу метрику, остальные продолжат работать, а тест-харнесс покажет FAIL).
3. Даты в `tables` — **UTC, tz-aware**; для «наивных» операций: `s.dt.tz_convert("UTC").dt.tz_localize(None)`.
4. Категория статуса (`status_category`) надёжнее имени (`status`): имена
   зависят от локализации процесса заказчика.
5. Для событий смены статуса есть готовые помощники
   (импортируйте из `src.core.tables`):
   ```python
   from src.core.tables import status_category_map, status_events
   st = status_events(tables["events"], status_category_map(tables["issues"]))
   # колонки: key, time, from, to, cat, prev_cat  (категории To Do/In Progress/Done)
   ```
6. `id` уникальный и стабильный — по нему пользователь выбирает метрику
   в интерфейсе. Файлы/папки с префиксом `_` игнорируются (удобно для черновиков).
7. Не делайте side effects: no `print` (используйте `logging.getLogger(__name__)`),
   no запись файлов, no `matplotlib`.
8. Значения строк таблиц/графиков — скаляры; сложные объекты приводите
   к строке/числу сами (или передавайте через хелперы, они справятся).

## 5. Полный пример: метрика с расчётом из changelog

```python
# metrics/priority_flow.py
import pandas as pd

from src.components.base import BaseMetric


class PriorityFlow(BaseMetric):
    id = "priority_flow"
    label = "Смены приоритета"
    description = "как часто и кто меняет приоритет после создания задачи"

    def build(self, tables, opts):
        ev = tables["events"]
        if ev.empty:
            return [self.list_block(["нет данных — выгрузка без changelog"])]
        ch = ev[ev["field"] == "priority"]
        by_priority = ch["to"].value_counts()

        by_author = (ch.groupby("author").size().sort_values(ascending=False)
                     .rename("смен").reset_index())

        return [
            self.bar_from_counts(by_priority, horizontal=False,
                                 title="Куда меняли приоритет"),
            self.bar(list(by_author["author"]), {"смен": by_author["смен"].tolist()},
                     title="Кто меняет приоритет", horizontal=True),
            self.table_from_df(by_author, collapsed=True),
        ]
```

## 6. Тестирование своей метрики

```bash
python main.py --check-metrics     # PASS/FAIL на каждую метрику + структура блоков
python main.py --list-metrics      # видна ли метрика в реестре
```

Харнесс проверяет: декларацию `params` (если есть), build() с дефолтными
параметрами на синтетике (или реальной выгрузке), блоки валидны по схеме,
таблицы прямоугольны, длины серий сходятся с осями. После этого — полный прогон
в интерфейсе или `python main.py --project CURATOR --skip-fetch --metrics by_type`.

## 7. Параметры метрики (запрашиваемые у пользователя)

Метрика может **объявить параметры** — пользователь задаст их значения в
web-форме (модальное окно строится из декларации автоматически) или в CLI:
`--metrics 'stale_issues(stale_days=45)'`. Одну метрику можно добавлять
несколько раз с разными значениями — каждый экземпляр попадёт в отчёт со своей
сводкой параметров в заголовке карточки.

```python
# metrics/stale.py — параметр-число
from src.components.base import BaseMetric
from src.components.params import Param


class StaleIssues(BaseMetric):
    id = "stale_issues"
    label = "Зависшие задачи"
    description = "открытые задачи без обновлений дольше порога"
    params = [Param(name="stale_days", label="Порог, дней", type="number",
                    default=14, required=True)]

    def build(self, tables, opts):
        stale_days = opts.get("stale_days")       # уже float/int, дефолт подставлен
        ...
```

```python
# метрика с диапазоном дат (date) — metrics/activity.py
params = [
    Param(name="date_from", label="Дата с", type="date",
          placeholder="пример: 2025-01-01"),
    Param(name="date_to", label="Дата по", type="date"),
]
# в build(): opts.get("date_from") -> "2025-01-01" или None
iss = iss[iss["created"] >= pd.Timestamp(opts.get("date_from"), tz="UTC")]
```

Поля `Param`:

| поле | смысл |
|------|-------|
| `name` | ключ для `opts.get()`: латиница/цифры/`_`, с буквы; уникален в метрике |
| `label` | подпись поля в форме (попадает и в сводку карточки) |
| `type` | `"number"` (спиннер, приводится к int/float) · `"text"` · `"date"` (нативный календарь, значение — строка `YYYY-MM-DD`) · `"select"` (выбор из `options`) |
| `default` | предзаполнение формы; подставляется, если поле оставили пустым |
| `placeholder` | серый пример в пустом поле («пример: 2025-01-01») |
| `required` | пустое значение без дефолта → ошибка «не задан обязательный параметр» (метрика падает с понятным текстом, задание не роняется) |
| `options` | список строк для `select` (обязателен для этого типа) |
| `hint` | пояснение под полем формы |

Валидация значений (типы, `required`, неизвестные ключи, варианты `select`) —
на backend, в `resolve_params()` (`src/components/params.py`): метрика получает
уже чистые значения, проверять их в `build()` не нужно.

CLI: параметры перечисляются в скобках через запятую, значения — строками
(типы приведутся по декларации): `--metrics 'stale_issues(stale_days=45),overview'`.

## 8. Добавить НОВЫЙ ТИП блока (редкая задача)

Типы блоков — расширяемый набор: если таблицы/диаграмм не хватает
(например, теплограмма или KPI-карточки), добавляются:
1. pydantic-модель с `type: Literal["..."]` в `src/components/blocks.py`
   (+ в Union `Block`);
2. рендерер в `src/web/static/blocks.js` (функция + ветка в `renderBlock`);
3. опционально хелпер в `src/components/base.py`.
Это часть фреймворка, а не метрики — делается один раз для всех.
