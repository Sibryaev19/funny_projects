"""Фреймворк компонентных метрик.

Метрика = класс-наследник BaseMetric (см. src/components/base.py), лежащий в
metrics/ — проект подхватывает его сам (src/components/registry.py).
Результат метрики — список блоков (src/components/blocks.py), которые рендерят
web-интерфейс и HTML-отчёт. Полная инструкция для человека/нейросети:
metrics/README.md.
"""
