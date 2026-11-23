"""Agent packages for the Eryndex platform.

The researcher vertical slice that lived under ``agents/researcher`` was retired with the move
to self-hosted PostgreSQL: it spoke PostgREST and GoTrue directly, and it never researched
anything — it persisted a hand-authored payload. Its historical output is kept under
``agents/researcher/runs/``; operational surfaces now live in ``operations/`` and the research
executor will be rebuilt on ``integrations/postgres``.

This file keeps ``agents`` a regular package so imports stay unambiguous.
"""
