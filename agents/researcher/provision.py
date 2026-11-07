"""
Identity Provisioning Tool for Supabase Auth.

Executes administrative identity provisioning outside the agent execution context.
Assigns verified app_metadata claims that cannot be forged or modified by clients.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("provision")


def provision_user(
    supabase_url: str,
    service_role_key: str,
    email: str,
    password: str,
    app_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Creates or updates an Auth user with trusted app_metadata using the Admin API.
    """
    admin_headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }

    # Check if user already exists
    list_url = f"{supabase_url.rstrip('/')}/auth/v1/admin/users"
    res = requests.get(list_url, headers=admin_headers, timeout=30)
    if res.status_code != 200:
        raise RuntimeError(f"Admin list users failed ({res.status_code}): {res.text}")

    existing_user: Optional[Dict[str, Any]] = None
    for u in res.json().get("users", []):
        if u.get("email") == email:
            existing_user = u
            break

    if existing_user:
        user_id = existing_user["id"]
        logger.info("User '%s' already exists (UUID: %s). Updating app_metadata...", email, user_id)
        update_url = f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
        update_payload = {"app_metadata": app_metadata}
        up_res = requests.put(update_url, headers=admin_headers, json=update_payload, timeout=30)
        if up_res.status_code not in (200, 201):
            raise RuntimeError(f"Admin update user failed ({up_res.status_code}): {up_res.text}")
        return up_res.json()
    else:
        logger.info("Creating new user '%s'...", email)
        create_payload = {
            "email": email,
            "password": password,
            "email_confirm": True,
            "app_metadata": app_metadata,
        }
        cr_res = requests.post(list_url, headers=admin_headers, json=create_payload, timeout=30)
        if cr_res.status_code not in (200, 201):
            raise RuntimeError(f"Admin create user failed ({cr_res.status_code}): {cr_res.text}")
        return cr_res.json()


def main():
    parser = argparse.ArgumentParser(description="Provision Supabase Auth Identities with App Metadata")
    parser.add_argument("--url", default=os.environ.get("SUPABASE_URL"), help="Supabase project URL")
    parser.add_argument("--service-key", default=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"), help="Supabase service role key")
    parser.add_argument("--role", choices=["researcher", "operator"], default="researcher", help="Role to provision")
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument("--password", required=True, help="User password")
    parser.add_argument("--agent-id", default="hermes-researcher-v1", help="Agent identity string (for researcher)")

    args = parser.parse_args()

    if not args.url or not args.service_key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be provided via flags or environment")
        sys.exit(1)

    if args.role == "researcher":
        app_meta = {
            "user_role": "agent",
            "subagent_type": "researcher",
            "agent_id": args.agent_id,
        }
    else:
        app_meta = {
            "user_role": "operator",
            "subagent_type": None,
            "agent_id": None,
        }

    user = provision_user(
        supabase_url=args.url,
        service_role_key=args.service_key,
        email=args.email,
        password=args.password,
        app_metadata=app_meta,
    )
    logger.info("Successfully provisioned identity '%s' (UUID: %s) with app_metadata: %s",
                args.email, user.get("id"), json.dumps(app_meta))


if __name__ == "__main__":
    main()
