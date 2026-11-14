"""Dispatch — the surface that must stay unreachable.

Gate G4 (``orchestration/workflow.py``) is a *separate human action*: an
approved message may still not leave the building until an operator authorizes
that specific dispatch. This package implements the drafting half of Workflow 4
only. Dispatch is therefore not "not called yet"; it is not callable:

* nothing here imports an HTTP client, SMTP, a socket or a credential;
* the constructor and every method raise :class:`DispatchDisabled`;
* :data:`DISPATCH_AVAILABLE` is ``False`` so a caller can gate a UI on it;
* ``outreach.gate`` does not import this module at all, so creating a draft
  cannot reach it even by accident.

When dispatch is eventually built it belongs behind G4, behind an explicit
operator action, with its own audit trail — and it will replace this module
rather than grow inside it.
"""

from __future__ import annotations

from typing import Any

#: Import-time fact, not a feature flag to be flipped by configuration. There is
#: no environment variable that turns dispatch on.
DISPATCH_AVAILABLE: bool = False

#: Stated once, so the refusal is identical wherever it is raised.
REFUSAL = (
    "Outreach dispatch is disabled by construction: this build drafts messages only. "
    "Sending requires human gate G4 (a separate operator action) and a delivery "
    "integration that does not exist yet."
)


class DispatchDisabled(RuntimeError):
    """Raised by every dispatch entry point. Never caught inside this package."""


class DispatchChannel:
    """The dispatch surface.

    Present so that an agent asking "can you send this?" gets a loud, typed
    refusal instead of an ``AttributeError`` it might work around — and so that
    the absence is testable rather than merely documented.
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        raise DispatchDisabled(REFUSAL)

    def send(self, *_: Any, **__: Any) -> None:
        raise DispatchDisabled(REFUSAL)

    def queue(self, *_: Any, **__: Any) -> None:
        raise DispatchDisabled(REFUSAL)

    def schedule(self, *_: Any, **__: Any) -> None:
        raise DispatchDisabled(REFUSAL)

    def credentials(self) -> None:
        raise DispatchDisabled(REFUSAL)


def dispatch(*_: Any, **__: Any) -> None:
    """Refuse to dispatch anything."""
    raise DispatchDisabled(REFUSAL)


def queue_for_dispatch(*_: Any, **__: Any) -> None:
    """Refuse to move a message to ``sending_status='queued'``.

    Included because ``queued`` is the first state in
    ``outreach_sending_status_type`` that implies transmission intent; a drafting
    stage that can queue has already left its mandate.
    """
    raise DispatchDisabled(REFUSAL)
