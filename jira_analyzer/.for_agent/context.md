# Контекст проекта: jira_analyzer

## Суть
Пользователь работает в корпоративном аудите крупной организации
(название организации нигде не упоминать — ни в файлах проекта, ни в
публичных репозиториях: там за этим следят). Задача от руководства расплывчатая:
«проанализировать работу разработчиков» по данным Jira. Стратегия: собрать максимум
метрик модульно, нужные отобрать позже.
Только ~1.9% задач имеют дедлайны → метрики сроков не строят, факт зашит в отчёт.
Имена секций .env пользователя: JIRA_TOKEN и JIRA_DELTA_TOKEN (префиксы jira, jira_delta;
в config.py читаются суффиксы и _TOKEN, и _PAT).

## Среда
- Дома: Linux (разработка). На работе: **Windows** → всё должно запускаться на Win.
- Python 3.11+. Авторизация: **PAT-токен** → `Authorization: Bearer <token>` (Server/DC).
- **ВАЖНО: на работе ДВЕ разные Jira** с разными PAT, структура полей примерно одинакова.
  В .env секции JIRA1_* / JIRA2_*, параметр «тип jira» передаётся вместе с именем проекта.
- Корпоративный прокси перехватывает TLS → доверяем хранилищу Windows: pip ≥24.2 сам
  (truststore), для requests — пакет `truststore` + `inject_into_ssl()` в начале main.py
  и main() web. Никаких verify=False / trusted-host / CA-файлов. Запуск web только
  штатно (start.bat / `python -m src.web.app`) — прямой `uvicorn src.web.app:app`
  минует инъекцию.
- setup.bat/start.bat/setup/start.py — ТОЛЬКО ASCII (кириллица в UTF-8 ломает cmd.exe);
  python ищется фактическим запуском (в PATH первым стоит Anaconda 3.9); pip поднимается
  ensurepip интерпретатором venv (не базовым — иначе ставит «в пользователя»).

## Стек (финал)
requests / pandas / pyarrow (parquet-кеш) / FastAPI + pydantic / ECharts 5.6.0
вендорен в src/web/static/vendor/ (сеть на работе закрыта, CDN не использовать).
matplotlib выпилен. Зависимости — ограниченные диапазоны под Python 3.11–3.13;
dev (pytest, httpx) — в requirements-dev.txt.

## Тестовая площадка (дом, без auth)
- **CURATOR** — issues.apache.org/jira, Jira Server 10.3, 724 задачи, анонимное чтение
  API (v2). Запасные: RAT (583), ANY23 (574).
- Changelog анонимно (expand=changelog); Apache отключил /issue/{key}/changelog →
  стратегия expand + догрузка урезанных (>100 событий) эндпоинтом, если есть.
- Epics в Apache-проектах отсутствуют → epic-ветку дерева и custom field «Epic Link»
  узнавать через user_tests на работе.

## Масштаб (решения этапа 1)
- На работе ~136k задач (163k в боевом тесте) → пагинация по 100, changelog батчами
  (expand_batch ≤ 100 — Jira search maxResults), потоковая запись JSONL (память O(страницы)).
- Комментарии off по умолчанию (--with-comments).
- Экстраполяция полной выгрузки на 136k ≈ 40–60 мин → потому инкремент (v8).

## Закреплённые решения (по версиям)
### v3: компонентная архитектура метрик
Метрика = .py-файл в metrics/ (наследник BaseMetric), автоскан. Выход = список
BlockSpec (pydantic, table/bar/line/pie/list), строятся хелперами self.table/self.bar/…,
JSON-safe. Рендер единый: blocks.js в web И инлайн в офлайн-отчёте. Упавшая метрика
не роняет задание (errors в payload). Инструкция для нейросети — metrics/README.md;
тест: python main.py --check-metrics.
### v4: параметры метрик
`params = [Param(...)]`; экземпляры {metric_id, params}; валидация backend
(resolve_params); CLI `--metrics 'id(имя=значение),…'`; params_label в заголовке карточки.
### v5: разделение выгрузки и анализа в web
Две зоны: «1 · Выгрузка» (POST /api/fetch) и «2 · Анализ» (таблица папок → /api/run
skip_fetch=true). Папки плоские data/raw/<КЛЮЧ>, метаданные в fetch_summary.json
(читает GET /api/downloads). Повторный fetch = тихая перезапись; смена типа Jira —
confirm(). Имя проекта санитизируется (^[A-Za-z0-9][A-Za-z0-9._-]*$).
### v6: надёжный fetch
_partial/ + state.json (байтовые офсеты, отпечаток параметров); корень не меняется
до полного успеха; resume/fresh — явный выбор пользователя (web 409 / CLI флаги).
«⏹ Стоп» → статус stopped, прогресс сохранён. JSONL всегда «последняя запись по
ключу выигрывает» (commit-дедуп + tables.py) — лечит догрузку полных историй.
Пагинация устойчива к лимиту глубины: пустая страница раньше total → рекурсивное
деление окна по created (до 1 часа). Параллельные задания web не ограничены
(решение пользователя); JOBS подрезается до 50 готовых.
### v7: фазы выгрузки + parquet-кеш + requires (done 2026-11-16)
- phases both|issues|changelog: changelog-only докачивает историю к готовой папке,
  issues.jsonl не трогается; фазы в fingerprint и fetch_summary.
- parquet-кеш data/processed/<ключ>/ (pyarrow), инвалидация size+mtime_ns, атомарная
  запись, без pyarrow — по-старому. Ловушка: путь data/processed, НЕ data/raw/processed
  (/api/downloads сканирует raw).
- BaseMetric.requires (issues/events/comments): событийные метрики без истории —
  в web disabled+тултип, в CLI понятная ошибка с командой докачки. Это же закрыло
  KeyError rework.py при пустых событиях.
- Проверено живым E2E apache + браузером; 53 pytest.
### v8: инкремент + updated-период + resume-дифф (done 2026-11-17)
- `incremental=True` (`--update` / web-чекбокс «обновить (только изменения)»):
  JQL `updated >= fetched_at` прошлого коммита (fetch_summary.json) или явный
  date_from; date_to/max_issues запрещены; дельта мержится _merge_file (потоково,
  «последняя выигрывает»). Summary: incremental, incremental_from,
  counts.delta_issues, итоговые counts по папке.
- `date_field created|updated` во всём стеке пагинации (`--date-field`, select в
  web); date_from может нести время «ГГГГ-ММ-ДД ЧЧ:ММ» (validate_dates/_parse_moment).
- Resume-ошибка перечисляет разошедшиеся поля fp («Разошлись: k: было → стало»).
- Проверено фейковым стендом (61 pytest); на живой Jira инкремент не гонялся.
- Оговорка: удалённые в Jira задачи дельтой не ловятся — сейчас игнорируются;
  при проблеме — периодический полный рефetch.

## Срезы метрик «на дату» — почему это НЕ «параметр в метрику»
tables.py при загрузке дедупит «последняя запись по ключу» до финального состояния —
история до метрики не доходит. Срез = tables, умеющие строить состояние на дату D
(реплей changelog) + изменение контракта build(tables, opts) + поле даты в UI/CLI +
переписывание метрик под «функция от даты». Отложено до реального запроса
«покажи как было тогда» — самому не поднимать.

## Windows-деплой (13–16.11.2026, итог)
Разворачивание проверено боём: setup с нуля на Python 3.12.7, 44 теста, живая
выгрузка apache через truststore. Правки восстановлены в этой копии по сводке
(файлы с работы недоступны); отличие от рабочей версии: _pip_install через
ensurepip в venv. На рабочем компе остаётся старый код до первого git clone —
напоминание в INDEX.
