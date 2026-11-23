"""Turn an approved message into something a human can send — and nothing more.

Why this module exists
----------------------
The platform has no transport and must not acquire one: :mod:`outreach.dispatch` raises on every
entry point and nothing here imports SMTP, HTTP or a socket. But the operator really does send the
message, from the partnerships mailbox, by hand. That leaves a gap at exactly one seam — the
approved row has to become text a person can paste into Gmail — and this module is that seam, and
only that seam.

Both public functions are pure:

* no I/O, no network, no clock, no configuration read;
* the output is a function of the row alone, so the same row always renders the same bytes;
* importing this module therefore cannot transmit anything, which is the property the tests assert.

Rendering is not sending and it is not recording. Nothing here writes a database row, and the CLI
that calls it says so; the act of sending is recorded separately, afterwards, by a human, through
:func:`fn_record_manual_send` in ``db/migrations/20261123180000_manual_send_record.sql``.

What is deliberately absent from the .eml
-----------------------------------------
``Bcc``, ``Date``, ``Message-ID`` and any tracking header or pixel. Gmail assigns the message id
and the date when the human presses send; a value invented here would be a claim the platform
cannot support, and the whole point of the record is that it can be checked against the mailbox.
"""

from __future__ import annotations

import email.message
import email.policy
from typing import Any, Mapping

from outreach.ports import OutreachError

#: The mailbox the human sends from. It is the partnerships address, not the operator's personal
#: one, because replies have to land somewhere the team reads. Written into every handoff so the
#: operator cannot send from the wrong account by habit.
SENDER_MAILBOX = "eryndexco@gmail.com"

#: Channels this module can render. An in-mail or a contact-form message is not an email and must
#: not be dressed up as one.
EMAIL_CHANNELS = frozenset({"email"})

#: Keys a row may carry the recipient address under, in preference order.
RECIPIENT_KEYS = ("to", "recipient_email", "contact_email", "email")

#: Stated in the rendered block. Rendering is the last step the platform is allowed to take.
HANDOFF_NOTE = (
    f"Send from {SENDER_MAILBOX}, by hand, in Gmail. The platform has no transport: it cannot "
    "send this message, and rendering this block recorded nothing about it."
)


class HandoffRefused(OutreachError):
    """The row is not in a state that may be handed to a human. Names the next action."""


def _value(row: Mapping[str, Any], key: str) -> str:
    """Read a column as trimmed text; ``None`` and non-strings become ``""``."""
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _require_approved(row: Mapping[str, Any]) -> None:
    """Refuse anything a human has not signed off.

    The handoff is the last place the platform touches a message before a real person transmits it,
    so the approval is re-checked here rather than assumed from the caller. Rendering an unapproved
    row would produce text that looks sendable and is not.
    """
    status = _value(row, "approval_status")
    message_id = _value(row, "id") or "<unknown>"
    if status != "approved":
        raise HandoffRefused(
            f"message {message_id} has approval_status {status or 'absent'!r}; only an approved "
            f"message may be handed off. Approve it at gate G3 first: "
            f"`python scripts/outreach_cli.py approve {message_id}`"
        )


def _require_email(row: Mapping[str, Any]) -> None:
    """Refuse a channel that is not email; a LinkedIn in-mail cannot be pasted into Gmail."""
    channel = _value(row, "channel") or "email"
    if channel not in EMAIL_CHANNELS:
        raise HandoffRefused(
            f"message {_value(row, 'id')} is channel {channel!r}; the handoff renders email only, "
            "because the mailbox it names is an email mailbox. Draft an email message instead."
        )


def recipient(row: Mapping[str, Any]) -> str:
    """The To address, from whichever of :data:`RECIPIENT_KEYS` the caller supplied."""
    for key in RECIPIENT_KEYS:
        address = _value(row, key)
        if address:
            return address
    raise HandoffRefused(
        "the row carries no recipient address (looked for "
        f"{', '.join(RECIPIENT_KEYS)}); join contacts.email onto the message before rendering it, "
        "otherwise the block cannot be addressed and the .eml would have no To header"
    )


def _require_content(row: Mapping[str, Any]) -> tuple[str, str]:
    """Subject and body, both non-empty: an email missing either is not worth handing over."""
    subject = _value(row, "subject")
    body = _value(row, "body")
    message_id = _value(row, "id") or "<unknown>"
    if not subject:
        raise HandoffRefused(
            f"message {message_id} has no subject; a handoff without one would be sent blank. "
            "Revoke approval, fix the draft, and approve it again."
        )
    if not body:
        raise HandoffRefused(
            f"message {message_id} has no body; there is nothing to hand off. Revoke approval, "
            "fix the draft, and approve it again."
        )
    return subject, body


def render_email_handoff(row: Mapping[str, Any], *, include_headers: bool = True) -> str:
    """Render the message as a copy-ready block for a human to paste into Gmail.

    ``include_headers=False`` omits the ``To:``/``Subject:`` lines for the case where the operator
    is already inside a composed draft and only wants the body; the send-from note is never
    omitted, because it is the one instruction that keeps the message out of the wrong mailbox.

    Raises :class:`HandoffRefused` for an unapproved row, a non-email channel, a missing recipient,
    subject or body — each with the action that fixes it.
    """
    _require_approved(row)
    _require_email(row)
    address = recipient(row)
    subject, body = _require_content(row)

    lines: list[str] = []
    if include_headers:
        lines.append(f"To: {address}")
        lines.append(f"Subject: {subject}")
        lines.append("")
    lines.append("Body:")
    lines.append("")
    lines.append(body.rstrip())
    lines.append("")
    lines.append(f"[handoff] {HANDOFF_NOTE}")
    return "\n".join(lines) + "\n"


def to_eml(row: Mapping[str, Any]) -> bytes:
    """Render the message as a minimal RFC 822 message a mail client can open.

    No ``Bcc``, no ``Date``, no ``Message-ID``, no attachments, no tracking pixel: each of those
    would either be a claim the platform cannot support or a way to watch the recipient. What is
    left is exactly what the human typed, addressed and sent.

    Raises :class:`HandoffRefused` on the same conditions as :func:`render_email_handoff`.
    """
    _require_approved(row)
    _require_email(row)
    address = recipient(row)
    subject, body = _require_content(row)

    message = email.message.EmailMessage(policy=email.policy.SMTP)
    message["From"] = SENDER_MAILBOX
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body.rstrip() + "\n", subtype="plain", charset="utf-8")
    return message.as_bytes(policy=email.policy.SMTP)


__all__ = [
    "EMAIL_CHANNELS",
    "HANDOFF_NOTE",
    "HandoffRefused",
    "RECIPIENT_KEYS",
    "SENDER_MAILBOX",
    "recipient",
    "render_email_handoff",
    "to_eml",
]
