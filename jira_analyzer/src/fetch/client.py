"""HTTP-клиент Jira REST API v2: авторизация, паузы между запросами,
ретраи с прогрессивной задержкой, уважение Retry-After при 429."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class JiraApiError(RuntimeError):
    """Фатальная ошибка API: после исчерпания ретраев или без права на ретрай."""


class JiraClient:
    def __init__(
        self,
        url: str,
        pat: str | None = None,
        auth: str = "bearer",
        *,
        delay: float = 0.3,
        max_retries: int = 5,
        timeout: float = 30.0,
        backoff_step: float = 1.0,
    ):
        self.base_url = url.rstrip("/")
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.backoff_step = backoff_step
        self.session = requests.Session()

        headers = {"Accept": "application/json"}
        if auth == "bearer":
            if not pat:
                raise ValueError("auth=bearer, но PAT не задан")
            headers["Authorization"] = f"Bearer {pat}"
        elif auth != "none":
            raise ValueError(f"Неизвестный режим авторизации: {auth!r}")
        self.session.headers.update(headers)
        self._last_request = 0.0

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET <base_url>/<path> с выдержкой между запросами и ретраями."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(1, self.max_retries + 2):
            # выдержка между запросами (delay), в ретраях — прогрессивная пауза
            gap = self.delay - (time.monotonic() - self._last_request)
            if gap > 0:
                time.sleep(gap)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                if attempt > self.max_retries:
                    raise JiraApiError(
                        f"Сеть недоступна после {self.max_retries} ретраев: {exc}"
                    ) from exc
                pause = self.backoff_step * attempt  # 1с, 2с, 3с...
                log.warning("Сетевая ошибка (%s), ретрай %d/%d через %.1fс",
                            exc, attempt, self.max_retries, pause)
                time.sleep(pause)
                continue
            self._last_request = time.monotonic()

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in RETRYABLE_STATUS and attempt <= self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                pause = float(retry_after) if retry_after else self.backoff_step * attempt
                log.warning("HTTP %d от %s, ретрай %d/%d через %.1fс",
                            resp.status_code, self.base_url, attempt, self.max_retries, pause)
                time.sleep(pause)
                continue

            hint = {
                401: "проверьте PAT/авторизацию",
                403: "нет прав на этот ресурс",
                404: "проверьте URL или ключ проекта",
            }.get(resp.status_code, "")
            raise JiraApiError(
                f"HTTP {resp.status_code} на GET {url}. {hint} Тело: {resp.text[:300]}"
            )
        raise JiraApiError(f"Не получили успешный ответ от {url}")
