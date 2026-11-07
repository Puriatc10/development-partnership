import os
from dataclasses import dataclass
from typing import Optional


def load_dotenv(dotenv_path: Optional[str] = None) -> None:
    """Minimal dependency-free .env file loader."""
    if not dotenv_path:
        dotenv_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k not in os.environ:
                os.environ[k] = v


@dataclass
class Config:
    project_ref: str
    supabase_url: str
    anon_key: str
    researcher_email: str
    researcher_password: str
    researcher_agent_id: str

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        project_ref = os.environ.get("SUPABASE_PROJECT_REF", "vyfqmrdznmocnmzyjjcj")
        supabase_url = os.environ.get("SUPABASE_URL", f"https://{project_ref}.supabase.co")
        anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        researcher_email = os.environ.get("RESEARCHER_EMAIL", "researcher.v1@agents.internal")
        researcher_password = os.environ.get("RESEARCHER_PASSWORD", "")
        researcher_agent_id = os.environ.get("RESEARCHER_AGENT_ID", "hermes-researcher-v1")

        return cls(
            project_ref=project_ref,
            supabase_url=supabase_url,
            anon_key=anon_key,
            researcher_email=researcher_email,
            researcher_password=researcher_password,
            researcher_agent_id=researcher_agent_id,
        )
