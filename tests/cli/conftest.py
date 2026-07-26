"""Fixtures for the CLI conformance suite (groups 5 and 6).

Thin wrappers over the doubles in :mod:`tests.cli.fake_cli`. Like the root ``conftest.py``, none
of these import a ``molt`` product module, so the package collects green while ``molt.cli`` is a
stub and ``molt.commands`` does not exist.

The doubles stand in for the seams the accepted ``adopt-typer-cli-shell`` change defines
(``openspec/changes/adopt-typer-cli-shell/``): ``molt.ui.console.Console`` and
``molt.ui.prompts.Prompts``. They are **structural** stand-ins -- duck-typed, never subclassing
the (not yet existing) protocols -- so nothing here needs a guard.
"""

from __future__ import annotations

import pytest
from tests.cli.fake_cli import FakeGit, RecordingConsole, ScriptedPrompts


@pytest.fixture
def console() -> RecordingConsole:
    """A :class:`RecordingConsole` capturing info/success/warn/error/note, VT-stripped.

    Replaces the reference's ``mockedLogger`` + ``silenceLogsInBlock``. Inject it wherever the
    command under test takes a console; assert with ``console.errors``, ``console.warnings`` or
    ``console.contains(...)``.
    """
    return RecordingConsole()


@pytest.fixture
def prompts() -> ScriptedPrompts:
    """An empty :class:`ScriptedPrompts`.

    An empty script means *any* prompt raises ``PromptExhausted``, which is what the
    "should not prompt" rows want (``add`` rows 6/7/9: ``--message`` skips the summary prompt).
    Build a scripted one inline when a flow does need answers::

        prompts = ScriptedPrompts(multiselect=[["pkg-a"]], select=["patch"], text=["summary"])
    """
    return ScriptedPrompts()


@pytest.fixture
def fake_git() -> FakeGit:
    """A recording :class:`FakeGit` with no pre-existing tags.

    Replaces ``vi.mock("@changesets/git")``. Seed tags for the idempotent-tagging rows with
    ``FakeGit(existing_tags=["pkg-a@1.0.0"])`` rather than mutating this fixture.
    """
    return FakeGit()
