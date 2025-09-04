# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .constants import RETRY_STATUS_CODES, SESSION_TIMEOUT


class AmoHttpError(Exception):
    """Ошибки, которые должны ретраиться."""


class HttpClient:
    def __init__(self, token: str) -> None:
        self._s = requests.Session()
        self._s.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    @retry(
        reraise=True,
        retry=retry_if_exception_type((requests.RequestException,
                                       AmoHttpError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
    )
    def request(
        self,
        method: str,
        url: str,
        json_body: Optional[Any] = None,
    ) -> requests.Response:
        resp = self._s.request(
            method=method,
            url=url,
            json=json_body,
            timeout=SESSION_TIMEOUT,
        )
        if resp.status_code in RETRY_STATUS_CODES:
            snippet = (resp.text or "")[:300]
            raise AmoHttpError(f"{method} {url} -> {resp.status_code}: "
                               f"{snippet}")
        resp.raise_for_status()
        return resp

    def get_json(self, url: str) -> Dict[str, Any]:
        return self.request("GET", url).json()

    def patch_json(self, url: str, payload: Any) -> None:
        self.request("PATCH", url, json_body=payload)
