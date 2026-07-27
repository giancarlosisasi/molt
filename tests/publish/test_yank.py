"""Conformance tests for ``molt yank`` -- PEP 592, molt-native, no changesets analogue.

There is nothing to port here. ``roadmap/research/test-suite/07-publish-pack.md`` lists ``molt
yank`` under "New in molt", research README 4.3 explains why it exists ("PyPI has no unpublish...
our worst constraint becomes a feature") and README section 5 item 11 lists it as differentiator
#11, marked "Impossible on npm". The specification is therefore the documentation:
``website/docs/cli/yank.md`` (flags, exit codes) and ``website/docs/guides/yank.md`` (semantics).

``molt yank`` does NOT perform the yank -- owner ruling, Session 6
-----------------------------------------------------------------
**PyPI exposes no supported way for a tool to yank a release.** PEP 592 specifies only the *read*
side (the ``data-yanked`` attribute in the Simple API) and explicitly leaves setting the flag to
each index. PyPI implements setting it as a **web form** on
``https://pypi.org/manage/project/<name>/releases/``: there is no documented API endpoint
(`warehouse#12708 <https://github.com/pypi/warehouse/issues/12708>`_ is the open request), ``twine``
has no yank command, PyPI API tokens are scoped to *uploads* so the CI credential could not
authorize one anyway, and only a project **Owner** may yank. Verified against
``docs.pypi.org/project-management/yanking/`` on 2026-07-26.

So ``molt yank`` is a **read-only advisory command**. It queries the index's public JSON API, checks
that the version exists, reports whether it is already yanked and why, and returns the management
URL plus the steps for the user to complete in a browser. The single most important assertion in
this file is therefore a **negative** one: the command must never attempt a mutation. ``FakeIndex``
has no write method at all, and every plausible one raises loudly, so an implementation that tries
to yank fails here rather than shipping.

This is a **deliberate divergence from an earlier draft of the docs**, which promised that ``molt
yank`` marked the version itself. That draft was unbuildable. ``cli/yank.md`` and ``guides/yank.md``
were rewritten to match this file, not the other way round.

What this file tests, and what it does not
------------------------------------------
The **library seam** ``molt.publish.yank(package, version, ...)`` (test-contract section 5), not the
shell routing. ``tests/cli/test_cli.py`` already pins the CLI surface -- two positionals plus
``--reason`` / ``--undo`` / ``--repository``, with ``--cwd`` a global -- and nothing here may
contradict it. Note that ``yank`` carries **no** ``--dry-run`` and is absent from that file's
``MUTATING_COMMANDS``: a command that never mutates does not need a switch to not mutate.

Decisions pinned where the docs are silent (all reported to the owner)
---------------------------------------------------------------------
1. **The library seam does not prompt.** There is nothing to confirm now that nothing is mutated,
   but the guarantee is kept and asserted (by poisoning stdin) because the seam must stay usable
   from the GitHub Action, which never has a TTY.
2. **"Already in the requested state" is success, not an error.** Checking a version that is
   already yanked returns ``already=True`` and exit 0; the docs list only "not found" and
   "unreachable" as failures, and a re-run of a completed recovery must not turn red in CI.
3. **Names are PEP 503-normalized before lookup** (research README 4.5), exactly as they are for tag
   construction and config matching -- and the normalized name is what the printed management URL
   carries, because that is the form PyPI serves.
4. **The index seam is injected as ``index=``**, mirroring the ``console=``/``git=``/``uploader=``
   convention the CLI and publish suites froze. The double lives in this file rather than in
   ``tests/publish/fake_publish.py`` because that module is frozen and no other writer needs it.
5. **No credential is resolved.** The command reads only the public JSON API, so it must not touch
   an auth seam; asserted negatively via ``FakeIndex.resolve_credentials``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

import pytest
from tests.publish.fake_publish import require_publish

_publish_module = require_publish()

if getattr(_publish_module, "yank", None) is None:  # pragma: no cover - TDD guard
    pytest.skip(
        "molt.publish exists but exposes no yank() yet (build step 8)",
        allow_module_level=True,
    )

#: A third guard stage: :func:`require_publish` only proves ``publish()`` exists, and ``yank`` is a
#: molt-native verb that may well land later than the rest of the module. Bound off the module
#: object rather than imported with a ``from molt.publish import yank`` line, which would trip
#: ruff's ``E402`` -- its exemption only recognises a literal bare ``pytest.importorskip(...)``.
yank: Any = _publish_module.yank

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

pytestmark = [pytest.mark.functional]

#: The management page a maintainer must open. PyPI serves it under the **normalized** project
#: name (``docs.pypi.org/project-management/yanking/``).
PYPI_MANAGE_URL = "https://pypi.org/manage/project/{name}/releases/"
TESTPYPI_MANAGE_URL = "https://test.pypi.org/manage/project/{name}/releases/"


# --------------------------------------------------------------------------------------
# The index double -- READ ONLY, by construction
# --------------------------------------------------------------------------------------


@dataclass
class Release:
    """One release on the index. ``yanked`` is a *flag*: the file itself never goes away."""

    version: str
    yanked: bool = False
    reason: str | None = None


class IndexUnreachable(RuntimeError):
    """The index could not be queried (``cli/yank.md`` exit-code table: unreachable -> 1)."""


class FakeIndex:
    """Serves releases from an in-memory map and **fails loudly on any attempt to mutate**.

    Injected as ``index=``. Every write-shaped method PyPI does not offer is present here purely so
    that reaching for one raises with an explanation -- ``set_yanked``, ``yank``, ``unyank``,
    ``delete``, and ``resolve_credentials``. That is the point of the double: the ruling that molt
    cannot perform a yank is only enforced if trying to is a test failure.

    Package keys are stored **verbatim**, not normalized, so a test seeding ``foo-bar`` and looking
    up ``Foo_Bar`` genuinely exercises the caller's normalization instead of the double's.
    """

    def __init__(
        self,
        releases: Mapping[str, Sequence[str | Release]] | None = None,
        *,
        unreachable: bool = False,
    ) -> None:
        self.releases: dict[str, list[Release]] = {
            name: [
                item if isinstance(item, Release) else Release(item) for item in (versions or [])
            ]
            for name, versions in (releases or {}).items()
        }
        self.unreachable = unreachable
        self.reads: list[tuple[str, str, str | None]] = []

    # -- read ---------------------------------------------------------------------------
    def get_release(
        self, package: str, version: str, *, repository: str | None = None
    ) -> Release | None:
        """The only method a conforming implementation may call."""
        self.reads.append((package, version, repository))
        if self.unreachable:
            raise IndexUnreachable(f"could not reach {repository or 'pypi'}")
        for release in self.releases.get(package, []):
            if release.version == version:
                return release
        return None

    def versions(self, package: str) -> list[str]:
        """Every version still on the index, yanked or not."""
        return [release.version for release in self.releases.get(package, [])]

    # -- the writes PyPI does not offer -------------------------------------------------
    def _no_write(self, what: str) -> NoReturn:
        raise AssertionError(
            f"molt.publish.yank called {what}(): PyPI exposes no API for yanking, so the command "
            "must only read and print the browser steps (cli/yank.md, 'Why this is a manual step')"
        )

    def set_yanked(self, *args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        self._no_write("set_yanked")

    def yank(self, *args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        self._no_write("yank")

    def unyank(self, *args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        self._no_write("unyank")

    def resolve_credentials(self, *args: Any, **kwargs: Any) -> NoReturn:
        del args, kwargs
        raise AssertionError(
            "molt.publish.yank resolved a credential: it reads only the public JSON API and needs "
            "none (cli/yank.md, 'It contacts only the public read API')"
        )

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Present only so reaching for it fails loudly: PyPI has no delete, and yank is not one."""
        del args, kwargs
        raise AssertionError(
            "yank is not deletion: PyPI is immutable and the version must stay resolvable "
            "for the pins that already depend on it (guides/yank.md)"
        )

    # -- assertion helpers ---------------------------------------------------------------
    def is_yanked(self, package: str, version: str) -> bool:
        for release in self.releases.get(package, []):
            if release.version == version:
                return release.yanked
        return False


class NoInput:
    """A stdin replacement that fails on any read; see decision 1 in the module docstring."""

    def read(self, *args: Any) -> str:
        raise AssertionError("molt.publish.yank read stdin: it has nothing to confirm")

    def readline(self, *args: Any) -> str:
        raise AssertionError("molt.publish.yank prompted: it has nothing to confirm")

    def isatty(self) -> bool:
        return False


@dataclass
class RecordedConsole:
    """A minimal console double -- ``fake_cli.RecordingConsole`` without the CLI import."""

    messages: list[str] = field(default_factory=list)

    def info(self, message: str) -> None:
        self.messages.append(message)

    def success(self, message: str) -> None:
        self.messages.append(message)

    def warn(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.messages.append(message)

    def note(self, title: str, body: str = "") -> None:
        self.messages.append(f"{title} {body}".strip())

    def text(self) -> str:
        return "\n".join(self.messages)


def run_failing(call: Callable[[], object]) -> BaseException:
    """Run ``call``, return the exception it raised; fail the test if it returned normally."""
    try:
        call()
    except Exception as exc:
        return exc
    pytest.fail("expected the yank check to fail, but it returned normally")


def steps_text(result: Any) -> str:
    """The printed steps as one string, however the result models them."""
    steps = getattr(result, "steps", None)
    if steps is None:
        return str(result)
    return "\n".join(steps) if isinstance(steps, (list, tuple)) else str(steps)


# --------------------------------------------------------------------------------------
# The command reads, reports, and instructs -- it never mutates
# --------------------------------------------------------------------------------------


def test_yank_reports_the_release_and_returns_the_manual_steps() -> None:
    """``cli/yank.md``: molt "verifies the version, tells you whether it is already yanked, and
    hands you the exact URL and steps -- and you complete the yank in your browser."

    The happy path: the version is on the index and not yanked, so there is a real action to
    describe. Both halves are asserted -- the management URL and the reason text -- because a
    result carrying the URL but silently dropping ``--reason`` would leave the user pasting
    nothing into PyPI's dialog.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    result = yank(
        "acme-core",
        "1.1.0",
        reason="Corrupt wheel; use 1.1.1.",
        console=RecordedConsole(),
        index=index,
    )

    assert result.url == PYPI_MANAGE_URL.format(name="acme-core")
    assert result.already is False
    assert "Corrupt wheel; use 1.1.1." in steps_text(result)


def test_yank_never_mutates_the_index() -> None:
    """**The load-bearing test of this file.** PyPI has no yank API (``cli/yank.md``, "Why this is
    a manual step"), so a conforming implementation calls exactly one index method: the read.

    ``FakeIndex`` raises on ``set_yanked``/``yank``/``unyank``/``delete``, so an implementation that
    tries to perform the action fails here. The positive half -- that the read *did* happen -- is
    asserted too, so this cannot pass by never touching the index at all.
    """
    index = FakeIndex({"acme-core": ["1.1.0"]})

    yank("acme-core", "1.1.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.reads == [("acme-core", "1.1.0", "pypi")]
    assert index.is_yanked("acme-core", "1.1.0") is False, "the release must be untouched"


def test_yank_resolves_no_credential() -> None:
    """``cli/yank.md``: "It contacts only the public read API, so it needs **no credentials**."

    An upload token cannot authorize a yank anyway (warehouse#12708), so an implementation that
    resolves one is doing work that can only fail confusingly in CI.
    """
    index = FakeIndex({"acme-core": ["1.1.0"]})

    yank("acme-core", "1.1.0", console=RecordedConsole(), index=index)


def test_the_steps_name_the_browser_action() -> None:
    """The steps must be actionable without the user already knowing PyPI's UI.

    ``docs.pypi.org/project-management/yanking/`` describes exactly this path: open the release
    management page, click ``Options`` next to the release, then ``Yank``. A result that printed
    only a bare URL would leave the user hunting.
    """
    index = FakeIndex({"acme-core": ["1.1.0"]})

    result = yank("acme-core", "1.1.0", console=RecordedConsole(), index=index)

    text = steps_text(result).lower()
    assert "options" in text
    assert "yank" in text
    assert PYPI_MANAGE_URL.format(name="acme-core") in steps_text(result)


def test_yank_is_not_deletion() -> None:
    """``guides/yank.md``: a yanked version "stays on the index and stays installable".

    ``FakeIndex.delete`` raises, so an implementation reaching for a delete fails loudly.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    yank("acme-core", "1.1.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.versions("acme-core") == ["1.0.0", "1.1.0"]


# --------------------------------------------------------------------------------------
# --undo, and the already-in-that-state cases
# --------------------------------------------------------------------------------------


def test_undo_returns_unyank_steps() -> None:
    """``cli/yank.md``: ``--undo`` -- "Print the steps to **un-yank** instead".

    Asserted on the returned ``undo`` flag *and* on the text, so an implementation that forwards
    the flag into the result but renders yank instructions regardless still fails.
    """
    index = FakeIndex({"acme-core": [Release("1.1.0", yanked=True, reason="Bad release")]})

    result = yank("acme-core", "1.1.0", undo=True, console=RecordedConsole(), index=index)

    assert result.undo is True
    assert result.already is False, "it is yanked, so there is a real un-yank to perform"
    assert "un-yank" in steps_text(result).lower()


@pytest.mark.parametrize(
    ("seeded_yanked", "undo", "why"),
    [
        (
            True,
            False,
            "already yanked and asked to yank -- nothing to do, and a re-run of a completed "
            "recovery must not turn red in CI",
        ),
        (
            False,
            True,
            "not yanked and asked to un-yank -- likewise a no-op, not a failure",
        ),
    ],
)
def test_a_version_already_in_the_requested_state_is_reported_not_failed(
    seeded_yanked: bool, undo: bool, why: str
) -> None:
    """Decision 2 in the module docstring. ``cli/yank.md``'s exit-code table gives this its own
    row at 0; only "not found" and "unreachable" are failures."""
    index = FakeIndex({"acme-core": [Release("1.1.0", yanked=seeded_yanked, reason="Bad")]})

    result = yank("acme-core", "1.1.0", undo=undo, console=RecordedConsole(), index=index)

    assert result.already is True, why


def test_an_already_yanked_version_surfaces_the_recorded_reason() -> None:
    """The JSON API carries ``yanked_reason``, and it is the thing that tells the user whether the
    yank they are about to make has already been made *for a different reason*."""
    index = FakeIndex({"acme-core": [Release("1.1.0", yanked=True, reason="Corrupt wheel")]})

    result = yank(
        "acme-core",
        "1.1.0",
        reason="Something else",
        console=RecordedConsole(),
        index=index,
    )

    assert result.already is True
    assert result.current_reason == "Corrupt wheel", (
        "the reason already recorded on the index, not the --reason the caller passed -- they "
        "differ here precisely so an implementation echoing its own input cannot pass"
    )


# --------------------------------------------------------------------------------------
# Failures: not found, unreachable
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("package", "version", "why"),
    [
        ("acme-cli", "1.0.0", "the package is not on the index at all"),
        ("acme-core", "9.9.9", "the package is there but that version never shipped"),
    ],
)
def test_an_unknown_package_or_version_is_an_error(package: str, version: str, why: str) -> None:
    """``cli/yank.md`` exit-code table: "Package or version not found on the index" -> 1.

    This is the check that earns the command its keep: a typo'd version number is the realistic
    failure, and catching it here beats loading a management page where one wrong click yanks a
    *good* release (``guides/yank.md``, "Running a yank safely").
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    exc = run_failing(lambda: yank(package, version, console=RecordedConsole(), index=index))

    assert version in str(exc) or package in str(exc), why


def test_an_unreachable_index_is_an_error() -> None:
    """``cli/yank.md`` exit-code table: "The index could not be reached" -> 1.

    Distinct from "not found": a network failure must not be reported as "that version does not
    exist", which would send the user off to check a release that is in fact fine.
    """
    index = FakeIndex({"acme-core": ["1.1.0"]}, unreachable=True)

    run_failing(lambda: yank("acme-core", "1.1.0", console=RecordedConsole(), index=index))


# --------------------------------------------------------------------------------------
# --repository, normalization, and the no-prompt guarantee
# --------------------------------------------------------------------------------------


def test_repository_selects_both_the_index_queried_and_the_url_printed() -> None:
    """``cli/yank.md``: ``--repository`` "Selects which index is queried and which management URL
    is printed."

    Both halves are asserted. An implementation that routes the *query* but prints pypi.org's
    management URL would send the user to yank a release on the wrong index -- the same
    single-use-of-a-multi-use-setting bug the publish suite caught as P6 B3.
    """
    index = FakeIndex({"acme-core": ["1.0.0"]})

    result = yank(
        "acme-core", "1.0.0", repository="testpypi", console=RecordedConsole(), index=index
    )

    assert index.reads == [("acme-core", "1.0.0", "testpypi")]
    assert result.url == TESTPYPI_MANAGE_URL.format(name="acme-core")


def test_yank_normalizes_the_package_name_per_pep_503() -> None:
    """Research README 4.5 -- normalization happens at lookup, and PyPI serves the management page
    under the normalized name, so the printed URL must carry it too.

    ``FakeIndex`` stores keys verbatim, so seeding ``foo-bar`` and asking for ``Foo_Bar`` fails
    unless the caller normalizes.
    """
    index = FakeIndex({"foo-bar": ["1.0.0"]})

    result = yank("Foo_Bar", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.reads == [("foo-bar", "1.0.0", "pypi")]
    assert result.url == PYPI_MANAGE_URL.format(name="foo-bar")


def test_the_library_seam_never_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 1 -- the seam must stay usable from the GitHub Action, which has no TTY.

    There is nothing to confirm now that nothing is mutated, but stdin is poisoned rather than
    merely unused: a future implementation that adds an "are you sure" fails here instead of
    hanging a CI job forever.
    """
    monkeypatch.setattr(sys, "stdin", NoInput())
    index = FakeIndex({"acme-core": ["1.0.0"]})

    yank("acme-core", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)
