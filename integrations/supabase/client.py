"""PostgREST client under Row Level Security.

Behaviour is deliberately identical to the client that shipped with the
researcher slice (same methods, same error type), now shared by every agent.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("eryndex.supabase.client")

DEFAULT_TIMEOUT_S = 30


class PostgrestError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        code: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(f"PostgREST Error {status_code} ({code}): {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class PostgrestClient:
    """HTTP client for querying PostgREST with a scoped user/agent JWT."""

    def __init__(
        self,
        supabase_url: str,
        anon_key: str,
        access_token: str,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.anon_key = anon_key
        self.access_token = access_token
        self.timeout_s = timeout_s
        self.session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _handle_response(self, response: requests.Response) -> Any:
        if response.status_code in (200, 201, 204):
            if response.status_code == 204 or not response.text.strip():
                return None
            return response.json()

        code = None
        details = None
        message = response.text
        try:
            err_json = response.json()
            code = err_json.get("code")
            message = err_json.get("message", response.text)
            details = err_json.get("details")
        except Exception:  # noqa: BLE001 - error body may not be JSON
            pass

        raise PostgrestError(
            status_code=response.status_code, message=message, code=code, details=details
        )

    def get(self, table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{table}"
        response = self.session.get(
            url, headers=self._headers(), params=params, timeout=self.timeout_s
        )
        return self._handle_response(response) or []

    def post(
        self,
        table: str,
        data: Union[Dict[str, Any], List[Dict[str, Any]]],
        prefer: str = "return=representation",
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{table}"
        response = self.session.post(
            url, headers=self._headers(prefer), json=data, timeout=self.timeout_s
        )
        res = self._handle_response(response)
        if isinstance(res, list):
            return res
        return [res] if res else []

    def patch(
        self,
        table: str,
        data: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        prefer: str = "return=representation",
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{table}"
        response = self.session.patch(
            url, headers=self._headers(prefer), params=params, json=data, timeout=self.timeout_s
        )
        res = self._handle_response(response)
        if isinstance(res, list):
            return res
        return [res] if res else []

    def delete(self, table: str, params: Optional[Dict[str, Any]] = None) -> None:
        url = f"{self.base_url}/{table}"
        response = self.session.delete(url, headers=self._headers(), params=params, timeout=self.timeout_s)
        self._handle_response(response)
