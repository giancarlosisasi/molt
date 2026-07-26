"""Conformance tests for ``molt yank`` -- PEP 592, molt-native, no changesets analogue.

There is nothing to port here. ``roadmap/research/test-suite/07-publish-pack.md`` lists ``molt
yank`` under "New in molt", research README 4.3 explains why it exists ("PyPI has no unpublish...
our worst constraint becomes a feature") and README section 5 item 11 lists it as differentiator
#11, marked "Impossible on npm". The specification is therefore the documentation:
``website/docs/cli/yank.md`` (flags, exit codes) and ``website/docs/guides/yank.md`` (semantics).

What this file tests, and what it does not
------------------------------------------
The **library seam** ``molt.publish.yank(package, version, ...)`` (test-contract section 5), not the
shell routing. ``tests/cli/test_cli.py`` already pins the CLI surface -- two positionals plus
``--reason`` / ``--undo`` / ``--repository`` / ``--dry-run`` and the ``--yes`` /
``--non-interactive`` / ``--cwd`` globals -- and nothing here may contradict it.

Decisions pinned where the docs are silent (all reported to the owner)
---------------------------------------------------------------------
1. **The library seam does not confirm.** ``cli/yank.md`` says molt "asks for confirmation before
   acting unless you pass ``--yes``", and the exit-code table gives declining the prompt its own
   code. That is command-layer behaviour: ``molt.commands.yank`` owns the prompt, and
   ``molt.publish.yank`` is the non-interactive core it calls once the answer is yes. Putting the
   prompt in the library would make the seam unusable from the GitHub Action, which never has a TTY.
   Asserted negatively, by poisoning stdin.
2. **Yank and un-yank are both idempotent.** Yanking an already-yanked version updates the reason;
   un-yanking a version that is not yanked is a no-op. The docs list only "package or version not
   found, or auth failure" as failures, so neither of these can be an error, and idempotence is what
   makes a retried CI step safe (the same rule ``molt git-tag`` already follows).
3. **Names are PEP 503-normalized before lookup** (research README 4.5), exactly as they are for tag
   construction and config matching.
4. **The index seam is injected as ``index=``**, mirroring the ``console=``/``git=``/``uploader=``
   convention the CLI and publish suites froze. The double lives in this file rather than in
   ``tests/publish/fake_publish.py`` because that module is frozen and no other writer needs it;
   this is the harness gap this phase reports.
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


# --------------------------------------------------------------------------------------
# The index double -- the PEP 592 yank endpoint
# --------------------------------------------------------------------------------------


@dataclass
class Release:
    """One release on the index. ``yanked`` is a *flag*: the file itself never goes away."""

    version: str
    yanked: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class YankCall:
    """One recorded mutation."""

    package: str
    version: str
    yanked: bool
    reason: str | None = None
    repository: str | None = None


class IndexAuthFailed(RuntimeError):
    """The index rejected the credentials (``cli/yank.md`` exit-code table: auth failure -> 1)."""


class FakeIndex:
    """Records yank/un-yank mutations against an in-memory set of releases.

    Injected as ``index=``. Deliberately **not** built on the frozen ``pypi_registry`` fixture: that
    one mocks ``GET pypi.org/pypi/<name>/json`` over HTTP, and a yank has to both read the release
    and write the flag, so keeping one double for both halves avoids two disagreeing sources of
    truth inside a single command.

    Package keys are stored **verbatim**, not normalized, so a test seeding ``foo-bar`` and yanking
    ``Foo_Bar`` genuinely exercises the caller's normalization instead of the double's.
    """

    def __init__(
        self,
        releases: Mapping[str, Sequence[str]] | None = None,
        *,
        auth_failure: bool = False,
    ) -> None:
        self.releases: dict[str, list[Release]] = {
            name: [Release(version) for version in versions]
            for name, versions in (releases or {}).items()
        }
        self.auth_failure = auth_failure
        self.calls: list[YankCall] = []

    # -- read ---------------------------------------------------------------------------
    def get_release(
        self, package: str, version: str, *, repository: str | None = None
    ) -> Release | None:
        del repository
        for release in self.releases.get(package, []):
            if release.version == version:
                return release
        return None

    def versions(self, package: str) -> list[str]:
        """Every version still on the index, yanked or not."""
        return [release.version for release in self.releases.get(package, [])]

    # -- write --------------------------------------------------------------------------
    def set_yanked(
        self,
        *,
        package: str,
        version: str,
        yanked: bool,
        reason: str | None = None,
        repository: str | None = None,
    ) -> None:
        self.calls.append(YankCall(package, version, yanked, reason, repository))
        if self.auth_failure:
            raise IndexAuthFailed(f"{package}: credentials rejected")
        release = self.get_release(package, version)
        if release is None:  # pragma: no cover - the caller must check first
            raise AssertionError(f"set_yanked on an unknown release: {package} {version}")
        release.yanked = yanked
        release.reason = reason if yanked else None

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Present only so reaching for it fails loudly: PyPI has no delete, and yank is not one."""
        del args, kwargs
        raise AssertionError(
            "yank is not deletion: PyPI is immutable and the version must stay resolvable "
            "for the pins that already depend on it (guides/yank.md)"
        )

    # -- assertion helpers ---------------------------------------------------------------
    def is_yanked(self, package: str, version: str) -> bool:
        release = self.get_release(package, version)
        return release is not None and release.yanked

    def reason_for(self, package: str, version: str) -> str | None:
        release = self.get_release(package, version)
        return None if release is None else release.reason


class NoInput:
    """A stdin replacement that fails on any read; see decision 1 in the module docstring."""

    def read(self, *args: Any) -> str:
        raise AssertionError("molt.publish.yank read stdin: confirmation belongs to the CLI layer")

    def readline(self, *args: Any) -> str:
        raise AssertionError("molt.publish.yank prompted: confirmation belongs to the CLI layer")

    def isatty(self) -> bool:
        return False


@dataclass
class RecordedConsole:
    """A minimal console double -- ``tests/cli/fake_cli.RecordingConsole`` without the CLI import.

    Only the levels a yank can emit are modelled; the dry-run row is the one that reads it back.
    """

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
    pytest.fail("expected the yank to fail, but it returned normally")


# --------------------------------------------------------------------------------------
# Yanking
# --------------------------------------------------------------------------------------


def test_yank_marks_the_version_yanked_with_the_reason() -> None:
    """``cli/yank.md``: "It marks one ``<package>`` at one ``<version>`` as yanked (optionally with
    a reason that PyPI displays)".

    The reason is not decoration -- PEP 592 carries it in the ``data-yanked`` attribute of the
    simple index and resolvers surface it to the user who would otherwise wonder why their pin
    stopped being selected. It has to reach the index, so it is asserted on the recorded call as
    well as on the resulting state.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    yank(
        "acme-core",
        "1.1.0",
        reason="Corrupt wheel; use 1.1.1.",
        console=RecordedConsole(),
        index=index,
    )

    assert index.is_yanked("acme-core", "1.1.0")
    assert index.reason_for("acme-core", "1.1.0") == "Corrupt wheel; use 1.1.1."
    assert index.calls == [
        YankCall("acme-core", "1.1.0", yanked=True, reason="Corrupt wheel; use 1.1.1.")
    ]


def test_yank_targets_exactly_one_version() -> None:
    """A yank is version-scoped, never package-scoped.

    ``cli/yank.md`` makes ``<version>`` a required positional ("Exact version to yank") precisely
    because the alternative -- yanking a project -- is not a thing PEP 592 offers and would take
    every release out of the default resolution path at once. Two neighbours on the same project
    make an over-broad implementation observable.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0", "1.2.0"]})

    yank("acme-core", "1.1.0", console=RecordedConsole(), index=index)

    assert [index.is_yanked("acme-core", v) for v in ("1.0.0", "1.1.0", "1.2.0")] == [
        False,
        True,
        False,
    ]


def test_yank_without_a_reason_records_none() -> None:
    """``--reason`` is optional (``cli/yank.md`` options table: no default).

    Pinned so an implementation cannot invent a placeholder reason: whatever string it chose would
    be published on the index and shown to every downstream user forever.
    """
    index = FakeIndex({"acme-core": ["1.0.0"]})

    yank("acme-core", "1.0.0", console=RecordedConsole(), index=index)

    assert index.is_yanked("acme-core", "1.0.0")
    assert index.reason_for("acme-core", "1.0.0") is None


def test_yanking_is_not_deletion() -> None:
    """``guides/yank.md``: "A yanked version stays on the index and stays installable".

    The whole product argument for yank rests on this asymmetry -- new installs skip the version,
    exact pins keep resolving -- so the test asserts both halves: the version is still listed by the
    index afterwards, and the delete route was never taken (:meth:`FakeIndex.delete` raises if it
    is). Research README 4.3: PyPI has no unpublish, which is *why* yank is the recovery verb.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    yank("acme-core", "1.1.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.versions("acme-core") == ["1.0.0", "1.1.0"], "the release is still on the index"
    assert index.is_yanked("acme-core", "1.1.0")


# --------------------------------------------------------------------------------------
# Un-yanking
# --------------------------------------------------------------------------------------


def test_undo_unyanks_the_version() -> None:
    """``cli/yank.md``: "``--undo`` -- Un-yank instead of yank -- restore the version to normal
    selection"; ``guides/yank.md``: "A yank can be reversed on PyPI".

    The recorded call carries ``yanked=False`` rather than being absent: un-yank is a mutation in
    its own right, and an implementation that simply skipped the call would leave the version yanked
    while reporting success.
    """
    index = FakeIndex({"acme-core": ["1.0.0"]})
    yank("acme-core", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)

    yank("acme-core", "1.0.0", undo=True, console=RecordedConsole(), index=index)

    assert not index.is_yanked("acme-core", "1.0.0")
    assert index.reason_for("acme-core", "1.0.0") is None
    assert [call.yanked for call in index.calls] == [True, False]


# (already_yanked, undo, expected_yanked, why)
IDEMPOTENCE_CASES: list[tuple[bool, bool, bool, str]] = [
    (True, False, True, "decision 2: re-yanking a yanked version is a no-op, not an error"),
    (False, True, False, "decision 2: un-yanking a version that is not yanked is a no-op"),
]


@pytest.mark.parametrize(("already_yanked", "undo", "expected_yanked", "why"), IDEMPOTENCE_CASES)
def test_yank_and_undo_are_idempotent(
    already_yanked: bool, undo: bool, expected_yanked: bool, why: str
) -> None:
    """Decision 2 in the module docstring.

    ``cli/yank.md``'s exit-code table lists exactly three failures -- declined confirmation, not
    found, auth failure -- so neither repetition can be one. It matters operationally: the release
    job that yanks is exactly the kind of step that gets re-run after an unrelated failure, and
    ``molt git-tag`` already sets the precedent ("a tag that already exists is skipped, so
    re-running is safe").
    """
    index = FakeIndex({"acme-core": ["1.0.0"]})
    if already_yanked:
        yank("acme-core", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)

    yank("acme-core", "1.0.0", undo=undo, console=RecordedConsole(), index=index)

    assert index.is_yanked("acme-core", "1.0.0") is expected_yanked, why
    assert index.versions("acme-core") == ["1.0.0"], why


# --------------------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------------------

# (package, version, why)
NOT_FOUND_CASES: list[tuple[str, str, str]] = [
    ("nosuchpkg", "1.0.0", "the distribution is not on the index at all"),
    ("acme-core", "9.9.9", "the distribution exists but has never released that version"),
]


@pytest.mark.parametrize(("package", "version", "why"), NOT_FOUND_CASES)
def test_an_unknown_package_or_version_is_an_error(package: str, version: str, why: str) -> None:
    """``cli/yank.md`` exit codes: "Package or version not found ... 1".

    ``index.calls == []`` is the assertion that carries the weight. Failing *after* attempting the
    mutation would be indistinguishable from succeeding against a typo'd version -- and on an index
    where a yank is publicly visible, yanking the wrong release is its own incident.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})

    run_failing(lambda: yank(package, version, console=RecordedConsole(), index=index))

    assert index.calls == [], why
    assert [index.is_yanked("acme-core", v) for v in ("1.0.0", "1.1.0")] == [False, False], why


def test_an_auth_failure_surfaces() -> None:
    """``cli/yank.md`` exit codes: "...or auth failure | 1".

    The state assertion pairs with the raise: an implementation that swallowed the rejection and
    reported success would leave the operator believing a broken release had been pulled.
    """
    index = FakeIndex({"acme-core": ["1.0.0"]}, auth_failure=True)

    run_failing(
        lambda: yank("acme-core", "1.0.0", reason="Bad", console=RecordedConsole(), index=index)
    )

    assert not index.is_yanked("acme-core", "1.0.0")


# --------------------------------------------------------------------------------------
# --dry-run, --repository, and the absence of a prompt
# --------------------------------------------------------------------------------------


def test_dry_run_performs_no_mutation_and_still_reports_the_plan() -> None:
    """``cli/yank.md``: "``--dry-run`` prints exactly what would be yanked and contacts nothing";
    ``guides/dry-run-and-plans.md``: a dry run "is a faithful preview, never an approximation".

    Both halves are asserted. The negative alone would pass against a ``--dry-run`` that does
    nothing at all and prints nothing either, which is not a preview -- and the operator reaching
    for ``--dry-run`` on a yank is precisely the one who needs to see the target before committing.
    """
    index = FakeIndex({"acme-core": ["1.0.0", "1.1.0"]})
    console = RecordedConsole()

    yank(
        "acme-core",
        "1.1.0",
        reason="Corrupt wheel",
        dry_run=True,
        console=console,
        index=index,
    )

    assert index.calls == [], "a dry run contacts nothing"
    assert not index.is_yanked("acme-core", "1.1.0")
    text = console.text()
    assert "acme-core" in text
    assert "1.1.0" in text


def test_repository_selects_the_index_the_version_lives_on() -> None:
    """``cli/yank.md``: "``--repository <name>`` -- Named repository/index the version lives on",
    default ``pypi``.

    Not cosmetic: research README 4.3 sends snapshot releases to a **non-PyPI index** by default, so
    the version an operator needs to yank is frequently not on pypi.org at all. A yank that ignored
    ``--repository`` would silently target the wrong index -- and, worse, report success.
    """
    index = FakeIndex({"acme-core": ["1.0.0"]})

    yank("acme-core", "1.0.0", repository="testpypi", console=RecordedConsole(), index=index)

    assert [call.repository for call in index.calls] == ["testpypi"]


def test_yank_normalizes_the_package_name_per_pep_503() -> None:
    """Decision 3: research README 4.5, "Flat global namespace + PEP 503 normalization".

    The index knows the distribution by its normalized name; the operator types whatever the
    manifest spells. If the two are compared verbatim, ``molt yank Foo_Bar 1.0.0`` reports "version
    not found" for a release that plainly exists -- the same class of bug the tag-construction rows
    in ``tests/cli/test_git_tag.py`` guard against, in the one command where the alternative to
    getting it right is an un-yanked broken release.
    """
    index = FakeIndex({"foo-bar": ["1.0.0"]})

    yank("Foo_Bar", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.is_yanked("foo-bar", "1.0.0")
    assert [call.package for call in index.calls] == ["foo-bar"]


def test_the_library_seam_never_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 1: the confirmation lives in ``molt.commands.yank``, not here.

    ``cli/yank.md`` requires a confirmation "unless you pass ``--yes``" and gives a declined prompt
    its own exit code, so the prompt exists -- at the shell layer, where ``--yes`` and
    ``--non-interactive`` are parsed (``tests/cli/test_cli.py`` pins both as globals). The library
    core must stay callable from a job with no TTY, which is where every real yank happens.

    Poisoning stdin catches a prompt raised through any route, including ones that never touch a
    ``prompts`` seam this function was not given.
    """
    monkeypatch.setattr(sys, "stdin", NoInput())
    monkeypatch.setattr(
        "builtins.input",
        lambda *args: pytest.fail(
            "molt.publish.yank called input(): the CLI layer owns the prompt"
        ),
    )
    index = FakeIndex({"acme-core": ["1.0.0"]})

    yank("acme-core", "1.0.0", reason="Bad release", console=RecordedConsole(), index=index)

    assert index.is_yanked("acme-core", "1.0.0"), "the yank ran - the negative is not vacuous"
