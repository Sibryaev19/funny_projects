# Устройство проекта jira_analyzer (карта для агента)

Фактическая карта кода. Читать вместе с INDEX.md (статусы задач) и context.md
(решения и «почему»). Инструкция для написания метрик — metrics/README.md.

## Поток данных (всё общение модулей — через файлы data/)
```
.env ──> fetch ──> data/raw/<КЛЮЧ>/_partial/* (чекпоинты) ─коммит─> data/raw/<КЛЮЧ>/
                    {issues.jsonl, changelog.jsonl, tree.json, fetch_summary.json}
                ──> tables (3 pandas.DataFrame; parquet-кеш в data/processed/<КЛЮЧ>/)
                ──> metrics (список BlockSpec)
                ──> payload ──> web-рендер (echarts) / офлайн report.html / Excel
```
**Фазы выгрузки (v7):** `phases` = both | issues | changelog — качать можно
только задачи, только историю (к УЖЕ скачанным задачам, issues.jsonl не
трогается) или обе. `fetch_summary.json["phases"]` помечает, что в папке.
Метрики декларируют `requires` (issues/events/comments): без истории
событийные метрики disabled в web-модалке (тултип) и дают понятную ошибку
в отчёте, а не падение. Парquet-кеш: src/core/cache.py — data/processed/,
инвалидация по size+mtime_ns сырых jsonl, pyarrow (без него тихо без кеша).
Скачал один раз — анализируй сколько угодно: fetch и анализ разделены
(v5): web «1 · Выгрузка» = fetch-only задание, «2 · Анализ» = выбор папки
из data/raw + пересчёт без fetch. CLI: `--skip-fetch`.

**Выгрузка с прогрессом и возобновлением (v6).** fetch пишет всё в
`_partial/` с чекпоинтом `state.json` после каждой страницы задач и каждой
пачки changelog/догрузки/комментариев (байтовые офсеты файлов, счётчики,
фаза, отпечаток параметров). Корень папки не меняется до полного успеха;
коммит = перенос файлов с дедупом по ключу (последняя запись выигрывает).
Прерванная выгрузка (падение, «Стоп» в web, закрытие сервера) продолжается:
web — баннер «Продолжить/Начать заново», CLI — `--resume` / `--fresh`.
Продолжать можно только с тем же отпечатком (проект, jira_type, даты,
max_issues, комментарии, hash fields.json) — иначе только заново.
Пагинация: пустая страница раньше конца total (сервер режет глубину startAt)
→ диапазон по created делится пополам рекурсивно (issues.py), ключи
дедуплицируются сквозно. Даты fetch валидируются (YYYY-MM-DD, from<=to) на
web-эндпоинтах (400) и в fetch-слое; date_to включительно (JQL < след. день).

## Слои и файлы

**main.py** — CLI. `parse_metrics_spec()` парсит `'id(k=v),id2'` в экземпляры
`{metric_id, params}` (значения строками — типы приведёт resolve_params);
команды `--list-metrics`, `--check-metrics`; `--phases` (устаревший
--skip-changelog = phases issues); прогон = run_pipeline.

**src/core/**
- `config.py` — `load_env()`, `load_config(jira_type)` → AppConfig
  (JiraInstance: url/auth/token из секций .env по префиксу; FetchSettings:
  delays/retries/page_size). `PROJECT_ROOT` — корень проекта.
- `pipeline.py` — оркестратор без логики: `PipelineOptions` (project,
  jira_type, fetch-параметры, `phases`, `metrics` — список экземпляров,
  `fetch_only`, `skip_fetch`), `run_fetch()` (только выгрузка),
  `run_pipeline()` (fetch? → load_tables → для каждого экземпляра:
  requires-проверка → resolve_params → build → sanitize_result → отчёты;
  метрика без нужных данных = ошибка с подсказкой докачать, не исключение),
  `_instances()` (нормализация "id"|dict).
- `cache.py` — parquet-кеш: `processed_dir(raw)` = data/processed/<ключ>/
  (parent.parent!), сигнатура size+mtime_ns в manifest.json, `_fresh`/
  `_load_parquet`/`_store` (os.replace — атомарно), HAVE_PARQUET=False →
  тихо без кеша. **Не хранить в raw/** — /api/downloads сканирует raw.
- `tables.py` — `load_tables(raw_dir, use_cache=True)` → {"issues",
  "events", "comments"} DataFrame; свежий кеш читается сразу, иначе разбор
  JSONL (`_build_tables`) + запись кеша. issues: key, summary, type, status,
  status_category ("To Do"/"In Progress"/"Done"), priority, assignee,
  reporter, created, updated, resolved_at, duedate, resolution, components,
  parent, n_subtasks, timespent_h. **Даты — Timestamp, UTC, tz-aware, NaT
  если нет.** Помощники метрикам: `status_category_map()`, `status_events()`.

**src/fetch/** — client.py (`JiraClient`: Bearer/none, задержки, ретраи с
+1с), issues.py (`build_jql` с валидацией дат и кавычками ключа,
`iter_issues`: пагинация по page_size=100 + fallback окон по created при
обрыве глубины, `start_at`/`seen` для резюме, `on_total` для прогресса,
`validate_dates`), changelog.py (`iter_changelog`: expand батчами по 50 +
догрузка урезанных через endpoint, если доступен), comments.py (off по
умолчанию), tree.py (epic→task→subtask), state.py (`_partial/state.json`:
fingerprint — project/jira_type/url/даты/max_issues/**phases**/comments/
fields, load/save атомарно, truncate_files по офсетам), run.py
(`fetch_project(phases=both|issues|changelog)`: фазы
issues→changelog→doload→comments→commit, каждая фаза пишет чекпоинт и
начинается только если входит в phases; changelog-only берёт ключи из
готового out_dir/issues.jsonl и на коммите не трогает его; `_Jsonl` —
бинарная дозапись с точными офсетами; `FetchStopped` для «Стопа»; коммит
с дедупом по ключу; CLI с --phases/--resume/--fresh; останавливается между
страницами/пачками).

**src/components/** — фреймворк метрик и рендера:
- `base.py` — `BaseMetric` (id/label/description/**params**/**requires**
  (issues|events|comments; по умолчанию issues) +
  `build(tables, opts) -> [блоки]`), `MetricOpts` ({params}, `opts.get("имя")`),
  хелперы блоков `self.table/table_from_df/bar/bar_from_counts/line/pie/
  pie_from_counts/list_block` (JSON-safe автоматически).
- `params.py` — `Param(name, label, type number|text|date|select, default,
  placeholder, required, options, hint)`; `resolve_params()` (дефолты,
  приведение типов, required/неизвестные/select-опции → понятный ValueError);
  `params_label()` (сводка «порог, дней: 45»); `validate_decl()`.
- `blocks.py` — типы блоков (pydantic, дискриминатор `type`): table
  {columns, rows, align}, bar {labels, series, horizontal, stacked},
  line {x, series}, pie {items}, list {items}; общие title/note/collapsed.
  `MetricResult` (+params_label), `json_safe()`, `sanitize_result()`.
- `registry.py` — автоскан metrics/ при старте (файлы с "_" игнорируются),
  METRICS dict, `load_errors()`.
- `reports.py` — `payload()` (таблицы ≤200 строк, полные в Excel),
  `build_html()` (самодостаточный офлайн-файл: данные + echarts + blocks.js
  инлайном), `build_excel()` (лист ≤5000 строк; нет таблиц — пояснительный
  лист вместо падения).
- `check.py` — харнесс: `synthetic_tables()` (Random(42)),
  `selftest_params()` (юнит-тесты resolve_params), `run_check()`.

**metrics/** — 10 компонентов-метрик:
| id | label | params |
|----|-------|--------|
| overview | Обзор проекта | — |
| activity_weekly | Активность по неделям | date_from, date_to (по created) |
| activity_users | Активность по исполнителям | date_from, date_to (по created) |
| completed_by_assignee | Выполнено по исполнителям | date_from, date_to (по resolved_at) |
| stale_issues | Зависшие задачи | stale_days (number, 14) |
| discipline_checks | Дисциплина | — |
| lifecycle_cycles / lifecycle_summary | Циклы задач / Сводка времени жизни | — |
| rework_issues / rework_summary | Переоткрытия / Сводка | — |

**src/web/**
- `app.py` — FastAPI. GET `/` (index.html перечитывается каждый раз — правки
  статики без рестарта), `/api/metrics` (декларации params + requires),
  `/api/downloads` (скан data/raw + fetch_summary.json + has_changelog/phases
  + инфо о _partial для баннера резюма), POST `/api/fetch` (fetch-only с
  phases; resume/fresh обязательны при наличии прогресса — иначе 409;
  валидация дат → 400, кроме phases=changelog) и `/api/run` (анализ; UI шлёт
  project+metrics+skip_fetch=true), POST `/api/job/{id}/stop` («Стоп», 409
  если задание не идущая выгрузка). Имя проекта санитизируется:
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` (из него строится путь).
- `jobs.py` — фоновые потоки, Job/JOBS в памяти (готовые подрезаются до 50);
  перехват INFO-логов СВОЕГО потока в кольцевой буфер (параллельные задания
  логи не смешивают); `opts.progress` (фаза/done/total для UI) и
  `opts.stop_event` подключаются в start_job — не оторвать; FetchStopped →
  статус stopped. `run_fetch | run_pipeline` по fetch_only.
- `static/index.html` — две зоны (v5): «1 · Выгрузка» (форма fetch + кнопка,
  confirm() при перезаписи из другого типа jira, баннер незавершённой
  выгрузки «Продолжить/Начать заново», кнопка ⏹ Стоп у идущей выгрузки,
  строка прогресса «фаза: done / total (%)») и «2 · Анализ» (таблица
  выгрузок с выбором строки + модалка метрик: поля строятся из деклараций,
  у выбранных ✎ (правка in place), ↑↓ (порядок = порядок в отчёте), ✕).
  Список метрик строго пуст при каждом открытии.
- `static/blocks.js` — ЕДИНЫЙ рендер для web и офлайн-отчёта: карточки,
  сворачивание, echarts-графики, params_label серым в заголовке, errors.
- `static/vendor/echarts.min.js` — вендоренный ECharts (сеть закрыта, CDN
  нельзя).

## Точки расширения (куда лезть при типовых задачах)
1. **Новая метрика** → один файл в metrics/ по metrics/README.md. Бэкенд,
   фронтенд, реестры не трогаются. Проверка: `--check-metrics`.
2. **Параметр существующей метрики** → `params = [Param(...)]` в классе;
   web-форма и CLI `id(имя=значение)` появятся сами.
3. **Новый ТИП блока** (редко) → blocks.py модель + blocks.js рендерер,
   см. metrics/README.md раздел про блоки.
4. **Пайплайн/задания** → src/core/pipeline.py; web-обвязка app.py + jobs.py.
5. **Новые поля выгрузки** → src/fetch/issues.py (fields) + src/core/tables.py
   (нормализация колонок).

## Контракты и грабли (не регрессировать)
- Упавшая метрика (build ИЛИ параметры) → errors в payload, задание живо;
  resolve_params вызывается до build — ошибка параметров выглядит как
  упавшая метрика с params_label сырых значений.
- Всё содержимое блоков — JSON-safe (хелперы + sanitize_result страховка).
- Даты в tables — UTC tz-aware; «наивные» операции через
  `s.dt.tz_convert("UTC").dt.tz_localize(None)`.
- status_category надёжнее status (локализация процессов заказчика).
- Синтетика харнесса детерминирована: random.Random(42).
- CSS `.hidden { display: none !important; }` — !important в конце стилей
  (исторически побеждался порядком правил).
- Батчи/стриминг заточены под ~136k задач; комментарии off по умолчанию.
- **JSONL читается «последняя запись по ключу выигрывает»** (tables.py
  `_iter_dedup`, run.py `_commit_file`): догруженные полные истории и хвосты
  resume не должны удваивать события — не убирать дедуп.
- **Офсеты чекпоинта — байтовые**: файлы выгрузки пишутся в бинарном режиме
  (`_Jsonl` в run.py); текстовый `f.tell()` даёт непрозрачные cookie.
- **`opts.progress` и `opts.stop_event` подключаются в jobs.start_job** —
  тест `test_stop_wiring_reaches_fetch` ловит отрыв.
- JSONL-файлы выгрузки нельзя читать текстом с офсетами из state — только
  через таблицы (load_tables) или last_line_offsets (дедуп сам устойчив).
- Параллельные задания web НЕ ограничиваются (решение пользователя: на его
  совести) — два fetch в одну папку = гонка; тесты/эксплуатация ждут
  завершения.
- Windows: setup.bat (установка) / start.bat (запуск web) — CRLF + chcp
  65001 (кириллица в консоли); accept-тесты на реальном Windows ещё не
  гонялись.
- Порт занят прошлым сервером: `pkill -f 'src[.]web[.]app'` — паттерн с [.]
  чтобы не убить собственную команду (ловушка pkill -f; НЕ совмещать pkill
  и запуск сервера в одной bash-строке — pkill убьёт её по подстроке
  «src.web.app» из nohup-части).
- pkill-паттерн, `python -I` для чужих данных; версии в requirements.txt
  ограничены диапазонами под Python 3.11–3.13 (проверено на 3.12/pandas 3.1).

## Как проверять изменения
```
python -m pytest                               # автотесты (без сети), см. README
python main.py --check-metrics                 # юнит params + все метрики
python main.py --project CURATOR --skip-fetch  # CLI-прогон по выгрузке
python -m src.web.app                          # web http://127.0.0.1:8765
```
Web-сценарии гонять браузер-автоматом: две зоны, модалка (поля из
деклараций), выбор строки таблицы, коллизия типов jira, ✎↑↓✕, повтор
метрики с разными параметрами, цикл «старт → Стоп → Продолжить».
Тестовые данные: CURATOR (724 задачи, 8523 события — эталон), RAT (15),
jira-type apache, без токена. Разовые скрипты — в .for_agent/test/,
автотесты — в tests/ (pytest, фейковый HTTP-стенд conftest.FakeJira).

## История версий
- v1 — пайплайн fetch → analysis → отчёты (plan-jira_analyzer.md)
- v2 — web на FastAPI с прогрессом
- v3 — компонентная архитектура метрик (plan-v3_components.md)
- v4 — параметры метрик + модалка (plan-v4_metric_params.md)
- v5 — разделение выгрузки и анализа в web (plan-v5_fetch_analysis_split.md)
- v6 — надёжный fetch: чекпоинты/резюме/Стоп, окна пагинации, валидация
  дат, тесты pytest, зависимости 3.11–3.13 (plan-v6_fetch_resume.md)
