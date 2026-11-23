"""The handoff renderer: pure, copy-ready, and incapable of sending.

These are unit tests around one property that matters more than the formatting: rendering an
approved message must be a pure function of the row. No network, no SMTP, no clock, no file write —
so "the platform cannot transmit" survives the addition of a feature that exists to get a message
out. The import-surface assertions at the bottom are the mechanical version of that claim.
"""

from __future__ import annotations

import ast
import email
import email.message
import email.parser
import email.policy
from pathlib import Path

import pytest

from outreach.handoff import (
    HANDOFF_NOTE,
    SENDER_MAILBOX,
    HandoffRefused,
    recipient,
    render_email_handoff,
    to_eml,
)

REPO = Path(__file__).resolve().parent.parent

APPROVED = {
    "id": "3dedf3c4-b485-4515-8117-29a6dfdbd149",
    "company_id": "e54cab31-c867-4c92-8be2-02a2a6d3fea4",
    "contact_id": "ff9d8644-3e63-4466-902e-f96b46ac8944",
    "channel": "email",
    "approval_status": "approved",
    "sending_status": "draft",
    "sent_at": None,
    "subject": "Capacity for your Webflow builds next quarter",
    "body": (
        "Hi Ada,\n\nYou ship a lot of Webflow sites and the backend layer is usually where it\n"
        "slows down. We take that layer on.\n\nSina"
    ),
}


def approved(**overrides: object) -> dict[str, object]:
    row = dict(APPROVED)
    row["to"] = "ada@example.test"
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# render_email_handoff
# ---------------------------------------------------------------------------


def test_the_block_carries_headers_body_and_the_send_from_note():
    block = render_email_handoff(approved())
    assert block.startswith("To: ada@example.test\n")
    assert f"Subject: {APPROVED['subject']}\n" in block
    assert "Body:\n" in block
    assert "Hi Ada," in block
    assert SENDER_MAILBOX in block
    assert "by hand, in Gmail" in block
    assert block.rstrip().endswith("recorded nothing about it.")


def test_the_block_says_the_platform_cannot_send():
    block = render_email_handoff(approved())
    assert "The platform has no transport: it cannot send this message" in block
    assert "[handoff]" in block


def test_the_body_is_reproduced_verbatim_inside_the_block():
    block = render_email_handoff(approved())
    assert APPROVED["body"] in block


def test_headers_can_be_left_out_for_a_draft_already_addressed():
    block = render_email_handoff(approved(), include_headers=False)
    assert "To:" not in block.split("\n")[0]
    assert "Subject:" not in block
    assert "Hi Ada," in block
    # The one instruction that must never be dropped, because it names the mailbox.
    assert HANDOFF_NOTE in block


def test_an_unapproved_row_is_refused_and_the_message_names_the_next_action():
    for status in ("draft", "pending_human_approval", "rejected", None):
        with pytest.raises(HandoffRefused) as excinfo:
            render_email_handoff(approved(approval_status=status))
        text = str(excinfo.value)
        assert "only an approved message" in text
        assert "outreach_cli.py approve" in text
        assert APPROVED["id"] in text


def test_a_non_email_channel_is_refused():
    with pytest.raises(HandoffRefused) as excinfo:
        render_email_handoff(approved(channel="linkedin_inmail"))
    assert "linkedin_inmail" in str(excinfo.value)
    assert "email only" in str(excinfo.value)


def test_a_row_without_a_recipient_is_refused():
    row = approved()
    row.pop("to")
    with pytest.raises(HandoffRefused) as excinfo:
        render_email_handoff(row)
    assert "no recipient address" in str(excinfo.value)


def test_a_blank_subject_or_body_is_refused():
    with pytest.raises(HandoffRefused) as excinfo:
        render_email_handoff(approved(subject="   "))
    assert "no subject" in str(excinfo.value)

    with pytest.raises(HandoffRefused) as excinfo:
        render_email_handoff(approved(body=""))
    assert "no body" in str(excinfo.value)


@pytest.mark.parametrize("key", ["to", "recipient_email", "contact_email", "email"])
def test_the_recipient_may_arrive_under_any_supported_key(key):
    row = dict(APPROVED)
    row[key] = "ada@example.test"
    assert recipient(row) == "ada@example.test"
    assert render_email_handoff(row).startswith("To: ada@example.test\n")


def test_a_missing_recipient_key_is_an_error_not_an_empty_header():
    with pytest.raises(HandoffRefused):
        recipient(dict(APPROVED))


# ---------------------------------------------------------------------------
# to_eml
# ---------------------------------------------------------------------------


def _parsed(row: dict[str, object]) -> email.message.EmailMessage:
    """Parse the .eml the way a mail client would, with its CRLF line endings normalised."""
    return email.parser.BytesParser(policy=email.policy.default).parsebytes(to_eml(row))


def test_the_eml_parses_and_carries_the_three_headers():
    parsed = _parsed(approved())
    assert parsed["From"] == SENDER_MAILBOX
    assert parsed["To"] == "ada@example.test"
    assert parsed["Subject"] == APPROVED["subject"]
    assert parsed.get_content().replace("\r\n", "\n").rstrip() == APPROVED["body"].rstrip()


def test_the_eml_has_no_bcc_no_tracking_and_no_attachment():
    parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(to_eml(approved()))
    assert parsed["Bcc"] is None
    assert parsed["Return-Receipt-To"] is None
    assert parsed["Disposition-Notification-To"] is None
    assert not parsed.is_multipart()
    assert parsed.get_content_type() == "text/plain"


def test_the_eml_does_not_invent_a_message_id_or_a_date():
    """Gmail assigns both when the human presses send; a guess here would be unverifiable."""
    parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(to_eml(approved()))
    assert parsed["Message-ID"] is None
    assert parsed["Date"] is None


def test_the_eml_is_deterministic():
    assert to_eml(approved()) == to_eml(approved())


def test_a_non_ascii_subject_and_body_round_trip():
    row = approved(subject="Capacité — votre studio", body="Bonjour Ada,\n\nÇa vous dit ?\n")
    parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(to_eml(row))
    assert parsed["Subject"] == "Capacité — votre studio"
    assert "Ça vous dit" in parsed.get_content()


def test_the_eml_refuses_an_unapproved_row():
    with pytest.raises(HandoffRefused):
        to_eml(approved(approval_status="draft"))


# ---------------------------------------------------------------------------
# The module cannot transmit anything
# ---------------------------------------------------------------------------


FORBIDDEN_MODULES = {
    "smtplib",
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "ssl",
    "subprocess",
    "webbrowser",
    "outlook",
    "sendgrid",
    "resend",
}


def test_the_handoff_module_imports_nothing_that_can_send():
    tree = ast.parse((REPO / "outreach" / "handoff.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_MODULES), sorted(imported & FORBIDDEN_MODULES)


def test_the_handoff_module_performs_no_io():
    """No file, no network, no subprocess: only string and bytes construction."""
    tree = ast.parse((REPO / "outreach" / "handoff.py").read_text(encoding="utf-8"))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (
        calls & {"open", "write", "write_text", "write_bytes", "urlopen", "connect", "send"}
    )
