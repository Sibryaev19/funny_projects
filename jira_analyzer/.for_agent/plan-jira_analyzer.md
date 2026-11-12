# План: jira_analyzer

**Статус: done — этапы 0–5 реализованы и проверены (2026-11-12, web v2 с результатами в UI).
Не реализовано (осознанно): PDF-экспорт отчёта (HTML+Excel достаточно до запроса), F5-заметка:
matplotlib categorical warnings в логе — косметика.**

## Цель
Модульная система анализа работы разработчиков по данным Jira (2 корпоративные Jira,
PAT-авторизация): выгрузка задач + changelog → метрики и графики → отчёт (Excel/HTML/PDF)
через веб-интерфейс. Разработка дома на открытой Apache Jira (CURATOR), перенос на
корпоративные инстансы через конфиг и user_tests.

## Стек
Python 3.11+ / requests / pandas / matplotlib / FastAPI + uvicorn /
PDF: weasyprint (или HTML-шаблон → print; решить на этапе отчёта).
Windows-совместимость: пути через pathlib, никаких POSIX-фич.

## Структура каталогов
```
jira_analyzer/
├── .env.example        # шаблон: JIRA1_*/JIRA2_* (URL, PAT), DATA_DIR, настройки fetch
├── .gitignore          # локальный: data/, .env
├── config/
│   ├── fields.json     # какие поля тянуть (наш «SELECT»)
│   └── settings.json   # задержка, ретраи, диапазон дат (можно переопределять из .env)
├── setup/
│   └── start.py        # поиск python, venv, установка зависимостей (этап 3)
├── src/
│   ├── fetch/          # этап 1: выгрузка
│   ├── analysis/       # этап 2: статистика + графики
│   ├── core/           # этап 4: оркестратор
│   └── web/            # этап 5: FastAPI
├── data/               # git-игнор: raw/<project>/*.json, tables/, reports/
├── main.py             # CLI-вход (этап 4)
└── user_tests/         # → .for_agent/user_tests (тесты для запуска на работе)
```

## Этапы

### Этап 0 — скелет ✅ done
- [x] каталоги, .env.example, локальный .gitignore
- [x] config-загрузчик (читает .env + fields.json + settings.json)

### Этап 1 — модуль fetch ✅ done (2026-11-12)
- [x] HTTP-клиент: Bearer-токен из .env по типу jira (JIRA1/JIRA2/аноним для Apache),
      задержка между запросами, ретраи с прогрессивной задержкой (+1с за попытку),
      обработка 429 (уважать Retry-After), таймауты
- [x] fetch_issues: JQL по проекту (+ диапазон дат), пагинация (page_size из конфига,
      по умолчанию 100/страница), поля из fields.json, потоковая запись issues.jsonl
- [x] changelog: батч-режим через search expand (50 задач/запрос) + догрузка урезанных
      (>100 событий) отдельным эндпоинтом, если доступен → changelog.jsonl
- [x] builder структуры: дерево parent/epic → tree.json (epic-поле — конфиг, узнаем на работе)
- [x] полный прогон CURATOR анонимно: 724 задачи + 8523 события за 19.5с
- ПРИНЯТЫЕ РЕШЕНИЯ: комментарии выключены по умолчанию (модуль comments.py остался,
  включение --with-comments / settings.json); большие артефакты — в JSONL (потоковая
  запись, память O(страницы)); масштаб 136k задач ≈ 40–60 мин полный прогон

### Этап 2 — модуль analysis ✅ done (2026-11-12)
- [x] normalize: JSONL → pandas-таблицы (issues/events/comments), категории статусов
      через statusCategory + эвристику имён (работает на любой локализации процесса)
- [x] реестр статистик @register_stat / графиков @register_plot (добавление = файл +
      импорт в stats/__init__.py)
- [x] 9 статистик: overview, activity_weekly, activity_users, lifecycle_cycles/summary
      (pickup/cycle/lead из changelog), stale_issues, rework_issues/summary,
      discipline_checks
- [x] экспорт: Excel (лист на статистику) + HTML (таблицы + PNG-графики base64)
- [x] смоук CURATOR: анализ 2.4с, отчёты data/reports/CURATOR/
- НЮАНСЫ ДАННЫХ CURATOR: workflow Open→Resolved напрямую (80% закрытий минуя работу —
  это правда, не баг); ASF GitHub Bot массово трогал задачи в 2025-01 → polluted updated;
  в корпоративной Jira учесть фильтрацию ботов/техпользователей из активностей

### Этап 3 — модуль setup ✅ done (2026-11-12)
- [x] setup/start.py: проверка python 3.11+, создание .venv, установка deps,
      создание .env из шаблона, контрольный импорт
- [x] start.bat для Windows: py-launcher → PATH → стандартные пути установки
      (проверен дома на Linux-ветке; Windows-ветка — user_test в конце)

### Этап 4 — core + CLI ✅ done (2026-11-12)
- [x] src/core/pipeline.py: PipelineOptions + run_pipeline (fetch → analysis)
- [x] main.py: CLI со всеми параметрами; смоук через venv-python OK

### Этап 5 — web ✅ done (2026-11-12)
- [x] FastAPI (src/web/app.py, порт 8765): форма, /api/stats (реестр → чекбоксы),
      POST /api/run, GET /api/job/{id} (лог-прогресс), /api/job/{id}/report (скачивание)
- [x] фоновые задания в тредах, прогресс из перехваченного лога модулей
- [x] UI (static/index.html, ванильный JS): форма + выбор статистик + лог + кнопки
      «Скачать HTML/Excel»; протестировано в браузере (запуск, прогресс, скачивание)
- ОТЛОЖЕНО: PDF-экспорт (сейчас HTML + Excel; PDF — по запросу, weasyprint)

## user_tests (точки делегирования)
1. **После этапа 1**: probe-скрипт для рабочей Jira — проверка Bearer-PAT, поиск ID
   custom field «Epic Link», объём задач проекта по датам
2. **После этапа 2**: прогон полного fetch на одном реальном проекте (объём/время)
3. **После этапа 5**: запуск setup+web на Windows-машине

## Готово = 
Дома: полный прогон CURATOR от выгрузки до HTML-отчёта через CLI.
На работе: выгрузка корпоративного проекта по PAT + отчёт.
