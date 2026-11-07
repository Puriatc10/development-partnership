import logging
import requests
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("researcher.client")


class PostgrestError(Exception):
    def __init__(self, status_code: int, message: str, code: Optional[str] = None, details: Optional[str] = None):
        super().__init__(f"PostgREST Error {status_code} ({code}): {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class PostgrestClient:
    """
    HTTP client for querying PostgREST under Row Level Security.
    Transmits user/agent JWT in the Authorization header.
    """

    def __init__(self, supabase_url: str, anon_key: str, access_token: str):
        self.base_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.anon_key = anon_key
        self.access_token = access_token
        self.session = requests.Session()
        retries = requests.adapters.Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            raise_on_status=False,
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retries)
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
        except Exception:
            pass

        raise PostgrestError(
            status_code=response.status_code,
            message=message,
            code=code,
            details=details,
        )

    def get(self, table: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{table}"
        response = self.session.get(url, headers=self._headers(), params=params, timeout=30)
        return self._handle_response(response) or []

    def post(
        self,
        table: str,
        data: Union[Dict[str, Any], List[Dict[str, Any]]],
        prefer: str = "return=representation",
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{table}"
        response = self.session.post(url, headers=self._headers(prefer), json=data, timeout=30)
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
        response = self.session.patch(url, headers=self._headers(prefer), params=params, json=data, timeout=30)
        res = self._handle_response(response)
        if isinstance(res, list):
            return res
        return [res] if res else []

    def delete(self, table: str, params: Optional[Dict[str, Any]] = None) -> None:
        url = f"{self.base_url}/{table}"
        response = self.session.delete(url, headers=self._headers(), params=params, timeout=30)
        self._handle_response(response)
