"""Pure, dependency-free analytical engines for the Eryndex platform.

Packages under ``intelligence/`` must never perform network calls, database
writes, or import third-party libraries: they are deterministic functions of
their inputs so that agent runtimes (Antigravity today, Hermes tomorrow) can
execute them inside supervised pipelines and replay them for audit.
"""
