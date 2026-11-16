# План: перенос windows_setup-правок в эту копию (восстановление по сводке)

Статус: done (2026-11-16)
Контекст: правки сделаны 13–16.11.2026 в рабочей копии на работе; файлы оттуда
недоступны. Восстанавливаем по детальной сводке (текст в переписке чата).
Рабочая копия на работе сохранит старый код до первого git clone — откат возможен.

## Цель
Любая Windows-машина с прокси/антивирусом (TLS-перехват), Anaconda 3.9 в PATH и
битым .venv разворачивается через setup.bat без ручных правок.

## Чек-лист
1. `requirements.txt` — добавить `truststore>=0.9` с комментарием.
2. `main.py` — блок `try: import truststore; truststore.inject_into_ssl() /
   except Exception: pass` сразу после `import sys`, ДО импорта
   `src.core.pipeline` (инъекция один раз на процесс; патч действует на
   последующие создания SSLContext).
3. `src/web/app.py` — тот же блок в начале `main()`, до `import uvicorn`.
4. `setup/start.py` — переписать по сводке:
   - `_run_probe()`/`_probe_ok()` — безопасная проверка запуска интерпретатора;
   - `_candidate_interpreters()` + `_version_of()` + `_suitable_interpreters()`
     + `_choose_interpreter()` — поиск python 3.11+ перебором кандидатов с
     фактическим запуском (не «первый в PATH»: Anaconda 3.9 отсеется);
   - `_ensure_venv()` — создание venv; битый .venv (python не запускается) →
     пересоздание; pip поднимать через `ensurepip` интерпретатором venv
     (не базовым — иначе ставит «в пользователя»);
   - `_pip_interpreters()`/`_pip_install()`/`_install_requirements()` — установка
     с запасным интерпретатором;
   - `_force_utf8_console()` — SetConsoleOutputCP(65001) + reconfigure;
   - `_selftest()` + флаг `--selftest`.
   НЕ оборачивать pip в python -c с inject_into_ssl (двойной патч ssl →
   RecursionError); pip ≥24.2 сам доверяет хранилищу Windows.
5. `setup.bat` — переписать: ТОЛЬКО ASCII; кандидаты `py -3`, `python`, `python3`,
   Python314/313/312/311 в %LOCALAPPDATA%\Programs\Python, `C:\Program Files`,
   `C:\` — с проверкой версии фактическим запуском через подпрограмму `:probe`
   (аргументы подпрограммы брать через `%*`, не `%1`); путь хранить БЕЗ кавычек
   (кавычки снимать: `set PY=%CM:"=%`), добавлять только в месте запуска;
   проброс `--selftest`; возврат кода возврата python. Шапка: «только ASCII».
6. `start.bat` — переписать: ТОЛЬКО ASCII (кириллица в UTF-8 ломает разбор cmd);
   проверка .venv; запуск `python -m src.web.app`; шапка «только ASCII».
7. `README.md` — раздел «Проблемы»: нужный python есть, но выбирается старый;
   битый .venv; CERTIFICATE_VERIFY_FAILED (pip и requests); bat только ASCII.

## Проверка (после реализации)
- `python -c "import ast; ast.parse(open('setup/start.py', encoding='utf-8').read())"`
- байты setup.bat/start.bat — 0 не-ASCII (проверка скриптом)
- `setup.bat --selftest`
- повторный `setup.bat` (ветка «venv уже есть»)
- `python -m pytest` (44 теста — без правок тестов)
- `main.py --check-metrics`
- руками: импорт main.py не ломается, truststore-блок молча пропускается если
  пакета нет (эмулировать не устанавливая? — truststore в .venv появится из
  requirements; проверить оба пути: с пакетом и с временно скрытым)

## Вне плана (не делать без спроса)
- баг `metrics/rework.py:28` (KeyError 'key' при --skip-changelog) — отдельное
  решение пользователя
- коммит в git — делает пользователь
