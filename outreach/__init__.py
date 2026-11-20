"""Outreach drafting: the gate between an approved qualified lead and a draft.

The package deliberately contains no client, no credentials and no sender. It
decides whether a draft may exist, builds the draft payload with its
provenance, and stops there: :mod:`outreach.dispatch` is a raising stub, so
"nothing here can send" is a property of the import graph rather than a
promise in a document (AGENTS.md section 10).
"""

from __future__ import annotations

# No imports on purpose: importing ``outreach.ports`` must not pull the gate,
# and importing ``outreach.gate`` must not pull ``outreach.dispatch``.
