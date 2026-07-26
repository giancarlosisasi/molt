"""Conformance tests for the exception hierarchy.

Ports ``packages/errors/src/index.test.ts`` (test-suite doc 08, errors group: 2 Port) plus the
whole hierarchy the source file defines but does not test (``GitError``, ``ExitError``,
``PreExitButNotInPreModeError``, ``PreEnterButInPreModeError``, ``InternalError`` --
``errors/src/index.ts:1-33``). ``InternalError`` guards the fixed/linked glob regression
(research README section 3.4).

Design note -- ``molt.errors`` does not exist yet (build step 9, TDD target); the whole module is
guarded by ``importorskip`` so the suite stays green until it lands. Intended surface::

    GitError(code: int, message: str)          # str == "{message}, exit code: {code}"; .code attr
    ExitError(code: int)                        # str == "The process exited with code: {code}"
    PreExitButNotInPreModeError()               # "pre mode cannot be exited when not in pre mode"
    PreEnterButInPreModeError()                 # "pre mode cannot be entered when in pre mode"
    InternalError(message: str)                 # message passthrough
    MoltParseError(message, *, path=None, line=None)   # changeset frontmatter/grammar failures

``MoltParseError`` is molt-native (research doc 02 section 12.6) rather than ported; the changeset
grammar tests in ``tests/changeset/test_parse.py`` assume it, so it is listed here to keep the
intended surface in one place. Its cases are asserted there, not in this file.

The npm->PyPI literal adaptation the task calls out does not bite this file: none of these
messages carry an npm-specific literal, so they port 1:1.
"""

from __future__ import annotations

import re

import pytest

errors = pytest.importorskip(
    "molt.errors", reason="build step 9 -- molt.errors not yet implemented (TDD target)"
)


@pytest.mark.unit
def test_git_error_is_an_instance_of_exception() -> None:
    """errors/src/index.test.ts:10-12 -- ``new GitError(1, ...)`` isa ``Error``."""
    assert isinstance(errors.GitError(1, "Operation failed"), Exception)


@pytest.mark.unit
def test_git_error_includes_message_and_exit_code_in_string() -> None:
    """errors/src/index.test.ts:13-15 -- ``toString()`` matches ``/(Operation failed).*(1)/``.

    Upstream format is ``"{message}, exit code: {code}"`` (``errors/src/index.ts:4``); the ``.code``
    attribute is preserved so callers can branch on the git exit status.
    """
    err = errors.GitError(1, "Operation failed")
    assert re.search(r"(Operation failed).*(1)", str(err)) is not None
    assert err.code == 1


@pytest.mark.unit
def test_exit_error_carries_its_code() -> None:
    """``errors/src/index.ts:9-16`` -- ``ExitError`` reports the process exit code."""
    err = errors.ExitError(2)
    assert err.code == 2
    assert isinstance(err, Exception)
    assert "2" in str(err)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: errors.PreExitButNotInPreModeError(),
            "pre mode cannot be exited when not in pre mode",
        ),
        (
            lambda: errors.PreEnterButInPreModeError(),
            "pre mode cannot be entered when in pre mode",
        ),
    ],
)
def test_pre_mode_errors_carry_their_fixed_messages(factory, message: str) -> None:
    """``errors/src/index.ts:18-27`` -- the two pre-mode guard errors have fixed strings."""
    err = factory()
    assert isinstance(err, Exception)
    assert str(err) == message


@pytest.mark.unit
def test_internal_error_passes_its_message_through() -> None:
    """``errors/src/index.ts:29-33`` -- ``InternalError`` guards the fixed/linked glob regression
    (research README section 3.4); the message it is raised with must survive verbatim.
    """
    err = errors.InternalError("could not match fixed constraint")
    assert isinstance(err, Exception)
    assert str(err) == "could not match fixed constraint"
