import base64
import json
import logging
import requests
from typing import Any, Dict, Optional

logger = logging.getLogger("researcher.auth")


class SupabaseAuthClient:
    """
    Supabase GoTrue client for authenticating agents and operators.
    
    Uses only public anon credentials and user passwords to issue genuine,
    cryptographically signed JWTs carrying verified app_metadata.
    """

    def __init__(self, supabase_url: str, anon_key: str):
        self.supabase_url = supabase_url.rstrip("/")
        self.anon_key = anon_key
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

    def sign_in_with_password(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate via POST /auth/v1/token?grant_type=password.
        Returns the auth payload containing access_token, user info, and decoded claims.
        """
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        headers = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        payload = {
            "email": email,
            "password": password,
        }

        response = self.session.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code != 200:
            raise PermissionError(
                f"Supabase Auth sign-in failed (status {response.status_code}): {response.text}"
            )

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise ValueError("No access_token returned in auth response")

        claims = self.decode_jwt_payload(access_token)
        return {
            "access_token": access_token,
            "token_type": data.get("token_type", "bearer"),
            "expires_in": data.get("expires_in"),
            "user": data.get("user"),
            "claims": claims,
        }

    @staticmethod
    def decode_jwt_payload(jwt_token: str) -> Dict[str, Any]:
        """
        Decodes the payload segment of a JWT without verifying signature
        (signature verification is handled server-side by PostgreSQL / PostgREST).
        """
        parts = jwt_token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload_b64 = parts[1]
        # Pad with = if needed
        payload_b64 += "=" * (-len(payload_b64) % 4)
        decoded_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(decoded_bytes.decode("utf-8"))

    def verify_researcher_claims(self, claims: Dict[str, Any], expected_agent_id: str) -> None:
        """
        Verifies that claims conform strictly to Section 3 requirements:
        - sub: valid UUID
        - role: 'authenticated'
        - app_metadata.user_role == 'agent'
        - app_metadata.subagent_type == 'researcher'
        - app_metadata.agent_id == expected_agent_id
        """
        sub = claims.get("sub")
        if not sub:
            raise ValueError("Missing 'sub' claim in JWT")

        role = claims.get("role")
        if role != "authenticated":
            raise ValueError(f"Expected role 'authenticated', got '{role}'")

        app_meta = claims.get("app_metadata", {})
        user_role = app_meta.get("user_role")
        subagent_type = app_meta.get("subagent_type")
        agent_id = app_meta.get("agent_id")

        if user_role != "agent":
            raise ValueError(f"Invalid claim app_metadata.user_role: expected 'agent', got '{user_role}'")
        if subagent_type != "researcher":
            raise ValueError(f"Invalid claim app_metadata.subagent_type: expected 'researcher', got '{subagent_type}'")
        if agent_id != expected_agent_id:
            raise ValueError(f"Invalid claim app_metadata.agent_id: expected '{expected_agent_id}', got '{agent_id}'")

        logger.info("Researcher claims successfully verified: sub=%s, agent_id=%s", sub, agent_id)
