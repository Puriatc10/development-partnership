"""Agent packages for the Eryndex platform.

This file makes ``agents`` a regular package so that test discovery imports
``agents.researcher.*`` exactly once. Without it, pytest prepends
``agents/researcher`` to ``sys.path`` and imports the code a second time as
``researcher.*``, which silently defeats ``unittest.mock.patch`` targets such
as ``agents.researcher.runner.SupabaseAuthClient``.
"""
