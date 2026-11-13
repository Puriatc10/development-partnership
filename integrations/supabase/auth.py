"""Supabase GoTrue authentication with claim verification.

Identity is the only thing standing between an agent and the CRM, so claim
verification is explicit and typed by subagent role rather than hard-coded to
the researcher.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("eryndex.supabase.auth")


class SupabaseAuthClient:
    """GoTrue client using only the public anon key plus user credentials."""

    def __init__(self, supabase_url: str, anon_key: str) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.anon_key = anon_key
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

    def sign_in_with_password(self, email: str, password: str) -> Dict[str, Any]:
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        headers = {"apikey": self.anon_key, "Content-Type": "application/json"}
        response = self.session.post(
            url, headers=headers, json={"email": email, "password": password}, timeout=30
        )
        if response.status_code != 200:
            raise PermissionError(
                f"Supabase Auth sign-in failed (status {response.status_code}): {response.text}"
            )

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise ValueError("No access_token returned in auth response")

        return {
            "access_token": access_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": data.get("expires_in"),
            "user": data.get("user"),
            "claims": self.decode_jwt_payload(access_token),
        }

    @staticmethod
    def decode_jwt_payload(jwt_token: str) -> Dict[str, Any]:
        """Decode the payload segment; signature verification is server-side."""
        parts = jwt_token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))

    def verify_agent_claims(
        self,
        claims: Dict[str, Any],
        expected_agent_id: str,
        expected_subagent_type: Optional[str] = None,
    ) -> None:
        """Require role=authenticated and app_metadata carrying the agent identity."""
        sub = claims.get("sub")
        if not sub:
            raise ValueError("Missing 'sub' claim in JWT")
        if claims.get("role") != "authenticated":
            raise ValueError(f"Expected role 'authenticated', got '{claims.get('role')}'")

        app_meta = claims.get("app_metadata") or {}
        user_role = app_meta.get("user_role")
        subagent_type = app_meta.get("subagent_type")
        agent_id = app_meta.get("agent_id")

        if user_role != "agent":
            raise ValueError(
                f"Invalid claim app_metadata.user_role: expected 'agent', got '{user_role}'"
            )
        if expected_subagent_type and subagent_type != expected_subagent_type:
            raise ValueError(
                "Invalid claim app_metadata.subagent_type: "
                f"expected '{expected_subagent_type}', got '{subagent_type}'"
            )
        if agent_id != expected_agent_id:
            raise ValueError(
                f"Invalid claim app_metadata.agent_id: expected '{expected_agent_id}', got '{agent_id}'"
            )
        logger.info("Agent claims verified: sub=%s, agent_id=%s", sub, agent_id)

    def verify_researcher_claims(self, claims: Dict[str, Any], expected_agent_id: str) -> None:
        """Backwards-compatible wrapper for the researcher slice."""
        self.verify_agent_claims(claims, expected_agent_id, expected_subagent_type="researcher")
