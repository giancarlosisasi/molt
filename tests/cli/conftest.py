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

import importlib
from typing import Final

import pytest
from tests.cli.fake_cli import FakeGit, RecordingConsole, ScriptedPrompts

# ======================================================================================
# Placeholder gate -- keeps a per-command suite skipped until its implementation lands
# ======================================================================================

#: Test module stem -> the ``molt.commands`` module it drives.
#:
#: Each of these suites guards itself with ``pytest.importorskip("molt.commands.<name>")``, on the
#: assumption that "the module exists" means "the command is implemented". The
#: ``adopt-typer-cli-shell`` change broke that assumption: ``tests/cli/test_cli.py`` drives the
#: shell by monkeypatching ``molt.commands.<name>.run``, so every one of those modules had to exist
#: -- as a placeholder -- before a single command was written. Without this hook, landing the shell
#: would have turned ~156 not-yet-written command tests from skipped to failing.
#:
#: The gate restores the original intent by asking the *module* instead of the import system:
#: a module that still declares ``__molt_placeholder__`` is not an implementation. Delete that flag
#: when the command lands and its suite lights up on the next run -- no change is needed here.
_PLACEHOLDER_SUITES: Final[dict[str, str]] = {
    "test_add": "molt.commands.add",
    "test_git_tag": "molt.commands.git_tag",
    "test_init": "molt.commands.init",
    "test_status": "molt.commands.status",
    "test_version": "molt.commands.version",
}


def _is_placeholder(module_name: str) -> bool:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return False  # absent: the suite's own importorskip already handles it
    return bool(getattr(module, "__molt_placeholder__", False))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the per-command suites whose implementation module is still a shell placeholder."""
    del config
    cache: dict[str, bool] = {}
    for item in items:
        path = getattr(item, "path", None)
        if path is None or path.parent.name != "cli":
            continue
        target = _PLACEHOLDER_SUITES.get(path.stem)
        if target is None:
            continue
        if target not in cache:
            cache[target] = _is_placeholder(target)
        if cache[target]:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        f"{target} is still a CLI-shell placeholder "
                        f"(remove __molt_placeholder__ when the command lands)"
                    )
                )
            )


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
