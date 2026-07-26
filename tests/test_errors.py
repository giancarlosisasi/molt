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
    MoltKeyPathError(message: str)              # edit_toml's missing-key-path / splice failures
    MoltError                                   # common base of every class above

``MoltParseError`` is molt-native (research doc 02 section 12.6) rather than ported; the changeset
grammar tests in ``tests/changeset/test_parse.py`` assume it, so it is listed here to keep the
intended surface in one place. Its cases are asserted there, not in this file.

``MoltKeyPathError`` is likewise molt-native and likewise asserted elsewhere first:
``tests/apply/test_edit_toml.py`` calls it "an assumed seam" and already raises it by name for a
missing TOML key path, an unsplice-able dependency section, and a direct-reference requirement
with no specifier region. This file pins its constructor shape directly.

``MoltError`` is this file's own addition, not named by any upstream source or any other test
file: none of ``@changesets/errors``' classes share a common base beyond the bare JS ``Error``,
but a CLI error funnel that wants to distinguish "a molt-raised, expected failure" from "a bug"
needs one root exception to catch. Every class this module defines subclasses it, pinned by
``test_every_molt_error_subclasses_the_common_base`` below.

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
    """``errors/src/index.ts:9-15`` -- ``ExitError`` reports the process exit code.

    The message is pinned exactly, not merely searched for the digit: ``"2" in str(err)`` alone
    is satisfied by any string that happens to contain a ``2`` (the CLI funnel's own
    ``tests/cli/test_cli.py::test_exit_error_carries_its_code`` renders this string to the user),
    so the loose form cannot tell the documented sentence from a placeholder.
    """
    err = errors.ExitError(2)
    assert err.code == 2
    assert isinstance(err, Exception)
    assert str(err) == "The process exited with code: 2"


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
    """``errors/src/index.ts:17-27`` -- the two pre-mode guard errors have fixed strings."""
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


@pytest.mark.unit
def test_git_error_message_format_is_exact() -> None:
    """Pins the exact wire format, not just the loose regex ``errors/src/index.test.ts:13-15``
    uses -- ``"{message}, exit code: {code}"`` (``errors/src/index.ts:4``), with no extra
    punctuation, reordering, or a dropped exit code. The CLI error funnel renders this string
    directly, so its shape is a contract, not an implementation detail.
    """
    assert str(errors.GitError(1, "Operation failed")) == "Operation failed, exit code: 1"


@pytest.mark.unit
def test_molt_parse_error_carries_path_and_line() -> None:
    """research doc 02 section 12.6 -- ``MoltParseError(message, *, path=None, line=None)``.

    ``tests/changeset/test_parse.py`` and ``tests/apply/test_edit_toml.py`` both already raise
    this class by name (the former through a documented fallback-to-``Exception`` resolver until
    this module lands); this is the one place its constructor shape is pinned directly.
    """
    err = errors.MoltParseError("bad frontmatter", path="CHANGELOG.md", line=3)
    assert isinstance(err, errors.MoltError)
    assert str(err) == "bad frontmatter"
    assert err.path == "CHANGELOG.md"
    assert err.line == 3


@pytest.mark.unit
def test_molt_parse_error_defaults_path_and_line_to_none() -> None:
    """``path``/``line`` are optional -- a parse failure caught deep inside a helper (e.g. a
    malformed TOML document with no changeset file on disk at all) may not know either yet.
    """
    err = errors.MoltParseError("bad frontmatter")
    assert err.path is None
    assert err.line is None


@pytest.mark.unit
def test_molt_key_path_error_carries_its_message() -> None:
    """``tests/apply/test_edit_toml.py`` module docstring, contracts 2 and 4: a missing key path,
    an unsplice-able dependency section, or a direct-reference requirement all raise this class,
    and the message has to name the failing path or dependency so a ``version-package``-style
    caller stays debuggable rather than getting one opaque exception for every failure mode.
    """
    err = errors.MoltKeyPathError('Key path "project.version" not found')
    assert isinstance(err, errors.MoltError)
    assert "project.version" in str(err)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "why"),
    [
        (lambda: errors.GitError(1, "boom"), "the git wrapper's own exception"),
        (lambda: errors.ExitError(2), "the CLI error funnel's exit-code carrier"),
        (lambda: errors.PreExitButNotInPreModeError(), "a pre-mode guard error"),
        (lambda: errors.PreEnterButInPreModeError(), "a pre-mode guard error"),
        (lambda: errors.InternalError("boom"), "guards the fixed/linked glob regression"),
        (lambda: errors.MoltParseError("boom"), "changeset-grammar / manifest parse failures"),
        (lambda: errors.MoltKeyPathError("boom"), "edit_toml's missing-key-path failures"),
    ],
)
def test_every_molt_error_subclasses_the_common_base(factory, why: str) -> None:
    """Pins ``MoltError`` as the common base so ``except MoltError`` is a usable contract.

    Nothing upstream needs this test -- ``@changesets/errors`` classes all extend the bare JS
    ``Error`` with no shared marker class -- but a CLI error funnel that wants to catch "any
    molt-raised, expected failure" (as opposed to a genuine bug) needs one root to catch against.
    Every class this module defines must trace back to it, or a caller's ``except MoltError``
    silently fails to catch one of them.
    """
    assert isinstance(factory(), errors.MoltError), why


@pytest.mark.unit
@pytest.mark.parametrize(
    ("foreign", "why"),
    [
        (lambda: ValueError("not ours"), "a stdlib error raised by a helper molt calls"),
        (
            lambda: RuntimeError("kaboom"),
            "the exact class tests/cli/test_cli.py raises to prove "
            "the funnel prints a traceback instead of a friendly message",
        ),
        (lambda: KeyError("k"), "a genuine bug, which must never look like an expected failure"),
        (lambda: OSError("disk"), "an I/O failure molt did not wrap"),
    ],
)
def test_a_foreign_exception_is_not_a_molt_error(foreign, why: str) -> None:
    """ANTI-VACUITY for the row above: ``MoltError`` must *discriminate*.

    ``test_every_molt_error_subclasses_the_common_base`` only asserts ``isinstance(x, MoltError)``
    for molt's own classes, which is trivially true of ``MoltError = Exception`` -- an alias that
    names a root without creating one. Verified by mutation: aliasing ``MoltError`` to
    ``Exception`` leaves that row green.

    The distinction is load-bearing for the committed CLI funnel. ``tests/cli/test_cli.py::
    test_an_unexpected_exception_prints_a_traceback_and_exits_one`` raises ``RuntimeError`` and
    requires a **traceback** plus the issue-report path, while ``ExitError``/``InternalError``
    take the friendly branches (``index.ts:51-58``). A funnel written ``except MoltError`` over an
    alias would swallow every bug into the friendly branch and the traceback would never print.
    """
    assert not isinstance(foreign(), errors.MoltError), why


@pytest.mark.unit
def test_molt_error_is_a_real_root_below_exception() -> None:
    """The structural half of the same guard: ``MoltError`` is a proper subclass of ``Exception``.

    ``except MoltError`` has to be narrower than ``except Exception`` to be worth writing, and
    ``raise MoltError`` has to be legal. Both fail if the name is an alias or a non-exception
    marker class.
    """
    assert isinstance(errors.MoltError, type)
    assert issubclass(errors.MoltError, Exception)
    assert errors.MoltError is not Exception, "an alias is not a hierarchy"
    assert errors.MoltError is not BaseException
