"""Conformance tests for ``apply`` -- the end-to-end ``apply-release-plan`` suite.

Every test maps to a row of ``roadmap/research/test-suite/04-apply-changelog.md``, section
``packages/apply-release-plan/src/index.test.ts`` (45 rows: **20 Port / 23 Adapt / 2 Drop** -- the
group file says 21/22, but row 13's self-name rule turned out to have no upstream implementation
behind it, so it is reclassified Adapt; see
:func:`test_self_references_and_path_dependencies_are_skipped`). The two
Drops -- row 26 (``onlyUpdatePeerDependentsWhenOutOfRange``) and row 37 (upstream ``it.todo``) --
are recorded in ``tests/apply/test_deliberately_not_ported.py``, together with the structural drops
this file makes (``pre.json``, the JS formatter matrix, the ``workspace:^`` / ``workspace:~``
aliases). Mechanics and ``file:line`` citations come from
``roadmap/research/changesets-04-apply-changelog-git-ci.md`` sections 1 (side-effect ordering),
2 (manifest mutation) and 3 (changelog format).

Load-bearing facts pinned here
------------------------------
- **Side-effect ordering** (doc 04 section 1.2): validate -> compute -> flush. A release naming a
  package that does not exist, and a changelog generator that raises, both escape **before any
  write** (rows 38, 39); the assertion is a real ``git status`` saying "nothing to commit".
- **Deps are a list of PEP 508 strings**, so a range rewrite is a **substring splice inside one
  requirement string** (research README section 4.5; doc 04 section 2.6). Re-serialising a whole
  ``packaging.requirements.Requirement`` loses the original spacing and is wrong -- the formatting
  rows assert byte-equality outside the edited substring, which catches that directly.
- **molt operates on the whole ``SpecifierSet`` and preserves every comparator.** Upstream's
  ``getVersionRangeType`` (``version-package.ts:116-125``) keeps only the *leading* operator, which
  is how ``">=1.0.0 <2.0.0"`` silently widens to ``">=1.0.4"`` (research README section 3.3). Those
  6 rows are already a recorded Drop in ``tests/versioning/test_deliberately_not_ported.py``.
  :func:`test_upper_bound_is_preserved_when_the_lower_bound_moves` pins the replacement rule.
- **``should_update_dependency`` gate** (doc 04 section 2.5, ``utils.ts:20-81``): a dependency whose
  new version *leaves* the range is ALWAYS updated, overriding ``update_internal_dependencies``;
  otherwise ``bump_level(type) >= bump_level(update_internal_dependencies)`` on the ladder
  ``["none", "patch", "minor", "major"]``.
- **Changelog shape** (doc 04 sections 3.1-3.3): ``## <version>`` then
  ``### Major|Minor|Patch Changes``; empty sections dropped; **dependency bumps always last and
  always in the PATCH section**; dev-group dependencies never produce an "Updated dependencies"
  line; ``type == "none"`` writes no ``CHANGELOG.md`` at all.
- **Changesets are not deleted for ignored packages nor for private unversioned packages**
  (``index.ts:214-221``), and ``apply`` returns the touched files without committing anything.

Deliberate divergences from upstream (research README sections 3.4 / 4, section 5 item 13)
------------------------------------------------------------------------------------------
1. **Row 29 -- the missing blank line.** Upstream emits ``...testing!\\n## 1.0.0`` when the existing
   ``CHANGELOG.md`` starts with a version heading (``index.ts:356-357``), a wart normally papered
   over by its prettier/oxfmt pass. **molt emits the separating blank line**;
   :func:`test_new_entry_is_inserted_before_an_existing_version_heading` asserts the corrected
   output and fails against the upstream one.
2. **Row 32 -- trailing-space continuation lines.** Upstream's ``changelog-git`` indents *every*
   continuation line by two spaces, so a blank paragraph separator becomes ``"  "``
   (``changelog-git/src/index.ts:14``). **molt emits clean blank lines**;
   :func:`test_multi_line_same_type_summaries_are_listed_with_clean_blank_lines` asserts that and
   fails against the upstream one.
3. **Nothing is atomic upstream** (doc 04 section 1.4), so a mid-loop failure double-bumps on
   re-run. molt buffers then flushes;
   :func:`test_a_failed_flush_leaves_the_tree_pristine_and_a_retry_bumps_exactly_once` is the
   net-new guard -- upstream has no test for this bug at all. The flush **order** is pinned
   separately by :func:`test_side_effects_are_flushed_in_the_documented_order`, because ordering is
   invisible to every end-state assertion and only matters when a run fails part-way.
4. **No lockfile update upstream.** molt updates ``uv.lock`` (research README section 5 item 2).
5. **PEP 440 spellings replace npm ones.** ``^1.0.3`` has no PEP 440 equivalent, so a caret range is
   written as its expansion ``>=1.0.3,<2.0.0`` and a tilde range as the native ``~=1.2.0``; npm
   dist-tags (``"latest"``, ``"bulbasaur"``) become direct-reference specifiers. Each row's ``why``
   states the mapping it used. A new version that escapes a caret expansion moves **both** bounds
   (``>=1.0.3,<2.0.0`` -> ``>=2.0.0,<3.0.0``), which is the PEP 440 form of upstream's
   ``^1.0.3`` -> ``^2.0.0`` and the worked example in
   ``website/docs/guides/dependency-propagation.md``.
6. **Rows 14-15 -- a byte-identical file is not "touched".** Upstream's unconditional ``version``
   edit (``index.ts:153``) rewrites a ``none`` release's manifest with identical bytes and still
   reports it (``index.test.ts:894``, ``:953``). molt reports neither manifest.
7. **Row 13 -- the self-name skip is molt's, not upstream's.** ``getDependencyVersionEdits``
   (``version-package.ts:31-114``) has no self-name check at all; see
   :func:`test_self_references_and_path_dependencies_are_skipped`.
8. **An unconstrained dependency produces no changelog line.** Upstream's manifest rewrite skips it
   (``version-package.ts:93-102``) while its changelog filter does not
   (``get-changelog-entry.ts:53-81``), so upstream reports a bump it never made;
   :func:`test_an_unconstrained_dependency_is_not_listed_in_the_changelog_either` pins the
   corrected behavior, matched on the ``molt.changelog`` side in ``tests/changelog/``.

Markers
-------
``functional`` is this module's kind: every test drives ``apply`` end-to-end over a tmp workspace.
Five tests **additionally** carry ``integration`` because they spawn a real external process --
``git`` (via the ``git_repo`` fixture, also marked ``git``) or ``uv lock``. ``pyproject.toml``
describes ``functional`` as "no external processes", so read the pair as "functional in kind,
integration in cost"; ``-m "functional and not integration"`` selects the process-free ones. Same
shape as ``tests/changeset/test_read.py``, which already combines the two. Flagged for the owner.

Why explicit literals instead of syrupy snapshots
-------------------------------------------------
The phase brief maps every upstream ``toMatchInlineSnapshot`` to a syrupy assertion. This file uses
**explicit string literals** instead, for the same reason ``tests/engine/test_assemble.py`` gave:
a guarded module never runs, so ``--snapshot-update`` can produce no baseline, and an absent
baseline records *nothing*. That is tolerable for an ordering assertion and not tolerable here --
divergences 1 and 2 above live entirely in the expected bytes, and under syrupy they would be
recorded only after the product exists, i.e. whatever the implementation happens to emit would
become the expectation. Literals keep the corrected markdown reviewable in the diff and make the
divergence tests fail against upstream behavior. The ``snapshot`` marker therefore goes unused.
"""

from __future__ import annotations

import logging
import os
import pathlib
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from packaging.requirements import Requirement
from packaging.version import Version
from tests.apply.fake_release_plan import (
    BASE_CHANGESET_ID,
    BASE_SUMMARY,
    FAILING_GENERATOR_MESSAGE,
    FakeApplyConfig,
    FakeChangeset,
    FakeChangesetRelease,
    FakePlan,
    FakeRelease,
    FakeReleasePlan,
    discover_packages,
    find_changed,
    git_status,
    member_manifest,
    read,
    root_manifest,
    run_apply,
    write_changeset_files,
    write_failing_generator,
    write_workspace,
)

from molt.versioning import BumpType, parse_range, satisfies

#: This suite guards on the **seam**, not the package.
#:
#: ``pytest.importorskip("molt.apply")`` was the original guard, on the usual assumption that "the
#: module exists" means "the behavior is implemented". The ``implement-toml-editing`` change broke
#: that assumption the same way ``adopt-typer-cli-shell`` broke it for ``molt.commands.*`` (see the
#: placeholder gate in ``tests/cli/conftest.py``): ``molt.apply`` had to exist to hold the two
#: manifest-editing primitives, which land a build step before ``apply_release_plan`` does. Asking
#: the package for the attribute keeps this module a clean *skip* rather than a collection error.
if not hasattr(
    pytest.importorskip(
        "molt.apply", reason="build step 5 - apply not yet implemented (TDD target)"
    ),
    "apply_release_plan",
):
    pytest.skip(
        "molt.apply.apply_release_plan not yet implemented (TDD target); "
        "molt.apply currently holds only the edit_toml primitives",
        allow_module_level=True,
    )

from molt.apply import apply_release_plan  # pyrefly: ignore[missing-module-attribute]

if TYPE_CHECKING:
    from tests.conftest import GitRepo

pytestmark = pytest.mark.functional


# ======================================================================================
# Local helpers
# ======================================================================================

#: The default changelog generator, named by its entry point.
#:
#: **Docs conflict, reported to the owner:** ``website/docs/extending/changelog-plugins.md``
#: ("Choosing a generator") says a bare name resolves in the ``molt.changelog`` entry-point group
#: and that ``git`` is the default, while ``website/docs/config/options.md`` writes the same option
#: as ``changelog = ["molt.changelog.github", { repo = "acme/acme" }]`` (a dotted module path). The
#: plugin page is the more specific oracle for the plugin seam, so the bare entry-point name is used
#: here; the test contract's ``molt.changelog.git`` module path is the same generator either way.
GIT_GENERATOR = "git"

#: The two escape lines ``apply`` prints before rethrowing a changelog-generation failure
#: (``index.ts:314-322``; asserted upstream at ``index.test.ts:3091-3100``). Ported verbatim -- they
#: carry no npm-ism -- but the wording is a molt seam the owner may reword.
CHANGELOG_ESCAPE_LINES = (
    "The following error was encountered while generating changelog entries",
    "We have escaped applying the changesets, and no files should have been affected",
)


def _cs(id: str, summary: str, releases: dict[str, BumpType]) -> FakeChangeset:
    return FakeChangeset(
        id=id,
        summary=summary,
        releases=[FakeChangesetRelease(name=name, type=bump) for name, bump in releases.items()],
    )


def _rel(
    name: str, bump: BumpType, old: str, new: str, changesets: list[str] | None = None
) -> FakeRelease:
    return FakeRelease(
        name=name,
        type=bump,
        old_version=old,
        new_version=new,
        changesets=list(changesets) if changesets else [],
    )


def _workspace(members: dict[str, str], *, root: str | None = None) -> dict[str, str]:
    """``{relative path: content}`` for a uv workspace root plus ``packages/<name>``."""
    files: dict[str, str] = {"pyproject.toml": root if root is not None else root_manifest()}
    for name, manifest in members.items():
        files[f"packages/{name}/pyproject.toml"] = manifest
    return files


def _manifest(root: Path, package: str) -> dict[str, Any]:
    return tomllib.loads(read(root, f"packages/{package}/pyproject.toml"))


def _version(root: Path, package: str) -> str:
    return str(_manifest(root, package)["project"]["version"])


def _deps(root: Path, package: str) -> list[str]:
    return list(_manifest(root, package)["project"].get("dependencies", []))


def _dev_deps(root: Path, package: str) -> list[str]:
    return list(_manifest(root, package).get("dependency-groups", {}).get("dev", []))


def _section_deps(root: Path, package: str, section: Sequence[str]) -> list[str]:
    """The dependency list at ``section``, e.g. ``("project", "optional-dependencies", "plot")``.

    ``section`` is spelled exactly as ``molt.apply.set_dependency_specifier``'s ``section=``
    argument (test contract section 8), so a row reads the same list the primitive would splice.
    """
    node: Any = _manifest(root, package)
    for step in section:
        node = node[step]
    return list(node)


def _specifier(requirements: list[str], name: str) -> str:
    """Return the specifier of ``name``'s PEP 508 entry as ``packaging`` renders it.

    ``str(SpecifierSet)`` sorts its comparators, so the entry ``"pkg-b>=1.0.4,<2.0.0"`` comes back
    as ``"<2.0.0,>=1.0.4"``. Callers therefore compare the **set** of comma-separated comparators,
    never the string. Use :func:`_deps` when the literal on-disk spelling matters.
    """
    for text in requirements:
        requirement = Requirement(text)
        if requirement.name == name:
            return str(requirement.specifier)
    raise AssertionError(f"no requirement on {name!r} in {requirements!r}")


def _apply(root: Path, plan: FakePlan, config: FakeApplyConfig, **extra: Any) -> list[Path]:
    """Run ``apply`` against an already-materialized workspace (for repeat-run tests)."""
    changed = apply_release_plan(plan, discover_packages(root), config, cwd=root, **extra)
    return [Path(entry) for entry in changed]


class _WriteFailer:
    """Record every final-artefact write/deletion inside ``root``; optionally fail the Nth write.

    Wraps ``Path.write_bytes`` / ``Path.write_text`` / ``os.replace`` so the guard holds for a
    direct write, a buffered flush, and a temp-file + rename; and ``Path.unlink`` / ``os.remove`` /
    ``os.unlink`` so changeset deletion is observable too. Counting is scoped to **final**
    artefacts under ``root`` -- a manifest, a changelog or the lockfile -- so unrelated writes
    (pytest internals) and intermediate ``*.tmp`` files do not shift the count, whichever write
    strategy the implementation picks.

    :attr:`order` is the observed side-effect sequence, as ``root``-relative posix paths, with a
    ``"deleted:"`` prefix on a deletion. It is what pins doc 04 section 1.2's ordering, which is
    otherwise entirely invisible to an end-state assertion. A failing write is recorded before it
    raises, and **recording stops there**, so ``order`` is the flush sequence up to and including
    the artefact the run died on -- the rollback that follows is deliberately not in it (the end
    state is what proves the rollback, not the order list).

    ``fail_on=0`` (the default) never fails -- use it for pure ordering observation. Deletions do
    **not** advance the write counter; ``fail_on`` counts writes, as its name says.
    """

    COUNTED_NAMES = frozenset({"pyproject.toml", "CHANGELOG.md", "uv.lock"})

    def __init__(self, root: Path, fail_on: int = 0) -> None:
        self.root = root.resolve()
        self.fail_on = fail_on
        self.writes = 0
        self.failed = False
        self.order: list[str] = []

    def _relative(self, destination: Path) -> str | None:
        try:
            resolved = destination.resolve()
        except OSError:  # pragma: no cover - defensive on odd paths
            return None
        if not resolved.is_relative_to(self.root):
            return None
        return resolved.relative_to(self.root).as_posix()

    def _tick(self, destination: Path) -> None:
        if self.failed or destination.name not in self.COUNTED_NAMES:
            return
        relative = self._relative(destination)
        if relative is None:
            return
        self.order.append(relative)
        self.writes += 1
        if self.writes == self.fail_on:
            self.failed = True
            raise OSError(13, "simulated mid-flush failure", str(destination))

    def _tick_delete(self, destination: Path) -> None:
        if self.failed:
            return
        relative = self._relative(destination)
        if relative is None:
            return
        record = f"deleted:{relative}"
        # `Path.unlink` delegates to `os.unlink`, which is patched too; collapse the double report.
        if self.order and self.order[-1] == record:
            return
        self.order.append(record)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_write_bytes = pathlib.Path.write_bytes
        real_write_text = pathlib.Path.write_text
        real_path_unlink = pathlib.Path.unlink
        real_replace = os.replace
        real_remove = os.remove
        real_unlink = os.unlink
        failer = self

        def write_bytes(self: Path, data: Any) -> int:
            failer._tick(self)
            return real_write_bytes(self, data)

        def write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
            failer._tick(self)
            return real_write_text(self, data, *args, **kwargs)

        def replace(src: Any, dst: Any, **kwargs: Any) -> None:
            failer._tick(Path(dst))
            real_replace(src, dst, **kwargs)

        def path_unlink(self: Path, missing_ok: bool = False) -> None:
            failer._tick_delete(self)
            real_path_unlink(self, missing_ok=missing_ok)

        def remove(path: Any, **kwargs: Any) -> None:
            failer._tick_delete(Path(path))
            real_remove(path, **kwargs)

        def unlink(path: Any, **kwargs: Any) -> None:
            failer._tick_delete(Path(path))
            real_unlink(path, **kwargs)

        monkeypatch.setattr(pathlib.Path, "write_bytes", write_bytes)
        monkeypatch.setattr(pathlib.Path, "write_text", write_text)
        monkeypatch.setattr(pathlib.Path, "unlink", path_unlink)
        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(os, "remove", remove)
        monkeypatch.setattr(os, "unlink", unlink)


# ======================================================================================
# Rows 1-4 -- formatting fidelity (Adapt: JSON reformatting -> tomlkit round-trip)
# ======================================================================================

#: A manifest carrying everything ``tomlkit`` has to round-trip: a comment above and beside
#: ``version``, a multi-line dependency array with a trailing comment on one entry, a sub-table and
#: an inline table. Research doc 04 section 2.6 names exactly these shapes as the golden cases.
GNARLY_MANIFEST = (
    "# pkg-a -- manifest kept under version control.\n"
    "[project]\n"
    'name = "pkg-a"\n'
    "# The line below is rewritten by `molt version`.\n"
    'version = "1.0.0"  # do not edit by hand\n'
    "dependencies = [\n"
    '    "requests>=2.31",\n'
    '    "rich>=13.7",  # pin: see issue 42\n'
    "]\n"
    "\n"
    "[project.urls]\n"
    'Homepage = "https://example.invalid/pkg-a"\n'
    "\n"
    "[tool.molt.inline]\n"
    'table = { key = "value", other = 1 }\n'
)

TAB_MANIFEST = "[project]\n\tname = 'pkg-a'\n\tversion = '1.0.0'\n"

PLAIN_MANIFEST = '[project]\nname = "pkg-a"\nversion = "1.0.0"\n'

FORMATTING_CASES: list[tuple[str, str, str]] = [
    (
        GNARLY_MANIFEST,
        'version = "1.0.0"',
        "row 1 (Adapt): a multi-line array must not be collapsed, and comments above/beside the "
        "edited key, a sub-table and an inline table must all survive byte-for-byte",
    ),
    (
        TAB_MANIFEST,
        "version = '1.0.0'",
        "row 2 (Adapt): TOML has no free indentation, but it does allow leading whitespace before "
        "a key, and single-quoted (literal) strings; both must round-trip unchanged",
    ),
    (
        PLAIN_MANIFEST.rstrip("\n"),
        'version = "1.0.0"',
        "row 3 (Adapt): a missing trailing newline must not be added",
    ),
    (
        PLAIN_MANIFEST,
        'version = "1.0.0"',
        "row 4 (Adapt): an existing trailing newline must not be removed",
    ),
    (
        PLAIN_MANIFEST + "\n\n",
        'version = "1.0.0"',
        "rows 3+4 together: trailing blank lines are trivia too, and are equally not ours to "
        "normalize",
    ),
]


@pytest.mark.parametrize(("manifest", "target", "why"), FORMATTING_CASES)
def test_formatting_is_preserved_outside_the_edited_substring(
    tmp_path: Path, manifest: str, target: str, why: str
) -> None:
    """Rows 1-4 (Adapt) -- ``index.test.ts:153-259``; research doc 04 section 2.2.

    Upstream asserts JSON indentation width, tab indentation and trailing-newline state
    individually. TOML has no free indentation, so those four rows collapse into one stronger
    assertion: **every byte outside the edited substring is unchanged**, expressed as
    ``result == original.replace(old, new)``. That is strictly stronger than a snapshot -- it
    cannot pass if the writer re-serialises the document, reorders keys, drops a comment, collapses
    the array or normalizes quoting.

    Single-package repo (no ``[tool.uv.workspace]``), the shape ``ecosystem = "auto"`` describes and
    the shape upstream uses for these four rows.
    """
    plan_builder = FakeReleasePlan()
    changed, root = run_apply(
        tmp_path / "ws", {"pyproject.toml": manifest}, plan_builder.plan(), plan_builder.config
    )

    assert find_changed(changed, "pyproject.toml") is not None, why
    expected = manifest.replace(target, target.replace("1.0.0", "1.1.0"))
    assert read(root, "pyproject.toml") == expected, why


def test_crlf_line_endings_survive_a_version_write(tmp_path: Path) -> None:
    """Net-new (Windows-correctness) -- research doc 04 section 2.2 "CRLF | untouched".

    Upstream gets this for free: ``editJson`` splices a byte range and never re-serialises. molt
    goes through ``tomlkit``, so it is a real risk and molt claims Windows-correctness from day one
    (research README section 5 item 15). A manifest checked out with CRLF must come back with CRLF.
    """
    manifest = PLAIN_MANIFEST.replace("\n", "\r\n")
    plan_builder = FakeReleasePlan()
    _, root = run_apply(
        tmp_path / "ws", {"pyproject.toml": manifest}, plan_builder.plan(), plan_builder.config
    )

    result = read(root, "pyproject.toml")
    assert result == manifest.replace('version = "1.0.0"', 'version = "1.1.0"')
    assert "\r\n" in result and "\n\r" not in result, "CRLF must not degrade to LF or CR"


# ======================================================================================
# Rows 5, 11, 14, 15 -- version writes and the `none` gate
# ======================================================================================


def test_updates_the_version_for_one_package(tmp_path: Path) -> None:
    """Row 5 (Port) -- ``index.test.ts:262-290``. The core ``[project] version`` rewrite."""
    plan_builder = FakeReleasePlan()
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert find_changed(changed, "packages", "pkg-a", "pyproject.toml") is not None
    assert _version(root, "pkg-a") == "1.1.0"


def test_updates_versions_for_two_packages_with_different_new_versions(tmp_path: Path) -> None:
    """Row 11 (Port) -- ``index.test.ts:625-682``. Two independent version fields in one plan.

    pkg-a takes the base minor (1.0.0 -> 1.1.0) and pkg-b a major (1.0.0 -> 2.0.0). pkg-a's exact
    pin on pkg-b leaves its range, so it is rewritten too -- npm's bare ``"1.0.0"`` exact pin is
    PEP 440's ``==1.0.0``.
    """
    plan_builder = FakeReleasePlan(
        releases=[_rel("pkg-b", BumpType.MAJOR, "1.0.0", "2.0.0")],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _version(root, "pkg-b") == "2.0.0"
    assert _deps(root, "pkg-a") == ["pkg-b==2.0.0"]


def _none_type_plan() -> FakePlan:
    """pkg-b released with ``type == none``; the shape of rows 14 and 15."""
    return FakePlan(
        changesets=[_cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-b": BumpType.NONE})],
        releases=[_rel("pkg-b", BumpType.NONE, "1.0.0", "1.0.0", [BASE_CHANGESET_ID])],
    )


@pytest.mark.parametrize(
    ("dependency", "workspace_sources", "why"),
    [
        (
            "pkg-b>=1.0.0,<2.0.0",
            (),
            "row 14 (Port): a plain caret-equivalent range; `none` never reaches the rewrite",
        ),
        (
            "pkg-b==1.0.0",
            ("pkg-b",),
            "row 15 (Adapt): same rule through a uv workspace source -- upstream's "
            "`workspace:1.0.0`, pinned by the harness as `workspace:==1.0.0`",
        ),
    ],
)
def test_a_none_type_release_changes_nothing(
    tmp_path: Path, dependency: str, workspace_sources: tuple[str, ...], why: str
) -> None:
    """Rows 14-15 -- ``index.test.ts:845-961``; ``get-changelog-entry.ts:31``.

    ``type == "none"`` means ``new_version == old_version``, so there is nothing to write for the
    released package and nothing that could push a dependent out of range. Both manifests must come
    back byte-identical.

    **DIVERGENCE -- decided here.** Upstream still rewrites the *released* package's manifest for a
    ``none`` release, because the ``version`` edit is unconditional (``index.ts:153``); the write is
    a byte-identical no-op, and its path is nevertheless pushed onto ``changedFiles``
    (``index.ts:161-164``) and asserted to be there (``index.test.ts:894`` and ``:953`` both do
    ``if (!pkgPathB) throw``). molt reports **neither** manifest: under buffer-then-flush the edit
    is computed, compared, and dropped when it changes nothing, so nothing is written and a file
    that was never written was not touched. The practical difference is real -- ``molt version``
    passes this list to ``git add``, and staging a byte-identical file is noise.
    """
    pkg_a = member_manifest(
        "pkg-a", "1.0.0", dependencies=[dependency], workspace_sources=workspace_sources
    )
    pkg_b = member_manifest("pkg-b", "1.0.0")
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": pkg_b}),
        _none_type_plan(),
        FakeApplyConfig(),
    )

    assert find_changed(changed, "packages", "pkg-a", "pyproject.toml") is None, why
    assert find_changed(changed, "packages", "pkg-b", "pyproject.toml") is None, (
        "a byte-identical file was not touched, so it is not reported -- upstream reports it "
        f"anyway (index.test.ts:894, :953). {why}"
    )
    assert read(root, "packages/pkg-a/pyproject.toml") == pkg_a, why
    assert read(root, "packages/pkg-b/pyproject.toml") == pkg_b, why


# ======================================================================================
# Rows 6-10, 12, 13, 16, 21 -- the range-rewrite skip rules
# ======================================================================================


def test_an_unconstrained_dependency_is_not_rewritten(tmp_path: Path) -> None:
    """Row 6 (Adapt) -- ``index.test.ts:292-347``; ``version-package.ts:93-102``.

    Python has no ``*``. The analogue of an npm range that normalises to the empty string is a PEP
    508 requirement with **no specifier at all** (``"pkg-b"``), which
    ``molt.versioning.is_unconstrained`` recognises. Such a pin accepts every version, so the author
    clearly meant it to float and molt leaves it alone.
    """
    plan_builder = FakeReleasePlan(
        changesets=[_cs("some-id", "a very useful summary", {"pkg-b": BumpType.MINOR})],
        releases=[_rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"])],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _deps(root, "pkg-a") == ["pkg-b"], "an unconstrained pin must survive verbatim"


def test_an_unconstrained_dependency_is_pinned_when_the_new_version_is_a_prerelease(
    tmp_path: Path,
) -> None:
    """Row 6, second clause -- ``version-package.ts:99-101``; doc 04 section 2.4 step 6.

    The one exception to the leave-it-alone rule: a prerelease does **not** satisfy an unconstrained
    dependency under Python's resolver defaults either (pip skips prereleases unless opted into --
    ``molt.versioning.accepts_prereleases``), so leaving the pin bare would produce an
    uninstallable tree. It must be pinned to the prerelease.

    Upstream's rule has exactly one answer for the spelling: ``getVersionRangeType("")`` returns
    ``""``, so the new range is the bare version -- an **exact pin**
    (``version-package.ts:103-105``, ``:116-125``). ``==1.1.0rc1`` is its PEP 440 form, so that is
    asserted literally. An invariant like "the result accepts 1.1.0rc1" would also pass for
    ``>=1.1.0rc1``, which is a different (and looser) rule.
    """
    plan_builder = FakeReleasePlan(
        changesets=[_cs("some-id", "a very useful summary", {"pkg-b": BumpType.MINOR})],
        releases=[_rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0rc1", ["some-id"])],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert _deps(root, "pkg-a") == ["pkg-b==1.1.0rc1"], (
        "an unconstrained pin must be narrowed to an exact pin on the prerelease, or the tree is "
        "uninstallable (version-package.ts:99-105)"
    )
    parsed = parse_range(_specifier(_deps(root, "pkg-a"), "pkg-b"))
    assert parsed is not None and satisfies(Version("1.1.0rc1"), parsed)


def test_a_workspace_sourced_dependency_range_is_rewritten(tmp_path: Path) -> None:
    """Row 7 (Adapt) -- ``index.test.ts:349-404``; doc 04 section 2.6.

    ``workspace:1.0.0`` has no PEP 508 spelling. The uv analogue is ``[tool.uv.sources]``
    ``pkg-b = { workspace = true }`` with the constraint left in the PEP 508 string, pinned by the
    harness (progress log, Session 2) as ``workspace:==1.0.0`` -> ``pkg-b==1.0.0``. Only the
    specifier moves; the source marker is untouched.
    """
    plan_builder = FakeReleasePlan(
        changesets=[_cs("some-id", "a very useful summary", {"pkg-b": BumpType.MINOR})],
        releases=[_rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"])],
    )
    pkg_a = member_manifest(
        "pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"], workspace_sources=["pkg-b"]
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": member_manifest("pkg-b", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert _deps(root, "pkg-a") == ["pkg-b==1.1.0"]
    expected = pkg_a.replace('version = "1.0.0"', 'version = "1.1.0"').replace(
        "pkg-b==1.0.0", "pkg-b==1.1.0"
    )
    assert read(root, "packages/pkg-a/pyproject.toml") == expected, (
        "the [tool.uv.sources] table and every other byte must be untouched"
    )


def test_a_workspace_source_without_a_constraint_is_not_rewritten(tmp_path: Path) -> None:
    """Row 8 (Adapt) -- ``index.test.ts:406-497``; ``version-package.ts:78-92``.

    Upstream skips three alias forms (``workspace:*``, ``workspace:^``, ``workspace:~``) because
    none carries a concrete version. In uv there is exactly **one** such spelling -- a workspace
    source whose PEP 508 string has no specifier -- which doc 04 section 2.6 names as the analogue
    of ``workspace:*``. ``workspace:^`` / ``workspace:~`` have no PEP 440 spelling at all and are
    recorded as a structural drop in ``test_deliberately_not_ported.py``.
    """
    plan_builder = FakeReleasePlan(
        changesets=[_cs("some-id", "a very useful summary", {"pkg-b": BumpType.MINOR})],
        releases=[_rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"])],
    )
    pkg_a = member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b"], workspace_sources=["pkg-b"])
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": member_manifest("pkg-b", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert read(root, "packages/pkg-a/pyproject.toml") == pkg_a.replace(
        'version = "1.0.0"', 'version = "1.1.0"'
    ), "pkg-a's own version moves; the unconstrained workspace pin does not"


def test_bump_workspace_sources_only_limits_rewrites_to_workspace_sources(tmp_path: Path) -> None:
    """Row 9 (Adapt) -- ``index.test.ts:499-589``; ``version-package.ts:70-76``.

    Upstream's ``bumpVersionsWithWorkspaceProtocolOnly`` is an **open decision** in research README
    section 6 ("keep the flag, or fold it into uv-workspace default behavior?").
    ``website/docs/config/options.md`` ("Versioning and propagation") settles it: the flag is kept
    and renamed to **``bump_workspace_sources_only``**, "Only rewrite dependency pins that are
    backed by a workspace source" -- the same name
    ``tests/engine/fake_state.FakeConfig`` already uses. With it on, pkg-a's workspace-sourced pin
    moves and pkg-c's externally-versioned pin on the same package does not.
    """
    plan_builder = FakeReleasePlan(
        changesets=[
            _cs(
                "some-id",
                "a very useful summary",
                {"pkg-b": BumpType.MINOR, "pkg-c": BumpType.MINOR},
            )
        ],
        releases=[
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"]),
            _rel("pkg-c", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"]),
        ],
        bump_workspace_sources_only=True,
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest(
                    "pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"], workspace_sources=["pkg-b"]
                ),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
                "pkg-c": member_manifest("pkg-c", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _deps(root, "pkg-a") == ["pkg-b==1.1.0"]
    assert _version(root, "pkg-c") == "1.1.0"
    assert _deps(root, "pkg-c") == ["pkg-b==1.0.0"], (
        "pkg-c's pin is not backed by a workspace source, so bump_workspace_sources_only skips it"
    )


def test_root_dependencies_are_rewritten_without_versioning_the_root(tmp_path: Path) -> None:
    """Row 10 (Adapt) -- ``index.test.ts:591-623``; ``index.ts:174-188``.

    The root manifest gets dependency edits only; its own ``version`` is never bumped. Upstream's
    ``devDependencies`` maps to a PEP 735 ``[dependency-groups] dev`` list, and its
    ``workspace:^1.0.0`` to the caret expansion ``>=1.0.0,<2.0.0`` behind a workspace source. Note
    the upper bound survives -- see
    :func:`test_upper_bound_is_preserved_when_the_lower_bound_moves`.
    """
    root_text = root_manifest(
        name="root-pkg",
        version="0.0.0",
        dev_dependencies=["pkg-a>=1.0.0,<2.0.0"],
        workspace_sources=["pkg-a"],
    )
    plan_builder = FakeReleasePlan()
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}, root=root_text),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert find_changed(changed, "pyproject.toml") is not None
    assert read(root, "pyproject.toml") == root_text.replace(
        "pkg-a>=1.0.0,<2.0.0", "pkg-a>=1.1.0,<2.0.0"
    )
    assert tomllib.loads(read(root, "pyproject.toml"))["project"]["version"] == "0.0.0", (
        "the workspace root is never versioned by apply (index.ts:174-188)"
    )


NON_RUNTIME_SECTION_CASES: list[tuple[dict[str, Any], tuple[str, ...], str]] = [
    (
        {"dev_dependencies": ["pkg-b==1.0.0"]},
        ("dependency-groups", "dev"),
        "row 12 verbatim: npm `devDependencies` -> PEP 735 `[dependency-groups] dev`",
    ),
    (
        {"optional_dependencies": {"plot": ["pkg-b==1.0.0"]}},
        ("project", "optional-dependencies", "plot"),
        "net-new: `[project.optional-dependencies]` is a third dependency section with no npm "
        "counterpart at all, and upstream's DEPENDENCY_TYPES loop (version-package.ts:44-47) has "
        "no row for it. It is a real declared constraint, so it goes stale like any other and must "
        "be rewritten -- the engine already bumps optional-dependency dependents like runtime ones "
        "(P2 decision), and a pin left behind here would contradict that",
    ),
]


@pytest.mark.parametrize(("manifest_kwargs", "section", "why"), NON_RUNTIME_SECTION_CASES)
def test_a_non_runtime_range_is_rewritten_even_when_the_dependent_is_not_bumped(
    tmp_path: Path, manifest_kwargs: dict[str, Any], section: tuple[str, ...], why: str
) -> None:
    """Row 12 (Adapt) -- ``index.test.ts:684-779``.

    A dev-only dependent takes ``none`` (``determine-dependents.ts:108-117``), so pkg-a's version
    stays at 1.0.0 -- but its **pin still moves**, because the rewrite loop runs over every
    dependency field of every release regardless of that release's own type
    (``version-package.ts:44-47``).

    The second row extends the rule to ``[project.optional-dependencies]``, which upstream has no
    analogue for and which is otherwise exercised nowhere end-to-end: an implementation that only
    walked ``[project] dependencies`` and ``[dependency-groups]`` would pass every other test in
    this file.
    """
    plan = FakePlan(
        changesets=[
            _cs(
                BASE_CHANGESET_ID,
                BASE_SUMMARY,
                {"pkg-a": BumpType.NONE, "pkg-b": BumpType.MINOR},
            )
        ],
        releases=[
            _rel("pkg-a", BumpType.NONE, "1.0.0", "1.0.0", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", **manifest_kwargs),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan,
        FakeApplyConfig(),
    )

    assert _version(root, "pkg-a") == "1.0.0", why
    assert _section_deps(root, "pkg-a", section) == ["pkg-b==1.1.0"], why
    assert _version(root, "pkg-b") == "1.1.0", why


def test_self_references_and_path_dependencies_are_skipped(tmp_path: Path) -> None:
    """Row 13 (Adapt + net-new) -- ``index.test.ts:781-843``; ``version-package.ts:31-114``.

    Two skip rules in one fixture:

    - **location, not version** (the Adapt half): upstream skips a range literally starting
      ``file:`` or ``link:`` (``version-package.ts:51-52``). Python's analogue is a PEP 508
      **direct reference** (``pkg-b @ file:///...``), which carries a location instead of a
      constraint -- the ``BARE_PATH_SOURCES`` / ``DIRECT_REFERENCE_SOURCES`` families
      ``tests/engine/fake_state.py`` documents. The guard is ``Requirement.url is not None``, since
      PEP 508 forbids combining ``@ <url>`` with a specifier at all.
    - **self-name** (the net-new half): pkg-a's own dev group pins ``pkg-a==1.0.0``, and molt leaves
      it alone.

    DIVERGENCE -- the self-name skip is **molt's rule, not a port**. Upstream's test is titled
    "should skip dependencies that have the same name as the package"
    (``index.test.ts:781``), but ``getDependencyVersionEdits`` (``version-package.ts:31-114``) has
    **no self-name check**: its only guards are "dep absent", ``startsWith("file:")`` and
    ``startsWith("link:")`` (``version-package.ts:50-52``). The upstream fixture pins
    ``devDependencies: {"self-referenced": "file:"}`` (``index.test.ts:787-789``), so what actually
    fires there is the ``file:`` guard -- the test misdescribes its own fixture. A *faithful* port
    would therefore rewrite ``pkg-a==1.0.0`` in pkg-a's own dev group to ``pkg-a==1.1.0``: an exact
    pin whose package takes a minor is out of range, and out-of-range dependencies are always
    updated (``utils.ts:69-72``). molt does not, because a package pinning itself is pathological
    and silently rewriting the pin is not obviously the right repair. **Owner decision** (see the
    phase report); if it is reversed, this assertion is the only thing that has to change.
    """
    pkg_b_dir = (tmp_path / "ws" / "packages" / "pkg-b").resolve().as_uri()
    pkg_a = member_manifest(
        "pkg-a", "1.0.0", dev_dependencies=["pkg-a==1.0.0", f"pkg-b @ {pkg_b_dir}"]
    )
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.MINOR, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": member_manifest("pkg-b", "1.0.0")}),
        plan,
        FakeApplyConfig(),
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _dev_deps(root, "pkg-a") == ["pkg-a==1.0.0", f"pkg-b @ {pkg_b_dir}"]


def test_snapshot_releases_pin_exactly_and_drop_range_modifiers(tmp_path: Path) -> None:
    """Row 16 (Port/Adapt) -- ``index.test.ts:963-1020``; ``version-package.ts:103-105``.

    In snapshot mode the rewritten value is the bare new version with **every** range modifier
    dropped -- in PEP 440 that is ``==<version>``. The fixture keeps upstream's ``1.1.0`` so the
    rule stays isolated; a real molt snapshot version is
    ``0.0.0.dev<datetime>+<tag>`` (research README section 4.3, progress log Session 2 decision 3),
    which changes the literal but not the rule.
    """
    plan_builder = FakeReleasePlan(
        changesets=[_cs("some-id", "a very useful summary", {"pkg-b": BumpType.MINOR})],
        releases=[_rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", ["some-id"])],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b>=1.0.0,<2.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
        snapshot="canary",
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _deps(root, "pkg-a") == ["pkg-b==1.1.0"], (
        "a snapshot pins exactly: the two-sided range is replaced outright, not spliced"
    )


NON_RANGE_CASES: list[tuple[str, str, str]] = [
    (
        "pkg-b @ git+https://example.invalid/pkg-b.git",
        "pkg-a @ git+https://example.invalid/pkg-a.git@main",
        "row 21 (Port): VCS direct references are locations, not constraints -- the Python "
        "analogue of npm's `latest` / `bulbasaur` dist-tags, which `validRange` rejects",
    ),
    (
        "pkg-b @ https://example.invalid/pkg_b-1.2.0.tar.gz",
        "pkg-a @ file:///nonexistent/pkg-a",
        "row 21 (Port), second family: a direct URL and a local path are equally not ranges; "
        "`Requirement.url` is set and the specifier region is empty, so there is nothing to splice",
    ),
]


@pytest.mark.parametrize(("a_on_b", "b_on_a", "why"), NON_RANGE_CASES)
def test_non_range_specifiers_are_left_alone(
    tmp_path: Path, a_on_b: str, b_on_a: str, why: str
) -> None:
    """Row 21 (Port) -- ``index.test.ts:1450-1551``; ``version-package.ts:70-76``.

    Upstream's guard is ``validRange(dep) == null``, which is why the npm dist-tags ``"latest"`` and
    ``"bulbasaur"`` survive a bump untouched. PyPI has no dist-tags (research README section 4.4),
    so the molt analogue of "a specifier that is not a range" is a PEP 508 direct reference.

    **The guard is ``Requirement.url is not None``, not ``parse_range(...) is None``.** PEP 508
    forbids combining ``@ <url>`` with a version specifier, so ``Requirement("pkg-b @ git+...")``
    has an **empty** specifier and ``molt.versioning.parse_range("")`` returns ``SpecifierSet('')``
    -- not ``None``. These fixtures are therefore skipped by the *unconstrained* rule as well, which
    masks the direct-reference rule entirely; the mask is lifted by
    :func:`test_a_direct_reference_is_left_alone_even_for_a_prerelease_new_version`, where the
    unconstrained rule's documented prerelease exception (``version-package.ts:99-101``) would
    otherwise force a rewrite.
    """
    pkg_a = member_manifest("pkg-a", "1.0.3", dependencies=[a_on_b])
    pkg_b = member_manifest("pkg-b", "1.2.0", dependencies=[b_on_a])
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.3", "1.0.4", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.PATCH, "1.2.0", "1.2.1", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws", _workspace({"pkg-a": pkg_a, "pkg-b": pkg_b}), plan, FakeApplyConfig()
    )

    assert _version(root, "pkg-a") == "1.0.4", why
    assert _version(root, "pkg-b") == "1.2.1", why
    assert _deps(root, "pkg-a") == [a_on_b], why
    assert _deps(root, "pkg-b") == [b_on_a], why


def test_a_direct_reference_is_left_alone_even_for_a_prerelease_new_version(
    tmp_path: Path,
) -> None:
    """Row 21, unmasked -- ``version-package.ts:48-52`` vs ``:99-101``; doc 04 section 2.4.

    :func:`test_non_range_specifiers_are_left_alone` cannot tell the direct-reference rule from the
    unconstrained rule, because a direct reference has an empty specifier and both rules skip an
    empty specifier. This fixture separates them: pkg-b's new version is a **prerelease**, and the
    unconstrained rule has an explicit prerelease exception -- an unconstrained pin *must* be
    narrowed to accept a prerelease or the tree is uninstallable
    (``version-package.ts:99-101``, pinned by
    :func:`test_an_unconstrained_dependency_is_pinned_when_the_new_version_is_a_prerelease`).

    With only the unconstrained rule in place, ``pkg-b @ git+https://...`` would take that exception
    and the implementation would either raise (there is no specifier region to splice) or emit the
    invalid ``pkg-b @ git+https://... ==1.1.0rc1``. The direct-reference guard has to fire first, so
    the requirement comes back byte-identical.
    """
    a_on_b = "pkg-b @ git+https://example.invalid/pkg-b.git"
    pkg_a = member_manifest("pkg-a", "1.0.0", dependencies=[a_on_b])
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0rc1", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": member_manifest("pkg-b", "1.0.0")}),
        plan,
        FakeApplyConfig(),
    )

    assert read(root, "packages/pkg-a/pyproject.toml") == pkg_a.replace(
        'version = "1.0.0"', 'version = "1.0.1"'
    ), "only pkg-a's own version may move; a direct reference has no specifier region to rewrite"


# ======================================================================================
# Rows 17-20, 22-25 -- internal dependency bumping (the update_internal_dependencies gate)
# ======================================================================================
#
# Operator mapping used throughout this block (research README section 4.1, doc 04 section 2.6):
#
#   npm `~1.2.0`  -> PEP 440 `~=1.2.0`      (native; == `>=1.2.0,<1.3.0`)
#   npm `^1.0.3`  -> PEP 440 `>=1.0.3,<2.0.0` (caret has no PEP 440 operator; molt.versioning.caret)
#   npm `2.0.0`   -> PEP 440 `==2.0.0`      (an npm bare version is an exact pin)
#
# Rows 20 and 25 are the direct analogue of upstream's `^1.0.3` -> `^2.0.0`: the new version escapes
# the caret expansion's upper bound, so **both** bounds move and `>=1.0.3,<2.0.0` becomes
# `>=2.0.0,<3.0.0`. That is not this phase inventing an answer -- it is the worked example in
# `website/docs/guides/dependency-propagation.md` ("Out of range: cascade + rewrite", which shows
# `acme-core>=1.2.0,<2.0.0` -> `acme-core>=2.0.0,<3.0.0`) and the rule doc 04 section 2.6 states
# outright: "the 'keep the leading operator' rule cannot round-trip and you must rewrite both
# bounds".


def _mutual_pair_plan(a_bump: BumpType, a_new: str, b_bump: BumpType, b_new: str) -> FakePlan:
    return FakePlan(
        changesets=[_cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": a_bump, "pkg-b": b_bump})],
        releases=[
            _rel("pkg-a", a_bump, "1.0.3", a_new, [BASE_CHANGESET_ID]),
            _rel("pkg-b", b_bump, "1.2.0", b_new, [BASE_CHANGESET_ID]),
        ],
    )


def _mutual_pair_files(*, b_on_a: str = "pkg-a>=1.0.3,<2.0.0") -> dict[str, str]:
    return _workspace(
        {
            "pkg-a": member_manifest("pkg-a", "1.0.3", dependencies=["pkg-b~=1.2.0"]),
            "pkg-b": member_manifest("pkg-b", "1.2.0", dependencies=[b_on_a]),
        }
    )


def test_patch_gate_updates_patch_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 17 (Adapt) -- ``index.test.ts:1025-1126``; ``utils.ts:74-75``.

    ``update_internal_dependencies = "patch"`` (the default): a patch bump clears the gate, so both
    pins move. This is also the primary in-range case for the **upper-bound preservation** rule --
    ``>=1.0.3,<2.0.0`` becomes ``>=1.0.4,<2.0.0`` and never ``>=1.0.4``.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(),
        _mutual_pair_plan(BumpType.PATCH, "1.0.4", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="patch"),
    )

    assert _version(root, "pkg-a") == "1.0.4"
    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.1"]
    assert _version(root, "pkg-b") == "1.2.1"
    assert _deps(root, "pkg-b") == ["pkg-a>=1.0.4,<2.0.0"]


def test_patch_gate_still_updates_dependencies_that_left_the_range(tmp_path: Path) -> None:
    """Row 18 (Adapt) -- ``index.test.ts:1127-1245``; ``utils.ts:69-72``.

    pkg-b takes ``none``, so pkg-a's in-range ``~=1.2.0`` pin on it does not move (a ``none`` is
    below the ``patch`` gate). pkg-c's exact pin ``==2.0.0`` *does* move, because 2.0.1 leaves the
    range -- and "dependencies leaving the range should always be updated" outranks the gate.
    """
    plan = FakePlan(
        changesets=[
            _cs(
                BASE_CHANGESET_ID,
                BASE_SUMMARY,
                {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH, "pkg-c": BumpType.PATCH},
            )
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.3", "1.0.4", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.NONE, "1.2.0", "1.2.0", [BASE_CHANGESET_ID]),
            _rel("pkg-c", BumpType.PATCH, "2.0.0", "2.0.1", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.3", dependencies=["pkg-b~=1.2.0"]),
                "pkg-b": member_manifest(
                    "pkg-b", "1.2.0", dependencies=["pkg-c==2.0.0", "pkg-a>=1.0.3,<2.0.0"]
                ),
                "pkg-c": member_manifest("pkg-c", "2.0.0", dependencies=["pkg-a>=1.0.3,<2.0.0"]),
            }
        ),
        plan,
        FakeApplyConfig(update_internal_dependencies="patch"),
    )

    assert _version(root, "pkg-a") == "1.0.4"
    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.0"], "pkg-b took `none`; nothing to widen for"
    assert _version(root, "pkg-b") == "1.2.0"
    assert _deps(root, "pkg-b") == ["pkg-c==2.0.1", "pkg-a>=1.0.4,<2.0.0"], (
        "list order is preserved -- a splice never reorders the array"
    )


def test_patch_gate_updates_minor_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 19 (Adapt) -- ``index.test.ts:1246-1347``. A minor clears the ``patch`` gate."""
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(),
        _mutual_pair_plan(BumpType.MINOR, "1.1.0", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="patch"),
    )

    assert _version(root, "pkg-a") == "1.1.0"
    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.1"]
    assert _deps(root, "pkg-b") == ["pkg-a>=1.1.0,<2.0.0"]


def test_patch_gate_updates_major_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 20 (Adapt) -- ``index.test.ts:1348-1449``.

    Upstream's ``^1.0.3`` -> ``^2.0.0`` moves both ends of the caret at once because ``^`` is a
    single token. The PEP 440 spelling of that caret is the two-sided ``>=1.0.3,<2.0.0``, so the
    analogue moves both ends too: ``>=2.0.0,<3.0.0``
    (``website/docs/guides/dependency-propagation.md``, "Out of range: cascade + rewrite"; doc 04
    section 2.6). Moving only the lower bound would yield the unsatisfiable ``>=2.0.0,<2.0.0``;
    dropping the upper bound would reintroduce the constraint-widening bug. Both failure modes are
    pinned by :func:`test_a_two_sided_range_escaped_by_a_major_bump_rewrites_both_bounds`.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(b_on_a="pkg-a>=1.0.3,<2.0.0"),
        _mutual_pair_plan(BumpType.MAJOR, "2.0.0", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="patch"),
    )

    assert _version(root, "pkg-a") == "2.0.0"
    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.1"]
    assert _deps(root, "pkg-b") == ["pkg-a>=2.0.0,<3.0.0"]


def test_minor_gate_does_not_update_patch_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 22 (Adapt) -- ``index.test.ts:1555-1656``.

    ``update_internal_dependencies = "minor"``: both bumps are patches and both new versions are
    still inside their pins, so nothing is rewritten -- only the two ``version`` fields move.
    """
    files = _mutual_pair_files()
    _, root = run_apply(
        tmp_path / "ws",
        files,
        _mutual_pair_plan(BumpType.PATCH, "1.0.4", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="minor"),
    )

    assert _version(root, "pkg-a") == "1.0.4"
    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.0"]
    assert _version(root, "pkg-b") == "1.2.1"
    assert _deps(root, "pkg-b") == ["pkg-a>=1.0.3,<2.0.0"]


def test_minor_gate_still_updates_dependencies_that_left_the_range(tmp_path: Path) -> None:
    """Row 23 (Adapt) -- ``index.test.ts:1657-1775``; ``utils.ts:69-72``.

    The out-of-range clause outranks ``update_internal_dependencies``, so ``pkg-c==2.0.0`` moves to
    ``==2.0.1`` even under the ``minor`` gate, while every in-range pin stays put.
    """
    plan = FakePlan(
        changesets=[
            _cs(
                BASE_CHANGESET_ID,
                BASE_SUMMARY,
                {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH, "pkg-c": BumpType.PATCH},
            )
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.3", "1.0.4", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.PATCH, "1.2.0", "1.2.1", [BASE_CHANGESET_ID]),
            _rel("pkg-c", BumpType.PATCH, "2.0.0", "2.0.1", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.3", dependencies=["pkg-b~=1.2.0"]),
                "pkg-b": member_manifest(
                    "pkg-b", "1.2.0", dependencies=["pkg-c==2.0.0", "pkg-a>=1.0.3,<2.0.0"]
                ),
                "pkg-c": member_manifest("pkg-c", "2.0.0", dependencies=["pkg-a>=1.0.3,<2.0.0"]),
            }
        ),
        plan,
        FakeApplyConfig(update_internal_dependencies="minor"),
    )

    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.0"]
    assert _deps(root, "pkg-b") == ["pkg-c==2.0.1", "pkg-a>=1.0.3,<2.0.0"]
    assert _deps(root, "pkg-c") == ["pkg-a>=1.0.3,<2.0.0"]


def test_minor_gate_updates_minor_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 24 (Adapt) -- ``index.test.ts:1776-1885``.

    pkg-a's minor clears the ``minor`` gate so pkg-b's pin on it moves (upper bound intact);
    pkg-b's patch does not, so pkg-a's pin on pkg-b stays.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(),
        _mutual_pair_plan(BumpType.MINOR, "1.1.0", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="minor"),
    )

    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.0"]
    assert _deps(root, "pkg-b") == ["pkg-a>=1.1.0,<2.0.0"]


def test_minor_gate_updates_major_bumped_internal_dependencies(tmp_path: Path) -> None:
    """Row 25 (Adapt) -- ``index.test.ts:1886-1990``. Same caret adaptation as row 20."""
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(b_on_a="pkg-a>=1.0.3,<2.0.0"),
        _mutual_pair_plan(BumpType.MAJOR, "2.0.0", BumpType.PATCH, "1.2.1"),
        FakeApplyConfig(update_internal_dependencies="minor"),
    )

    assert _deps(root, "pkg-a") == ["pkg-b~=1.2.0"]
    assert _deps(root, "pkg-b") == ["pkg-a>=2.0.0,<3.0.0"]


# --------------------------------------------------------------------------------------
# Net-new: the range-rewrite rules that replace upstream's getVersionRangeType
# --------------------------------------------------------------------------------------


def test_upper_bound_is_preserved_when_the_lower_bound_moves(tmp_path: Path) -> None:
    """Net-new, load-bearing -- research README sections 3.3 / 3.4; doc 04 section 2.4.

    This is the whole reason ``getVersionRangeType`` is a 6-row Drop
    (``tests/versioning/test_deliberately_not_ported.py``). Upstream keeps only the *leading*
    operator, so ``>=1.0.0 <2.0.0`` flattens to ``>=1.0.4`` -- the constraint-widening bug, which
    quietly allows a future breaking major into a range whose author explicitly excluded it. molt
    operates on the whole ``SpecifierSet`` and splices only the comparator that has to move.

    The negative assertion is the point: a passing ``>=1.0.4`` would mean the bug was ported.

    pkg-a is in the plan as the dependent patch the engine would have created for it -- ``apply``
    only rewrites manifests of packages that are **in the release plan**, plus the workspace root
    (``index.ts:150-188``), so a dependent left out of the plan is never visited at all.
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.PATCH, "1.0.0", "1.0.4", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b>=1.0.0,<2.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan,
        FakeApplyConfig(),
    )

    specifier = _specifier(_deps(root, "pkg-a"), "pkg-b")
    assert set(specifier.split(",")) == {">=1.0.4", "<2.0.0"}, (
        "the lower bound moves and the upper bound is preserved"
    )
    assert specifier != ">=1.0.4", "upstream's leading-operator flattening must not be ported"


def test_a_two_sided_range_escaped_by_a_major_bump_rewrites_both_bounds(tmp_path: Path) -> None:
    """Net-new, load-bearing -- doc 04 section 2.6 ("you must rewrite **both** bounds").

    ``>=1.0.3,<2.0.0`` with a new version of ``2.0.0`` is the shape where moving only the lower
    bound yields the unsatisfiable ``>=2.0.0,<2.0.0``, and dropping the upper bound instead
    reintroduces the constraint-widening bug. The answer is neither: **both** bounds move, so the
    result is ``>=2.0.0,<3.0.0``.

    This is decided, not open. ``website/docs/guides/dependency-propagation.md`` ("Out of range:
    cascade + rewrite") shows exactly this rewrite -- ``acme-core>=1.2.0,<2.0.0`` becomes
    ``acme-core>=2.0.0,<3.0.0`` -- and doc 04 section 2.6 states the rule in the same words. It is
    the PEP 440 spelling of upstream's ``^1.0.3`` -> ``^2.0.0`` (``version-package.ts:103-105``
    with ``getVersionRangeType`` returning ``"^"``), and ``tests/apply/test_edit_toml.py``'s
    operator matrix already encodes the same expectation at the primitive level.

    The assertion is the exact comparator set, compared as a set because ``str(SpecifierSet)``
    sorts its comparators. "Accepts the new version and still has some upper bound" is *not*
    enough: it also admits an absurd bound like ``<9999.0.0``, which satisfies both invariants and
    is still wrong.
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.MAJOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MAJOR, "1.0.3", "2.0.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b>=1.0.3,<2.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.3"),
            }
        ),
        plan,
        FakeApplyConfig(),
    )

    specifier = _specifier(_deps(root, "pkg-a"), "pkg-b")
    assert set(specifier.split(",")) == {">=2.0.0", "<3.0.0"}, (
        f"{specifier!r} is not the documented rewrite: both bounds move, so "
        "'>=1.0.3,<2.0.0' becomes '>=2.0.0,<3.0.0' "
        "(website/docs/guides/dependency-propagation.md; doc 04 section 2.6)"
    )
    parsed = parse_range(specifier)
    assert parsed is not None and satisfies(Version("2.0.0"), parsed), (
        "moving only the lower bound yields the unsatisfiable '>=2.0.0,<2.0.0'"
    )


def test_a_compatible_release_pin_escaped_by_a_minor_bump_moves_wholesale(tmp_path: Path) -> None:
    """Net-new -- the most Python-native operator; doc 04 section 2.6, research README section 4.1.

    ``~=1.2.0`` is PEP 440's compatible-release operator and expands to ``>=1.2.0, ==1.2.*``, so a
    **minor** bump to 1.3.0 leaves it: the out-of-range clause (``utils.ts:69-72``) forces the
    rewrite even though the operator itself never appears in upstream's ``getVersionRangeType``
    table. The whole operator moves as one token -- ``~=1.3.0`` -- exactly as upstream's ``~1.2.0``
    becomes ``~1.3.0``; splicing only a bound would be meaningless for an operator that has no
    separate bounds to splice.

    Rows 17-19 only ever move ``~=`` within its own window (``~=1.2.0`` -> ``~=1.2.1``), where the
    dependency never actually leaves the range, so this escape case was untested.
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.2.0", "1.3.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b~=1.2.0"]),
                "pkg-b": member_manifest("pkg-b", "1.2.0"),
            }
        ),
        plan,
        FakeApplyConfig(),
    )

    assert _deps(root, "pkg-a") == ["pkg-b~=1.3.0"], (
        "1.3.0 leaves `~=1.2.0` (== `>=1.2.0, ==1.2.*`), so the pin moves as a whole"
    )


def test_a_comment_beside_a_dependency_survives_a_range_rewrite(tmp_path: Path) -> None:
    """Net-new -- research README section 4.5 ("comment-preserving TOML"); doc 04 section 2.6.

    JSON let changesets ignore this problem entirely. ``pyproject.toml`` has comments, and a
    ``version`` run that stripped them would produce an unacceptable diff on the first invocation.
    The assertion is byte-equality outside the spliced substring, which also catches a writer that
    re-serialises the requirement (``"pkg-b >= 1.1.0, < 2.0.0"``) instead of splicing it.
    """
    pkg_a = (
        "[project]\n"
        'name = "pkg-a"\n'
        'version = "1.0.0"\n'
        "dependencies = [\n"
        "    # Upper bound is deliberate: pkg-b 2.x drops the sync client.\n"
        '    "pkg-b>=1.0.0,<2.0.0",  # pin: see issue 42\n'
        "]\n"
    )
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.PATCH, "1.0.0", "1.0.4", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": pkg_a, "pkg-b": member_manifest("pkg-b", "1.0.0")}),
        plan,
        FakeApplyConfig(),
    )

    expected = pkg_a.replace('version = "1.0.0"', 'version = "1.0.1"').replace(
        "pkg-b>=1.0.0,<2.0.0", "pkg-b>=1.0.4,<2.0.0"
    )
    assert read(root, "packages/pkg-a/pyproject.toml") == expected


def test_a_changeset_name_resolves_through_pep_503_normalization(tmp_path: Path) -> None:
    """Net-new -- research README section 4.5; progress log Session 2 decision 4.

    ``Foo_Bar``, ``foo-bar`` and ``foo.bar`` are the same distribution under PEP 503. Changeset
    files and manifests keep the author's literal spelling; **matching normalizes both sides**, so
    ``apply`` must index packages by normalized name or a changeset written with the PyPI display
    name silently releases nothing. Zero prior art upstream -- npm names are compared verbatim.
    """
    plan = FakePlan(
        changesets=[_cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"Foo_Bar": BumpType.MINOR})],
        releases=[_rel("Foo_Bar", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID])],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"foo-bar": member_manifest("foo-bar", "1.0.0")}),
        plan,
        FakeApplyConfig(),
    )

    assert _version(root, "foo-bar") == "1.1.0"


# ======================================================================================
# Rows 27-36 -- changelogs
# ======================================================================================

PKG_A_NEW_CHANGELOG = (
    "# pkg-a\n\n## 1.1.0\n\n### Minor Changes\n\n- Hey, let's have fun with testing!\n"
)


def test_no_changelog_is_generated_when_changelog_is_disabled(tmp_path: Path) -> None:
    """Row 27 (Port) -- ``index.test.ts:2092-2116``; ``index.ts:241-248``.

    ``changelog = false`` short-circuits before the generator is even resolved, so no
    ``CHANGELOG.md`` is written and none is reported as touched.
    """
    plan_builder = FakeReleasePlan(changelog=False)
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert find_changed(changed, "packages", "pkg-a", "CHANGELOG.md") is None
    assert not (root / "packages" / "pkg-a" / "CHANGELOG.md").exists()


def test_the_default_changelog_ref_resolves_to_the_git_generator(tmp_path: Path) -> None:
    """AC-7 (owner ruling, 2026-07-30): the prefix-strip resolution mechanism is ratified.

    ``molt.config``'s ``BUILTIN_CHANGELOG`` is the ref ``"molt.changelog.default"``, which matches
    neither the entry-point name (``"default"``) nor an importable module. ``resolve_generator``
    reaches it anyway by also trying the ref with the ``molt.changelog.`` group prefix stripped --
    the same mechanism ``molt.commit.load_provider`` uses for ``"molt.commit.default"``. Asserting
    generator identity (rather than only "no exception was raised") pins the mechanism itself: a
    branch special-cased on the literal ref would also avoid raising, but would not be this
    resolution path.
    """
    from molt.apply.generators import resolve_generator
    from molt.changelog.git import generator as git_generator

    resolved = resolve_generator(
        "molt.changelog.default", changeset_dir=tmp_path, project_root=tmp_path
    )

    assert resolved is not None
    assert resolved.generator is git_generator


def test_a_changelog_is_created_for_one_package(tmp_path: Path) -> None:
    """Row 28 (Port) -- ``index.test.ts:2118-2155``; ``index.ts:333-341``.

    No ``CHANGELOG.md`` on disk -> write ``# <name>`` + a blank line + the entry + a trailing
    newline. Heading skeleton per doc 04 section 3.1: ``## <version>`` then
    ``### <Capitalized type> Changes``.
    """
    plan_builder = FakeReleasePlan(changelog=GIT_GENERATOR)
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert find_changed(changed, "packages", "pkg-a", "CHANGELOG.md") is not None
    assert read(root, "packages/pkg-a/CHANGELOG.md") == PKG_A_NEW_CHANGELOG


def test_new_entry_is_inserted_before_an_existing_version_heading(tmp_path: Path) -> None:
    """Row 29 (Adapt -- **DIVERGENCE**) -- ``index.test.ts:2157-2200``; ``index.ts:353-357``.

    When the file already starts with a version heading (no ``# <package>`` title), the new entry is
    prepended. **Upstream emits no blank line before the old heading** -- it does
    ``templateString.trimStart() + fileData``, producing ``...testing!\\n## 1.0.0`` -- and relies on
    its prettier/oxfmt pass to repair it (doc 04 section 3.3 case 3, flagged there with a warning
    sign).

    molt does not run a formatter (``format`` defaults to ``false``) and does not need one: it emits
    the separating blank line itself. Research README section 3.4 bug 1 / section 5 item 13; this
    expectation is deliberately **not** upstream's and fails against it.
    """
    existing = "## 1.0.0\n\n### Minor Changes\n\n- Initial release\n"
    plan_builder = FakeReleasePlan(changelog=GIT_GENERATOR)
    files = _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")})
    files["packages/pkg-a/CHANGELOG.md"] = existing

    _, root = run_apply(tmp_path / "ws", files, plan_builder.plan(), plan_builder.config)

    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "## 1.1.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "\n"  # <- the blank line upstream omits
        "## 1.0.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Initial release\n"
    )


def test_new_entry_is_inserted_below_an_existing_package_title(tmp_path: Path) -> None:
    """Net-new (the common path) -- ``index.ts:359-364``; doc 04 section 3.3 case 4.

    Row 29 covers the title-less file. The everyday case is a changelog that already has both a
    ``# <package>`` title and previous entries; the new entry goes **below the title and above the
    newest existing entry** (``website/docs/guides/changelog-templates.md``, "The default
    structure": newest-first).
    """
    existing = "# pkg-a\n\n## 1.0.0\n\n### Minor Changes\n\n- Initial release\n"
    plan_builder = FakeReleasePlan(changelog=GIT_GENERATOR)
    files = _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")})
    files["packages/pkg-a/CHANGELOG.md"] = existing

    _, root = run_apply(tmp_path / "ws", files, plan_builder.plan(), plan_builder.config)

    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "# pkg-a\n"
        "\n"
        "## 1.1.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "\n"
        "## 1.0.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Initial release\n"
    )


def test_a_dependency_bump_lands_last_and_in_the_patch_section(tmp_path: Path) -> None:
    """Row 30 (Port) -- ``index.test.ts:2202-2273``; ``get-changelog-entry.ts:95-103``.

    **Load-bearing**: the dependency line is always pushed into the ``patch`` bucket and always
    last, even though pkg-a's own release is a *minor* and pkg-b's bump is a *major*. pkg-b carries
    no changesets of its own, so ``getDependencyReleaseLine`` renders the bare dependency list with
    no ``- Updated dependencies`` header line (``changelog-git/src/index.ts:22-33``), and the
    section renderer trims the two-space indent off the first line
    (``get-changelog-entry.ts:141``).
    """
    plan_builder = FakeReleasePlan(
        releases=[_rel("pkg-b", BumpType.MAJOR, "1.0.0", "2.0.0")],
        changelog=GIT_GENERATOR,
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan_builder.plan(),
        plan_builder.config,
    )

    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "# pkg-a\n"
        "\n"
        "## 1.1.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- pkg-b@2.0.0\n"
    )
    assert read(root, "packages/pkg-b/CHANGELOG.md") == "# pkg-b\n\n## 2.0.0\n", (
        "an all-empty section returns None and is filtered out entirely"
    )


def test_no_changelog_is_written_for_a_none_type_release(tmp_path: Path) -> None:
    """Row 31 (Adapt) -- ``index.test.ts:2275-2351``; ``get-changelog-entry.ts:31``.

    pkg-a takes ``none`` because its only stake in the release is a **dev-group** dependency on
    pkg-b, and ``get_changelog_entry`` returns ``None`` for ``none`` before any dependency logic
    runs -- so no ``CHANGELOG.md`` is written at all. npm ``devDependencies`` -> PEP 735
    ``[dependency-groups] dev``.
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.NONE, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.NONE, "1.0.0", "1.0.0"),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
        ],
    )
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dev_dependencies=["pkg-b==1.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan,
        FakeApplyConfig(changelog=GIT_GENERATOR),
    )

    assert find_changed(changed, "packages", "pkg-a", "CHANGELOG.md") is None
    assert not (root / "packages" / "pkg-a" / "CHANGELOG.md").exists()


def test_dev_group_dependencies_never_produce_an_updated_dependencies_line(
    tmp_path: Path,
) -> None:
    """Net-new, strengthening row 31 -- ``get-changelog-entry.ts:53-81``.

    Row 31 as written upstream is really a ``type == "none"`` test: the ``none`` short-circuit fires
    before the dependency scan, so it never exercises the dev-dependency rule it is named for. Here
    pkg-a takes a real patch of its own, so the entry *is* written -- and the dev-group bump of
    pkg-b must still be invisible in it, because only ``[project] dependencies`` feeds the "Updated
    dependencies" list (research README section 3.3; upstream also scans ``peerDependencies``, which
    molt drops).
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dev_dependencies=["pkg-b==1.0.0"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan,
        FakeApplyConfig(changelog=GIT_GENERATOR),
    )

    changelog = read(root, "packages/pkg-a/CHANGELOG.md")
    assert changelog == (
        "# pkg-a\n\n## 1.0.1\n\n### Patch Changes\n\n- Hey, let's have fun with testing!\n"
    )
    assert "Updated dependencies" not in changelog
    assert _dev_deps(root, "pkg-a") == ["pkg-b==1.1.0"], (
        "the pin still moves -- invisible in the changelog is not the same as not rewritten"
    )


def test_multi_line_same_type_summaries_are_listed_with_clean_blank_lines(
    tmp_path: Path,
) -> None:
    """Row 32 (Adapt -- **DIVERGENCE**) -- ``index.test.ts:2353-2408``.

    Three minor summaries, two of them multi-paragraph. Continuation lines are indented by exactly
    two spaces (``changelog-git/src/index.ts:14``) and the section renderer clamps the gap between
    release lines to one newline, so the bullets sit adjacent.

    **Upstream indents the blank separator line too**, emitting a line consisting of two spaces --
    trailing whitespace that only its prettier pass removes, and that ``markdownlint`` MD009 flags.
    molt emits a genuinely empty line. Research README section 3.4 / section 5 item 13; this
    expectation fails against upstream's output.
    """
    plan_builder = FakeReleasePlan(
        changesets=[
            _cs("some-id-1", "Random stuff\n\nget it while it's hot!", {"pkg-a": BumpType.MINOR}),
            _cs(
                "some-id-2",
                "New feature, much wow\n\nlook at this shiny stuff!",
                {"pkg-a": BumpType.MINOR},
            ),
        ],
        changelog=GIT_GENERATOR,
    )
    plan_builder.releases[0].changesets.extend(["some-id-1", "some-id-2"])

    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        plan_builder.plan(),
        plan_builder.config,
    )

    changelog = read(root, "packages/pkg-a/CHANGELOG.md")
    assert changelog == (
        "# pkg-a\n"
        "\n"
        "## 1.1.0\n"
        "\n"
        "### Minor Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "- Random stuff\n"
        "\n"
        "  get it while it's hot!\n"
        "- New feature, much wow\n"
        "\n"
        "  look at this shiny stuff!\n"
    )
    assert "  \n" not in changelog, (
        "no line may end in whitespace -- that is the upstream artifact molt refuses to inherit"
    )


def _dependency_line_plan(c_bump: BumpType | None = None, c_new: str = "2.1.0") -> FakePlan:
    """The rows 33-36 plan: pkg-a + pkg-b patched, optionally a third package pkg-c."""
    releases = {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.PATCH}
    plan_releases = [
        _rel("pkg-a", BumpType.PATCH, "1.0.3", "1.0.4", [BASE_CHANGESET_ID]),
        _rel("pkg-b", BumpType.PATCH, "1.2.0", "1.2.1", [BASE_CHANGESET_ID]),
    ]
    if c_bump is not None:
        releases["pkg-c"] = c_bump
        plan_releases.append(_rel("pkg-c", c_bump, "2.0.0", c_new, [BASE_CHANGESET_ID]))
    return FakePlan(
        changesets=[_cs(BASE_CHANGESET_ID, BASE_SUMMARY, releases)],
        releases=plan_releases,
    )


def test_an_updated_dependencies_line_is_added_when_a_dependency_moved(tmp_path: Path) -> None:
    """Row 33 (Port) -- ``index.test.ts:2410-2520``; ``changelog-git/src/index.ts:19-34``.

    One bullet per relevant changeset (``- Updated dependencies``, with no ``[sha]`` because the
    changeset file was never committed), then the moved dependencies as an indented list.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(),
        _dependency_line_plan(),
        FakeApplyConfig(changelog=GIT_GENERATOR, update_internal_dependencies="patch"),
    )

    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "# pkg-a\n"
        "\n"
        "## 1.0.4\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "- Updated dependencies\n"
        "  - pkg-b@1.2.1\n"
    )
    assert read(root, "packages/pkg-b/CHANGELOG.md") == (
        "# pkg-b\n"
        "\n"
        "## 1.2.1\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "- Updated dependencies\n"
        "  - pkg-a@1.0.4\n"
    )


def test_no_updated_dependencies_line_when_no_dependency_moved(tmp_path: Path) -> None:
    """Row 34 (Port) -- ``index.test.ts:2522-2628``.

    The changelog mirrors the manifest: with ``update_internal_dependencies = "minor"`` two patch
    bumps rewrite nothing (row 22), so neither entry names a dependency.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _mutual_pair_files(),
        _dependency_line_plan(),
        FakeApplyConfig(changelog=GIT_GENERATOR, update_internal_dependencies="minor"),
    )

    for package, version in (("pkg-a", "1.0.4"), ("pkg-b", "1.2.1")):
        assert read(root, f"packages/{package}/CHANGELOG.md") == (
            f"# {package}\n"
            "\n"
            f"## {version}\n"
            "\n"
            "### Patch Changes\n"
            "\n"
            "- Hey, let's have fun with testing!\n"
        )


def _three_package_files() -> dict[str, str]:
    return _workspace(
        {
            "pkg-a": member_manifest("pkg-a", "1.0.3", dependencies=["pkg-b~=1.2.0"]),
            "pkg-b": member_manifest(
                "pkg-b", "1.2.0", dependencies=["pkg-c==2.0.0", "pkg-a>=1.0.3,<2.0.0"]
            ),
            "pkg-c": member_manifest("pkg-c", "2.0.0", dependencies=["pkg-a>=1.0.3,<2.0.0"]),
        }
    )


def test_only_dependencies_that_moved_are_listed(tmp_path: Path) -> None:
    """Row 35 (Port) -- ``index.test.ts:2630-2768``.

    Under the ``minor`` gate pkg-b's entry lists **only** pkg-c (a minor, so the gate clears) and
    not pkg-a (a patch that stays in range). Both sides run the same
    ``should_update_dependency`` predicate here, which is why they agree -- but the gate is not the
    only rule that can skip a manifest rewrite, and upstream demonstrably *does* let the two
    disagree elsewhere. That case is pinned by
    :func:`test_an_unconstrained_dependency_is_not_listed_in_the_changelog_either`.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _three_package_files(),
        _dependency_line_plan(BumpType.MINOR, "2.1.0"),
        FakeApplyConfig(changelog=GIT_GENERATOR, update_internal_dependencies="minor"),
    )

    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "# pkg-a\n\n## 1.0.4\n\n### Patch Changes\n\n- Hey, let's have fun with testing!\n"
    )
    assert read(root, "packages/pkg-b/CHANGELOG.md") == (
        "# pkg-b\n"
        "\n"
        "## 1.2.1\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "- Updated dependencies\n"
        "  - pkg-c@2.1.0\n"
    )
    assert read(root, "packages/pkg-c/CHANGELOG.md") == (
        "# pkg-c\n\n## 2.1.0\n\n### Minor Changes\n\n- Hey, let's have fun with testing!\n"
    )


def test_a_dependency_that_left_the_range_is_listed_even_below_the_gate(tmp_path: Path) -> None:
    """Row 36 (Port) -- ``index.test.ts:2770-2908``.

    pkg-c takes a *patch* under the ``minor`` gate, which would normally be invisible -- but its
    exact pin ``==2.0.0`` leaves the range, so the out-of-range clause forces the rewrite (row 23)
    and the changelog line comes with it.
    """
    _, root = run_apply(
        tmp_path / "ws",
        _three_package_files(),
        _dependency_line_plan(BumpType.PATCH, "2.0.1"),
        FakeApplyConfig(changelog=GIT_GENERATOR, update_internal_dependencies="minor"),
    )

    assert read(root, "packages/pkg-b/CHANGELOG.md") == (
        "# pkg-b\n"
        "\n"
        "## 1.2.1\n"
        "\n"
        "### Patch Changes\n"
        "\n"
        "- Hey, let's have fun with testing!\n"
        "- Updated dependencies\n"
        "  - pkg-c@2.0.1\n"
    )
    assert read(root, "packages/pkg-c/CHANGELOG.md") == (
        "# pkg-c\n\n## 2.0.1\n\n### Patch Changes\n\n- Hey, let's have fun with testing!\n"
    )


def test_an_unconstrained_dependency_is_not_listed_in_the_changelog_either(
    tmp_path: Path,
) -> None:
    """Net-new (**DIVERGENCE**) -- ``get-changelog-entry.ts:53-81``, ``version-package.ts:93-102``.

    The claim that "the changelog and the manifest can never disagree" is false upstream, and this
    is the case that breaks it. ``getDependencyVersionEdits`` skips a dependency whose range
    normalises to the empty string (``version-package.ts:93-102``, the ``*`` rule -- molt's
    unconstrained ``"pkg-b"``), but the ``dependentReleases`` filter that feeds the changelog has
    **no such guard**: it checks only ``versionRange &&`` a valid range ``&&
    shouldUpdateDependencyBasedOnConfig`` (``get-changelog-entry.ts:53-81``), and ``"*"`` is a
    perfectly valid range. So upstream writes ``- Updated dependencies\\n  - pkg-b@1.1.0`` into a
    changelog for a pin it never rewrote.

    **molt emits no line.** The changelog documents what the release *did*, and an unconstrained
    dependency "never triggers a bump" (``website/docs/guides/dependency-propagation.md``), so
    claiming it was updated is simply false. The same answer is asserted from the
    ``molt.changelog`` side in ``tests/changelog/``; the two suites must not diverge on it.

    **The discriminator is constrainedness, NOT whether the pin text changed.** Do not implement
    this as "emit the line iff the manifest edit rewrote something" -- that reading is the one an
    implementer reaches for, and it is wrong. A ``workspace``-sourced bare pin is *also* never
    rewritten (see
    :func:`test_a_workspace_source_without_a_constraint_is_not_rewritten`) yet it **does** get a
    changelog line,
    because ``[tool.uv.sources]`` resolves it to ``==<old_version>`` -- a constrained edge that
    genuinely caused the dependent's release. That case is pinned at the command level by
    ``tests/cli/test_version.py`` row 27. The rule both suites implement is: **emit the line iff
    the dependency edge is version-constrained after workspace-source resolution**, i.e. iff the
    edge could have forced the release.

    The prerelease exception is unaffected: when the new version *is* a prerelease the pin is
    rewritten (see
    :func:`test_an_unconstrained_dependency_is_pinned_when_the_new_version_is_a_prerelease`), and a
    rewritten pin is a real dependency bump that belongs in the changelog.
    """
    plan = FakePlan(
        changesets=[
            _cs(BASE_CHANGESET_ID, BASE_SUMMARY, {"pkg-a": BumpType.PATCH, "pkg-b": BumpType.MINOR})
        ],
        releases=[
            _rel("pkg-a", BumpType.PATCH, "1.0.0", "1.0.1", [BASE_CHANGESET_ID]),
            _rel("pkg-b", BumpType.MINOR, "1.0.0", "1.1.0", [BASE_CHANGESET_ID]),
        ],
    )
    _, root = run_apply(
        tmp_path / "ws",
        _workspace(
            {
                "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b"]),
                "pkg-b": member_manifest("pkg-b", "1.0.0"),
            }
        ),
        plan,
        FakeApplyConfig(changelog=GIT_GENERATOR),
    )

    assert _deps(root, "pkg-a") == ["pkg-b"], "the premise: the pin is not rewritten (row 6)"
    assert read(root, "packages/pkg-a/CHANGELOG.md") == (
        "# pkg-a\n\n## 1.0.1\n\n### Patch Changes\n\n- Hey, let's have fun with testing!\n"
    ), "a pin that was not rewritten must not be reported as an updated dependency"


# ======================================================================================
# Rows 38-39 -- errors escape before any write (atomicity)
# ======================================================================================


#: Directories whose contents are not part of the working tree for this purpose. Loading a
#: file-path changelog generator leaves a ``__pycache__`` beside it, and ``.git`` churns on every
#: command; neither is something ``apply`` wrote.
_TREE_IGNORED = frozenset({".git", "__pycache__"})


def _tree(root: Path) -> set[str]:
    """Every file under ``root`` as a posix-relative path, minus :data:`_TREE_IGNORED`."""
    listing: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or _TREE_IGNORED.intersection(relative.parts):
            continue
        listing.add(relative.as_posix())
    return listing


def _assert_nothing_was_affected(repo: GitRepo, before: set[str]) -> None:
    """Assert no file was written, created or deleted.

    Upstream states this as ``git status`` containing "nothing to commit"
    (``index.test.ts:3022-3029``). That is too brittle *and* too weak for molt. Too brittle:
    loading a file-path changelog generator leaves a ``__pycache__``, which makes git report
    untracked files while nothing was affected. Too weak: git reports a **created** file only as
    ``??``, so filtering ``??`` out -- which the ``__pycache__`` noise forces -- would hide a
    partial flush that created a ``CHANGELOG.md``.

    Both halves are therefore asserted: ``--porcelain`` must show no ``M``/``A``/``D``/``R`` entry
    (nothing tracked changed), **and** the file listing must be set-equal to the snapshot taken
    before the run (nothing appeared or vanished).
    """
    entries = [
        line for line in repo.run("status", "--porcelain").stdout.splitlines() if line.strip()
    ]
    affected = [line for line in entries if not line.startswith("??")]
    assert affected == [], f"apply wrote before failing; git reports {affected}"

    after = _tree(repo.root)
    assert after == before, (
        f"apply created {sorted(after - before)} and removed {sorted(before - after)} before "
        "failing; a failed run must leave the tree exactly as it found it"
    )


def _error_fixture_files() -> dict[str, str]:
    return _workspace(
        {
            "pkg-a": member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"]),
            "pkg-b": member_manifest("pkg-b", "1.0.0"),
        }
    )


@pytest.mark.integration
@pytest.mark.git
def test_a_release_for_a_missing_package_fails_before_any_write(git_repo: GitRepo) -> None:
    """Row 38 (Port) -- ``index.test.ts:2971-3034``; ``index.ts:92-102``.

    Joining releases to packages happens *before* any I/O, so an unknown package name aborts with a
    clean tree. The assertion is a real ``git status`` reporting "nothing to commit", which is
    exactly how upstream states it -- and the only way to prove no partial write happened. Aligns
    with molt's buffer-then-flush discipline (research README section 3.4, section 5 item 12).
    """
    plan_builder = FakeReleasePlan(
        releases=[_rel("impossible-package", BumpType.MINOR, "1.0.0", "1.0.0")]
    )
    write_workspace(git_repo.root, _error_fixture_files())
    git_repo.run("add", ".")
    git_repo.commit("first commit")
    before = _tree(git_repo.root)

    with pytest.raises(Exception, match="impossible-package") as excinfo:
        _apply(git_repo.root, plan_builder.plan(), plan_builder.config)

    assert "Could not find matching package for release of" in str(excinfo.value)
    assert "nothing to commit" in git_status(git_repo.root)
    _assert_nothing_was_affected(git_repo, before)


@pytest.mark.integration
@pytest.mark.git
def test_a_failing_changelog_generator_escapes_before_any_write(
    git_repo: GitRepo, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 39 (Port) -- ``index.test.ts:3036-3106``; ``index.ts:314-322``.

    Changelog generation is the only step that runs user code or touches the network, and it is
    deliberately placed **before every write** -- doc 04 section 1.4 calls this "the only
    guaranteed-clean failure window, and it is intentional". The generator is the port of
    ``test-utils/failing-functions.ts``: both entry points raise ``no chance``.

    Upstream asserts the two escape lines with an inline snapshot of ``console.error.mock.calls``;
    here they are asserted against captured logs and captured stdio, so the test does not care
    whether molt logs them or prints them through ``rich``.
    """
    write_workspace(git_repo.root, _error_fixture_files())
    changelog_ref = write_failing_generator(git_repo.root)
    git_repo.run("add", ".")
    git_repo.commit("first commit")
    before = _tree(git_repo.root)

    plan_builder = FakeReleasePlan(changelog=changelog_ref)
    with caplog.at_level(logging.ERROR), pytest.raises(Exception, match=FAILING_GENERATOR_MESSAGE):
        _apply(git_repo.root, plan_builder.plan(), plan_builder.config)

    captured = capsys.readouterr()
    reported = "\n".join([caplog.text, captured.out, captured.err])
    for line in CHANGELOG_ESCAPE_LINES:
        assert line in reported, f"apply must report {line!r} before rethrowing"
    _assert_nothing_was_affected(git_repo, before)


# ======================================================================================
# Rows 40-42 -- changeset consumption
# ======================================================================================


def _changeset_path(root: Path) -> Path:
    return root / ".changeset" / f"{BASE_CHANGESET_ID}.md"


@pytest.mark.parametrize(
    ("config_overrides", "manifest_kwargs", "expected_to_survive", "why"),
    [
        (
            {},
            {},
            False,
            "row 40 (Port): an applied changeset is deleted (index.ts:222-223)",
        ),
        (
            {"ignore": ("pkg-a",)},
            {},
            True,
            "row 41 (Port): a changeset naming an ignored package is kept, so it can be applied "
            "once the package stops being ignored (index.ts:214-221)",
        ),
        (
            {"private_packages_version": False},
            {"private": True},
            True,
            "row 42 (Port): same guard, private branch -- with private_packages = { version = "
            "false } a private package is not versioned, so its changeset is not consumed",
        ),
    ],
)
def test_changeset_consumption_respects_the_skip_guard(
    tmp_path: Path,
    config_overrides: dict[str, Any],
    manifest_kwargs: dict[str, Any],
    expected_to_survive: bool,
    why: str,
) -> None:
    """Rows 40-42 -- ``index.test.ts:3110-3223``; ``index.ts:195-228``.

    Deletion is guarded by ``should_skip_package(pkg, ignore=..., allow_private=...)``: a changeset
    is removed only when **none** of its releases names a skipped package. ``private=True`` is the
    ``Private :: Do Not Upload`` classifier, the Python analogue of npm's ``"private": true``
    (research doc 02 section 12.1).

    The **deleted path is reported as touched**, and only the deleted one: ``index.ts:222`` pushes
    it onto ``touchedFiles`` inside the ``if (!skipped)`` branch, immediately before ``fs.rm``.
    That is not bookkeeping trivia -- ``molt version`` feeds the returned list to ``git add``
    (``cli/src/commands/version/index.ts:132-138``), so a deletion missing from the list is a
    deletion that never gets staged, and the release commit silently keeps the consumed changeset.
    """
    plan_builder = FakeReleasePlan(**config_overrides)
    plan = plan_builder.plan()
    changed, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0", **manifest_kwargs)}),
        plan,
        plan_builder.config,
        setup=lambda workspace: write_changeset_files(workspace, plan),
    )

    assert _changeset_path(root).exists() is expected_to_survive, why
    reported = find_changed(changed, ".changeset", f"{BASE_CHANGESET_ID}.md") is not None
    assert reported is not expected_to_survive, (
        "a consumed changeset must be reported so `molt version` can `git add` the deletion "
        f"(index.ts:222); a kept one must not be. {why}"
    )


# ======================================================================================
# Rows 43-44 -- what apply leaves in the working tree
# ======================================================================================


@pytest.mark.integration
@pytest.mark.git
def test_apply_does_not_commit_the_files_it_touches(git_repo: GitRepo) -> None:
    """Row 43 (Port) -- ``index.test.ts:3227-3273``; ``index.ts:231``.

    ``apply`` returns the touched paths and stops. Committing is the CLI's job
    (``cli/src/commands/version/index.ts:132-143``), and molt keeps that split -- so after a run the
    manifest is modified-but-unstaged and ``HEAD`` is still the fixture commit. Configuring a commit
    generator must not change that.
    """
    write_workspace(git_repo.root, _error_fixture_files())
    git_repo.run("add", ".")
    git_repo.commit("first commit")

    plan_builder = FakeReleasePlan(commit=True)
    changed = _apply(git_repo.root, plan_builder.plan(), plan_builder.config)

    status = git_status(git_repo.root)
    assert "Changes not staged for commit" in status
    assert "modified:   packages/pkg-a/pyproject.toml" in status
    assert "first commit" in git_repo.run("log", "-1").stdout
    assert find_changed(changed, "packages", "pkg-a", "pyproject.toml") is not None


@pytest.mark.integration
@pytest.mark.git
def test_applied_changesets_are_removed_from_the_working_tree(git_repo: GitRepo) -> None:
    """Row 44 (Port) -- ``index.test.ts:3275-3338``.

    The same deletion as row 40, seen through git: the file is gone from disk and ``git status``
    reports it as ``deleted:`` rather than the run having quietly left it behind.
    """
    plan_builder = FakeReleasePlan(commit=True)
    plan = plan_builder.plan()
    write_workspace(git_repo.root, _error_fixture_files())
    write_changeset_files(git_repo.root, plan)
    git_repo.run("add", ".")
    git_repo.commit("first commit")

    _apply(git_repo.root, plan, plan_builder.config)

    assert plan.changesets, "the fixture must actually carry a changeset"
    assert not _changeset_path(git_repo.root).exists()
    status = git_status(git_repo.root)
    for changeset in plan.changesets:
        assert f"deleted:    .changeset/{changeset.id}.md" in status


# ======================================================================================
# Row 45 replacement -- prerelease is an invocation flag, not branch state
# ======================================================================================


def test_a_pre_release_applies_the_bump_and_keeps_the_changesets(tmp_path: Path) -> None:
    """Row 45 replacement (net-new) -- research README section 4.2; doc 04 section 1.2 step 10.

    Upstream row 45 asserts that ``pre.json`` is written and shows up as modified in ``git status``.
    **molt deletes ``pre.json`` wholesale** -- prerelease is an invocation flag (``molt version
    --pre rc``), not persistent branch state -- so that row is recorded as a Drop in
    ``test_deliberately_not_ported.py`` and this is the molt-native behavior in its place.

    What survives from upstream is the half that is about ``apply``: **the version bump is still
    applied under ``--pre``, and the changeset files are not consumed.** Upstream skips deletion
    whenever ``preState != null`` and ``mode != "exit"`` (``index.ts:195``); molt's documented
    inference is the same -- changesets stay on disk across ``--pre`` runs until a plain
    ``molt version`` cuts the stable release (progress log, "Changeset-file lifecycle during
    ``--pre``"). **Stated as an assumption; the owner must confirm.**
    """
    plan_builder = FakeReleasePlan()
    plan_builder.releases[0].new_version = "1.1.0rc0"
    plan = plan_builder.plan()

    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        plan,
        plan_builder.config,
        pre="rc",
        setup=lambda workspace: write_changeset_files(workspace, plan),
    )

    assert _version(root, "pkg-a") == "1.1.0rc0"
    assert _changeset_path(root).exists(), (
        "changesets are consumed by the stable release, not by a --pre run"
    )
    assert not (root / ".changeset" / "pre.json").exists(), (
        "molt has no pre.json: prerelease is an invocation flag (research README section 4.2)"
    )


def test_a_plain_run_after_a_pre_run_consumes_the_changesets(tmp_path: Path) -> None:
    """Row 45 replacement, second half -- research README section 4.2.

    The stable release that follows one or more ``--pre`` runs is a plain ``apply``, and *that* is
    what consumes the changeset files. Without this the prerelease flow would leak changesets
    forever.
    """
    pre_builder = FakeReleasePlan()
    pre_builder.releases[0].new_version = "1.1.0rc0"
    pre_plan = pre_builder.plan()
    _, root = run_apply(
        tmp_path / "ws",
        _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")}),
        pre_plan,
        pre_builder.config,
        pre="rc",
        setup=lambda workspace: write_changeset_files(workspace, pre_plan),
    )
    assert _changeset_path(root).exists()

    stable_builder = FakeReleasePlan()
    stable_builder.releases[0].old_version = "1.1.0rc0"
    _apply(root, stable_builder.plan(), stable_builder.config)

    assert _version(root, "pkg-a") == "1.1.0"
    assert not _changeset_path(root).exists()


# ======================================================================================
# Net-new: the lockfile and the atomicity moat
# ======================================================================================

UV_LOCK = """version = 1
requires-python = ">=3.11"

[[package]]
name = "pkg-a"
version = "1.0.0"
source = { editable = "packages/pkg-a" }
"""


@pytest.mark.integration
@pytest.mark.slow
def test_uv_lock_is_updated_by_apply(tmp_path: Path) -> None:
    """Net-new -- research README section 5 item 2; doc 04 section 1.2.

    changesets never touches a lockfile (README section 3.4: "cosmetic in npm; **load-bearing in
    Python**"), so there is no upstream row to port. In a uv workspace ``uv.lock`` records every
    member's version, and a ``version`` run that left it stale would make the very next
    ``uv sync --locked`` fail (``website/docs/ecosystems/uv.md:52-54``). It is the **last** write in
    the ordering (doc 04 section 1.2), after manifests, changelogs and changeset deletion.

    Marked ``integration`` **and** ``slow`` deliberately: the refresh is specified as a real
    ``uv lock`` subprocess, not a TOML edit -- "for uv, one ``uv lock`` call"
    (``website/docs/cli/version.md:23``, ``ecosystems/overview.md:38``,
    ``guides/versioning.md:24``). That is a spawned external tool, which is exactly what the
    ``integration`` marker means in ``pyproject.toml``, and it is the slowest step ``apply`` has.

    The assertion is two-sided on purpose. ``'version = "1.1.0"' in ...`` alone passes for an
    implementation that *appends* the new version and leaves the stale record above it -- which is
    the failure mode that breaks ``--locked`` installs. The old version must be gone.
    """
    files = _workspace({"pkg-a": member_manifest("pkg-a", "1.0.0")})
    files["uv.lock"] = UV_LOCK
    plan_builder = FakeReleasePlan()
    changed, root = run_apply(tmp_path / "ws", files, plan_builder.plan(), plan_builder.config)

    assert find_changed(changed, "uv.lock") is not None, "uv.lock must be reported as touched"
    lock = read(root, "uv.lock")
    assert 'name = "pkg-a"\nversion = "1.1.0"' in lock, (
        "the recorded version must move with the manifest, on pkg-a's own [[package]] record"
    )
    assert 'version = "1.0.0"' not in lock, (
        "a stale record left behind is what breaks `uv sync --locked`; the update replaces, it "
        "does not append"
    )


def _atomicity_fixture(root: Path) -> tuple[Path, FakeReleasePlan, str, str]:
    """Materialize a workspace exercising **all four** artefact kinds ``apply`` writes.

    Two manifests, two ``CHANGELOG.md`` files, a ``uv.lock`` and a changeset on disk. Anything less
    proves atomicity for a subset: the previous fixture had ``changelog=False`` and no lockfile, so
    a rollback that restored the manifests and left a written ``CHANGELOG.md`` behind was invisible.

    Returns ``(root, plan_builder, pkg_a_manifest, pkg_b_manifest)``.
    """
    pkg_a = member_manifest("pkg-a", "1.0.0", dependencies=["pkg-b==1.0.0"])
    pkg_b = member_manifest("pkg-b", "1.0.0")
    files = _workspace({"pkg-a": pkg_a, "pkg-b": pkg_b})
    files["uv.lock"] = UV_LOCK
    plan_builder = FakeReleasePlan(
        releases=[_rel("pkg-b", BumpType.MAJOR, "1.0.0", "2.0.0")],
        changelog=GIT_GENERATOR,
    )
    write_workspace(root, files)
    write_changeset_files(root, plan_builder.plan())
    return root, plan_builder, pkg_a, pkg_b


#: The documented flush order (doc 04 section 1.2, shared brief section 9) for
#: :func:`_atomicity_fixture`, as :attr:`_WriteFailer.order` records it -- root-relative posix
#: paths, ``"deleted:"``-prefixed for a deletion: every manifest, then every ``CHANGELOG.md``, then
#: the changeset deletion, then ``uv.lock`` last.
#:
#: Derivation, and the one thing this list decides beyond the docs. Doc 04 section 1.2 orders the
#: *kinds*: manifest (version field first, then dep ranges -- one write, since molt buffers), then
#: ``CHANGELOG.md`` (step 7), then changeset deletion (step 10), then ``uv.lock`` (molt-native,
#: research README section 5 item 2). What it does not settle is the two-package case, because
#: upstream's step 7 is a per-release loop that interleaves (manifest-a, changelog-a, manifest-b,
#: changelog-b) purely as an artefact of writing as it goes -- the very design molt replaces with
#: buffer-then-flush. **Chosen: grouped by kind**, which is what a buffered flush produces
#: naturally and what the section-9 ordering reads as. Flagged for the owner; if the interleaved
#: order is preferred instead, this constant is the only thing that changes.
EXPECTED_FLUSH_ORDER = [
    "packages/pkg-a/pyproject.toml",
    "packages/pkg-b/pyproject.toml",
    "packages/pkg-a/CHANGELOG.md",
    "packages/pkg-b/CHANGELOG.md",
    f"deleted:.changeset/{BASE_CHANGESET_ID}.md",
    "uv.lock",
]


def test_side_effects_are_flushed_in_the_documented_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Net-new -- doc 04 section 1.2; shared brief section 9.

    The ordering is stated in the research and is **invisible to every end-state assertion in this
    file**: a run that deletes the changesets first, or writes ``uv.lock`` first, or writes the
    changelogs before the manifests, produces byte-identical results on a successful run. It only
    becomes observable when something fails part-way -- which is precisely when it decides whether
    the tree is recoverable, and precisely the situation buffer-then-flush exists for.

    Two properties are pinned:

    1. **Content before consumption.** Changeset deletion comes after every content write, so a
       failure mid-flush can never have consumed the input that produced the output.
    2. **The lockfile is last.** It is derived from the manifests, so it must be refreshed from
       their final state, and it is the one step that shells out (``uv lock``).
    """
    root, plan_builder, _, _ = _atomicity_fixture(tmp_path / "ws")

    failer = _WriteFailer(root)
    with monkeypatch.context() as patched:
        failer.install(patched)
        _apply(root, plan_builder.plan(), plan_builder.config)

    assert failer.order == EXPECTED_FLUSH_ORDER, (
        "the flush order is doc 04 section 1.2's: manifests, changelogs, changeset deletion, "
        "lockfile"
    )


def test_a_failed_flush_leaves_the_tree_pristine_and_a_retry_bumps_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Net-new, the correctness moat -- research README section 3.4 / section 5 item 12.

    This is the bug changesets has **no test for at all**. Upstream's per-release loop writes each
    manifest as it goes (``index.ts:150-172``), so a failure on package 3 of 10 leaves packages 1-2
    bumped with their changesets still on disk; the next release plan is computed from the versions
    now on disk, so re-running ``version`` **double-bumps** them (doc 04 section 1.4). Manual
    ``git checkout`` is upstream's only recovery.

    molt buffers every edit and flushes at the end. The failure is forced on the **fourth** write,
    which by :data:`EXPECTED_FLUSH_ORDER` is the second ``CHANGELOG.md`` -- deliberately *after*
    both manifests and one changelog have already landed, so the rollback has something of every
    kind to undo. Failing on the third write would be weaker: the first changelog would never have
    been created, and a rollback that restores manifests but abandons changelogs would still pass.

    Asserted: **zero net writes across all four artefact kinds** (both manifests byte-identical, no
    ``CHANGELOG.md`` on disk, ``uv.lock`` untouched), and a retry that produces **exactly one** bump
    each, not two.

    The failer wraps ``Path.write_bytes`` / ``Path.write_text`` / ``os.replace``, so it holds for a
    direct write, a buffered flush and a temp-file + rename alike.
    """
    root, plan_builder, pkg_a, pkg_b = _atomicity_fixture(tmp_path / "ws")

    failer = _WriteFailer(root, fail_on=4)
    with monkeypatch.context() as patched:
        failer.install(patched)
        with pytest.raises(OSError, match="simulated mid-flush failure"):
            _apply(root, plan_builder.plan(), plan_builder.config)

    assert failer.order == EXPECTED_FLUSH_ORDER[:4], (
        "the premise: both manifests and one changelog must already be on disk when it dies"
    )
    assert read(root, "packages/pkg-a/pyproject.toml") == pkg_a, (
        "a failed flush must not leave a package bumped -- that is the double-bump bug"
    )
    assert read(root, "packages/pkg-b/pyproject.toml") == pkg_b
    assert not (root / "packages" / "pkg-a" / "CHANGELOG.md").exists(), (
        "the changelog written before the failure must be rolled back too -- a partial rollback "
        "that only restores manifests leaves a changelog entry for a release that never happened"
    )
    assert not (root / "packages" / "pkg-b" / "CHANGELOG.md").exists()
    assert read(root, "uv.lock") == UV_LOCK
    assert _changeset_path(root).exists()

    _apply(root, plan_builder.plan(), plan_builder.config)

    assert _version(root, "pkg-a") == "1.1.0", "exactly one bump, not 1.2.0"
    assert _version(root, "pkg-b") == "2.0.0", "exactly one bump, not 3.0.0"


def test_a_failed_flush_does_not_consume_the_changesets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Net-new, the other half of the atomicity moat -- doc 04 section 1.4, "Mid-deletion".

    Upstream deletes changeset files in an unordered ``Promise.all`` (``index.ts:197-227``), so a
    failure part-way through leaves some deleted and some not, and "re-run replays only the
    survivors -> those packages get bumped a second time". A failed run must consume nothing.

    The failure is forced on the **last** write (``uv.lock``, write 5 of 5 by
    :data:`EXPECTED_FLUSH_ORDER`), which is *after* the deletion point -- so the changeset has
    genuinely been unlinked by the time the run dies and the rollback has to put it back. Failing
    earlier would assert nothing: the deletion would not have been reached.
    """
    root, plan_builder, _, _ = _atomicity_fixture(tmp_path / "ws")

    failer = _WriteFailer(root, fail_on=5)
    with monkeypatch.context() as patched:
        failer.install(patched)
        with pytest.raises(OSError, match="simulated mid-flush failure"):
            _apply(root, plan_builder.plan(), plan_builder.config)

    assert failer.order == EXPECTED_FLUSH_ORDER, (
        "the premise: the run must have reached the deletion before it failed"
    )
    assert _changeset_path(root).exists(), "a failed run consumes nothing"
    assert _version(root, "pkg-a") == "1.0.0"
    assert _version(root, "pkg-b") == "1.0.0"
    assert not (root / "packages" / "pkg-a" / "CHANGELOG.md").exists()
