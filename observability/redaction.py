"""The codebase's credential-scrubbing rules, imported rather than restated.

Why the rules live in ``outreach/gate.py``
------------------------------------------
They were compiled there first, for a different reason: a rejected draft logs
the provider exception, and an unwrapped PostgREST/httpx message carries the
request URL — which carries the API key. ``_describe_exception`` therefore
scrubs ``apikey=...``/``password: ...`` shaped fragments (``_CREDENTIAL_RE``),
``Authorization: Bearer <jwt>`` headers (``_BEARER_RE``) and control characters
(``_CONTROL_RE``) before the text reaches a log or an operator.

That is the only redaction in the codebase, and it stays the only one. This
module imports those three compiled patterns and applies them in the same
order. A second copy of a security rule is not redundancy, it is a second
answer to the same question — and the one nobody re-reads is wrong.

Why the import is deferred
--------------------------
``outreach/gate.py`` imports the IPP rubric, so importing it eagerly would make
every importer of the event emitter pay for the scoring layer. The patterns are
compiled at import of ``outreach.gate`` (module level, no I/O), so the first
``redact`` call resolves them once and caches them for the process.

Additions, and why they are not duplicates
------------------------------------------
``_CREDENTIAL_RE`` scrubs a value that carries its own key, e.g.
``password=hunter2``. A structured field does not: ``structured_event(name,
password="hunter2")`` sends the value alone, where no pattern can see a secret.
:func:`redact_fields` therefore masks a value outright when the *field name*
says it is a credential. That is a name check, not a second regex.

The other addition is a shape the gate never meets: a connection string keeps
its password in the userinfo (``postgresql://agent:pw@host/db``), where a
``key=value`` pattern cannot see it. ``scripts/research_run.py`` prints
connection errors, so :data:`_DSN_CREDENTIALS_RE` replaces that password with
``***`` and keeps the role and host, which are the parts an operator needs. It
is one new rule for one new shape — nothing the gate already handles is
re-implemented.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

#: Truncation applied to a redacted value, matching ``_describe_exception``'s cap.
REDACTION_LIMIT = 300

#: Field names whose value must never be emitted, matched case-insensitively by
#: substring after ``-`` is folded to ``_``. Deliberately not a regex: the list
#: is short, and a substring test cannot silently gain a backtracking failure.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

#: ``("bearer", "credential", "control")`` once resolved; see :func:`_rules`.
_RULES: tuple[Any, Any, Any] | None = None

#: ``scheme://user:password@host`` — a connection string carries its credential in
#: the *userinfo*, where no ``key=value`` pattern can see it, and an operator
#: running ``scripts/research_run.py`` reads connection errors. The gate never
#: meets this shape (it scrubs drafts, not DSNs), so this is an addition, not a
#: copy: the role survives because it names which identity failed; the password
#: does not.
_DSN_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):[^@/\s]+@"
)


def _rules() -> tuple[Any, Any, Any]:
    """The three compiled patterns owned by ``outreach.gate``, resolved once."""
    global _RULES
    if _RULES is None:
        from outreach.gate import _BEARER_RE, _CONTROL_RE, _CREDENTIAL_RE

        _RULES = (_BEARER_RE, _CREDENTIAL_RE, _CONTROL_RE)
    return _RULES


def _is_sensitive_key(key: str) -> bool:
    folded = key.casefold().replace("-", "_")
    return any(part in folded for part in _SENSITIVE_KEY_PARTS)


def redact(value: Any, *, limit: int = REDACTION_LIMIT) -> str:
    """``str(value)`` with credentials and control characters removed.

    The order is the one ``outreach.gate._describe_exception`` uses, because the
    bearer pattern has to run before the credential pattern rewrites the line
    into ``authorization=***``. Newlines survive here (JSON encoding escapes
    them); the cap keeps one hostile value from filling the log.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - an object whose __str__ raises must not break a log line
        return "<unprintable>"

    bearer, credential, control = _rules()
    text = bearer.sub("bearer=***", text or "")
    text = _DSN_CREDENTIALS_RE.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:***@", text
    )
    text = credential.sub(lambda match: f"{match.group(1)}=***", text)
    text = control.sub(" ", text).strip()
    if limit is not None and len(text) > limit:
        text = text[:limit] + "..."
    return text


def redact_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Redact every value in ``fields``, recursing into mappings and sequences.

    Scalars that cannot hold a secret (``None``/``bool``/``int``/``float``) pass
    through unchanged, so a count stays a count and stays machine-readable.
    """
    return {str(key): _redact_value(key, value) for key, value in fields.items()}


def _redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return "***"
    if isinstance(value, Mapping):
        return redact_fields(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(key, item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(value)


__all__ = ["REDACTION_LIMIT", "redact", "redact_fields"]
