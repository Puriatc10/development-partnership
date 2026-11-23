"""The CLI's two new verbs, driven against a fake client.

No database here: these are the *state machine* checks — what the command refuses to ask for, what
it does with a refusal from the function, and what it insists on before reporting success. The real
function is exercised in ``tests/db/test_manual_send_db.py``; this file is about the operator-facing
behaviour around it, which is where a "mark sent" that quietly succeeds without recording anything
would hide.

``export`` and ``mark-sent`` both state that dispatch is disabled; that is asserted rather than
assumed, because it is the promise the whole design rests on.
"""

from __future__ import annotations

import email
import email.parser
import email.policy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent

OPERATOR_ID = "c549b7f9-70a8-4919-b987-aff7d692ea1b"
MESSAGE_ID = "3dedf3c4-b485-4515-8117-29a6dfdbd149"


def _load_cli() -> Any:
    spec = importlib.util.spec_from_file_location(
        "outreach_cli_under_test", REPO / "scripts" / "outreach_cli.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["outreach_cli_under_test"] = module
    spec.loader.exec_module(module)
    return module


class Refusal(RuntimeError):
    """A database refusal as the CLI sees it: a message plus the SQLSTATE in ``code``."""

    def __init__(self, message: str, code: str = "P0001") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class FakeClient:
    """A client that returns canned rows and records every call."""

    def __init__(self, message: dict[str, Any], contact: dict[str, Any] | None = None) -> None:
        self.message = message
        self.contact = contact or {"id": message.get("contact_id"), "email": "ada@example.test"}
        self.calls: list[tuple[str, Any]] = []
        self.rpc_error: Exception | None = None
        self.rpc_result: list[dict[str, Any]] | None = None
        self.after_rpc: dict[str, Any] | None = None

    def get(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append(("get", table))
        row = self.message if table == "outreach_messages" else self.contact
        if table not in {"outreach_messages", "contacts"}:
            raise AssertionError(f"unexpected table {table!r}")
        return [dict(row)]

    def rpc(self, function: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append(("rpc", (function, dict(params or {}))))
        if self.rpc_error is not None:
            raise self.rpc_error
        if self.rpc_result is not None:
            return self.rpc_result
        if self.after_rpc is not None:
            self.message = {**self.message, **self.after_rpc}
        return [dict(self.message)]


class FakeSession:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.user_id = OPERATOR_ID
        self.email = "operator@eryndex.test"
        self.role = "operator"
        self.claims: dict[str, Any] = {
            "sub": OPERATOR_ID,
            "app_metadata": {"user_role": "operator"},
        }


def approved_message() -> dict[str, Any]:
    return {
        "id": MESSAGE_ID,
        "company_id": "e54cab31-c867-4c92-8be2-02a2a6d3fea4",
        "contact_id": "ff9d8644-3e63-4466-902e-f96b46ac8944",
        "channel": "email",
        "approval_status": "approved",
        "sending_status": "draft",
        "sent_at": None,
        "sent_via": None,
        "external_message_id": None,
        "sent_recorded_by": None,
        "subject": "Capacity for your Webflow builds next quarter",
        "body": "Hi Ada,\n\nWe take on the backend layer.\n\nSina",
    }


def _args(module: Any, argv: list[str]) -> Any:
    return module.build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# mark-sent
# ---------------------------------------------------------------------------


def test_mark_sent_records_and_reports_the_stored_state(capsys):
    module = _load_cli()
    client = FakeClient(
        approved_message(),
        contact={"id": "ff9d8644-3e63-4466-902e-f96b46ac8944", "email": "ada@example.test"},
    )
    client.after_rpc = {
        "sending_status": "sent",
        "sent_via": "manual_gmail",
        "external_message_id": "<CAF1@mail.gmail.com>",
        "sent_recorded_by": OPERATOR_ID,
        "sent_at": "2026-11-23T22:41:51+00:00",
    }
    code = module._cmd_mark_sent(
        FakeSession(client),
        _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "<CAF1@mail.gmail.com>"]),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "recorded manual send" in out
    assert "sending_status  : sent" in out
    assert "sent_via        : manual_gmail" in out
    assert "dispatch: disabled" in out
    assert (
        "rpc",
        (
            "fn_record_manual_send",
            {
                "p_message": MESSAGE_ID,
                "p_external_id": "<CAF1@mail.gmail.com>",
                "p_note": None,
            },
        ),
    ) in client.calls


def test_mark_sent_never_asks_the_database_for_a_blank_external_id():
    module = _load_cli()
    client = FakeClient(approved_message())
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_mark_sent(
            FakeSession(client), _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "   "])
        )
    assert "blank" in str(excinfo.value)
    assert "Nothing was recorded" in str(excinfo.value)
    assert client.calls == []


def test_a_database_refusal_becomes_an_operator_message_with_the_next_action():
    module = _load_cli()
    client = FakeClient(approved_message())
    # Shaped like the data-access layer's error (a `code` carrying the SQLSTATE), which is what the
    # CLI keys on — it deliberately does not import that layer, because it never builds a client.
    client.rpc_error = Refusal(
        "Manual Send Refused: outreach message x already has a recorded send (sent_at ...). "
        "A send is recorded once; nothing was rewritten.",
        code="P0001",
    )
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_mark_sent(
            FakeSession(client), _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "abc@x"])
        )
    text = str(excinfo.value)
    assert "already has a recorded send" in text
    assert "[P0001]" in text
    assert "Nothing was recorded" in text


def test_mark_sent_refuses_to_report_success_when_the_row_still_says_draft():
    module = _load_cli()
    client = FakeClient(approved_message())  # the function returns, but nothing changed
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_mark_sent(
            FakeSession(client), _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "abc@x"])
        )
    assert "does not show the manual send" in str(excinfo.value)


def test_mark_sent_refuses_to_attribute_someone_elses_send():
    module = _load_cli()
    client = FakeClient(approved_message())
    client.after_rpc = {
        "sending_status": "sent",
        "sent_via": "manual_gmail",
        "external_message_id": "<CAF1@mail.gmail.com>",
        "sent_recorded_by": "00000000-0000-4000-8000-000000000002",
    }
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_mark_sent(
            FakeSession(client),
            _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "<CAF1@mail.gmail.com>"]),
        )
    assert "not the signed-in operator" in str(excinfo.value)


def test_mark_sent_refuses_when_the_stored_external_id_differs():
    module = _load_cli()
    client = FakeClient(approved_message())
    client.after_rpc = {
        "sending_status": "sent",
        "sent_via": "manual_gmail",
        "external_message_id": "<other@mail.gmail.com>",
        "sent_recorded_by": OPERATOR_ID,
    }
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_mark_sent(
            FakeSession(client),
            _args(module, ["mark-sent", MESSAGE_ID, "--external-id", "<CAF1@mail.gmail.com>"]),
        )
    assert "does not match what was sent" in str(excinfo.value)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_prints_the_block_and_records_nothing(capsys):
    module = _load_cli()
    client = FakeClient(approved_message())
    code = module._cmd_export(FakeSession(client), _args(module, ["export", MESSAGE_ID]))
    out = capsys.readouterr().out
    assert code == 0
    assert "To: ada@example.test" in out
    assert "Subject: Capacity for your Webflow builds next quarter" in out
    assert "recorded: nothing" in out
    assert "dispatch: disabled" in out
    assert [call for call in client.calls if call[0] == "rpc"] == []


def test_export_writes_an_eml_that_a_mail_client_can_parse(tmp_path, capsys):
    module = _load_cli()
    client = FakeClient(approved_message())
    target = tmp_path / "message.eml"
    code = module._cmd_export(
        FakeSession(client), _args(module, ["export", MESSAGE_ID, "--out", str(target)])
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "wrote" in out and "recorded: nothing" in out
    payload = target.read_bytes()
    parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(payload)
    assert parsed["To"] == "ada@example.test"
    assert parsed["Bcc"] is None
    assert "We take on the backend layer." in parsed.get_content()


def test_export_refuses_a_file_it_would_not_recognise_as_eml(tmp_path):
    module = _load_cli()
    client = FakeClient(approved_message())
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_export(
            FakeSession(client),
            _args(module, ["export", MESSAGE_ID, "--out", str(tmp_path / "message.txt")]),
        )
    assert "does not end in .eml" in str(excinfo.value)
    assert not (tmp_path / "message.txt").exists()


def test_export_refuses_to_overwrite_and_says_how(tmp_path):
    module = _load_cli()
    client = FakeClient(approved_message())
    target = tmp_path / "message.eml"
    target.write_bytes(b"a file someone else wrote")
    with pytest.raises(module.OutreachError) as excinfo:
        module._cmd_export(
            FakeSession(client), _args(module, ["export", MESSAGE_ID, "--out", str(target)])
        )
    assert "--force" in str(excinfo.value)
    assert target.read_bytes() == b"a file someone else wrote"


def test_export_refuses_an_unapproved_message_and_writes_nothing(tmp_path):
    module = _load_cli()
    message = approved_message()
    message["approval_status"] = "draft"
    client = FakeClient(message)
    target = tmp_path / "message.eml"
    with pytest.raises(module.HandoffRefused) as excinfo:
        module._cmd_export(
            FakeSession(client), _args(module, ["export", MESSAGE_ID, "--out", str(target)])
        )
    assert "only an approved message" in str(excinfo.value)
    assert not target.exists()


def test_the_export_verb_is_read_only_by_construction():
    """No patch/post/delete call appears in the two new commands."""
    source = (REPO / "scripts" / "outreach_cli.py").read_text(encoding="utf-8")
    start = source.index("def _cmd_export")
    end = source.index("# Parser / entry point")
    body = source[start:end]
    assert ".patch(" not in body
    assert ".post(" not in body
    assert ".delete(" not in body
    assert ".rpc(" in body  # mark-sent is the one writer, and only through the function
