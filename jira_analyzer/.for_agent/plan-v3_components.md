# План: v3 компонентная архитектура метрик

Статус: **done** (реализовано и проверено 2026-11-13)
Дата: 2026-11-13
Решение по открытому вопросу: **JS-рендер везде** — отчёт тоже рендерится из payload
через инлайн echarts + blocks.js; matplotlib выпиливается полностью.

## Цель
Метрика = компонент (класс-наследник `BaseMetric`), результат — JSON-спецификация блоков
(таблица/график/список/...), которую рендерят: web-интерфейс (интерактивные JS-графики),
HTML-отчёт (самодостаточный, офлайн) и Excel (табличные блоки). Пользователь/нейросеть
кладёт .py-файл в `metrics/` — проект подхватывает сам. Инструкция для нейросети +
тест-харнесс компонентов.

## Принятые решения (шаг 1)
- Старые 9 статистик **переписываются** как компоненты; registry/plots/stats удаляются.
- Графики: **клиентский рендер из JSON**, библиотека **ECharts вендорится** в репозиторий
  один раз (сеть на работе закрыта; CDN отпадает). matplotlib выпиливается.
- Папка пользовательских метрик: **`metrics/` в корне**, рекурсивный автоскан при старте.
- Компонент возвращает **список блоков** (конструктор: KPI + таблица + график и т.д.).
- Валидация блоков — **pydantic** (уже есть с FastAPI), кривой блок не роняет задание.
- Ручной ввод пользователя в блоках (п.8 видения) — НЕ в этой версии, но схема блоков
  проектируется так, чтобы поле добавилось без ломки.

## Новая структура
```
src/components/            # фреймворк (без метрик)
  blocks.py                # pydantic-спеки: TableBlock, BarBlock, LineBlock, PieBlock,
                           #   ListBlock (+ Series); сериализация дат/numpy -> JSON-safe
  base.py                  # BaseMetric (id/label/description, build(tables, opts) -> [Block])
                           #   + register; хелперы self.table/self.bar/self.line/...
  registry.py              # METRICS: dict + autodiscover(metrics/) по файлам
  reports.py               # excel: лист на TableBlock; html: payload + инлайн
                           #   vendor/echarts.min.js + static/blocks.js (единый рендер!)
  check.py                 # тест-харнесс: прогон метрик на синтетике/реальной выгрузке
                           #   + валидация схемы, PASS/FAIL на каждую
src/core/tables.py         # load_tables, cat_of, status_events (перенос из analysis/tables.py)
static/blocks.js           # JS-рендереры блоков + collapse/expand; используется И web,
                           #   И инлайнится в report.html (один источник рендеринга)
static/vendor/echarts.min.js
metrics/                   # встроенные (переписанные) и пользовательские компоненты
  README.md                # ИНСТРУКЦИЯ ДЛЯ НЕЙРОСЕТИ: схема входных таблиц, API BaseMetric,
                           #   конструкторы блоков с примерами, как тестировать, куда класть
  overview.py activity.py lifecycle.py stale.py rework.py discipline.py
```
Удаляются: `src/analysis/` целиком (registry, plots, stats/, tables→core, report→components).

## Формат блока (эскиз, пирогового поля type — дискриминатор)
- `table`: {columns, rows, align?} — даты уже строками
- `bar`: {labels, series[], horizontal?, stacked?}
- `line`: {x, series[]}
- `pie`: {items: [{name, value}]}
- `list`: {items[]}
- общий: {type, title, note?, collapsed?}; результат метрики: {metric_id, label,
  description, blocks[]}

## Этапы (чек-лист) — все выполнены
- [x] 1. ECharts 5.6.0 вендорен в src/web/static/vendor/echarts.min.js (1 МБ, Apache-2.0)
- [x] 2. Фреймворк: src/components/{blocks,base,registry}.py (pydantic-спеки,
      MetricOpts, автоскан metrics/ с игнором _* и контейнированием ошибок)
- [x] 3. tables.py → src/core/tables.py (load_tables, cat_of, status_events)
- [x] 4. 9 метрик переписаны компонентами в metrics/ (6 файлов, классы-наследники)
- [x] 5. reports.py: Excel лист на TableBlock (уникальные имена листов <=31 символ),
      HTML = payload + инлайн echarts + blocks.js (офлайн, интерактивен)
- [x] 6. static/blocks.js (рендереры table/bar/line/pie/list, сворачивание карточек
      и отдельных блоков, ленивая инициализация графиков, CSS самоинжект) +
      index.html (чекбоксы из /api/metrics, DOM-безопасно, без innerHTML для данных)
- [x] 7. pipeline.py считает метрики (упавшая не роняет задание -> errors), main.py:
      --metrics, --list-metrics, --check-metrics
- [x] 8. check.py (синтетика из random.Random(42): циклы/зависшие/переоткрытия,
      проверки: прямоугольность таблиц, длины серий против осей) + metrics/README.md
- [x] 9. src/analysis/ удалена, matplotlib выпилен (requirements, setup/start.py),
      e2e пройден: харнесс 9/9 PASS, CLI на CURATOR, web-задание через UI,
      офлайн-отчёт отрисован в браузере (9 карточек, 6 графиков), Excel 9 листов

## Итоговые заметки
- API: GET /api/metrics, POST /api/run (поле metrics), GET /api/job/{id}/blocks
  (payload с обрезкой таблиц до 200 строк + errors), /api/job/{id}/report без изменений.
- /static смонтирован (StaticFiles): index.html грузит vendor/echarts.min.js и blocks.js.
- Payload web/HTML ограничен 200 строками таблицы с пометкой (полные — в Excel, 5000).
- Гистограмма циклов (lifecycle_cycles) пропускается при пустом s после фильтра
  p95/неотрицательных — на тестовой CURATOR (30 задач) так и есть, это норма.
- «Скачать HTML» отдаёт отчёт с Content-Disposition: attachment — это скачивание,
  не навигация (проверка офлайн-рендера делалась через временную копию в /static).
