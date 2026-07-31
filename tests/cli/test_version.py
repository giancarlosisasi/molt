"""Conformance tests for the ``molt version`` command seam.

Every test maps to a row of ``roadmap/research/test-suite/05-cli-version.md``
(``packages/cli/src/commands/version/version.test.ts``: 68 ``it`` blocks across 15 ``describe``
groups, scored **27 Port / 20 Adapt / 21 Drop**). Mechanics and ``file:line`` citations come from
``roadmap/research/changesets-03-cli-and-ux.md`` section 3 (3.1 ordering of operations, 3.2 what
``applyReleasePlan`` mutates, 3.3 commit behaviour, 3.4 ``--snapshot``), section 9.2 (the failure
catalogue), section 10 (footguns) and section 11.6 (the exit-code contract). The flag surface is
``website/docs/cli/version.md`` and ``website/docs/cli/pre.md``, which win over the group file
wherever the two disagree.

Where two *website* pages disagree the CLI reference page is normally the more specific oracle and
wins -- but not unconditionally, because a docs page can simply be wrong. One such conflict is
live: ``website/docs/cli/pre.md:45`` says the first ``--pre`` run **consumes** the pending
changesets while ``website/docs/concepts/prerelease.md:51`` says they stay in place. molt follows
``prerelease.md`` and treats ``cli/pre.md:45`` as a docs bug, because the counter mechanism is
impossible under the other reading; the full argument is recorded on
:func:`test_a_pre_run_leaves_the_changesets_on_disk`.

Scope -- this file is the **command seam**, not the engine
----------------------------------------------------------
``tests/engine/test_assemble.py`` owns release-plan assembly (bump math, the dependent fixpoint,
fixed/linked resolution, the snapshot suffix) and ``tests/apply/test_apply.py`` owns manifest and
changelog writing (the PEP 508 splice, flush order, atomicity, ``uv.lock``). Rows that are really
engine or apply rows are covered here **thinly** -- "did the command wire it up and did it land on
disk" -- and each such test names the sibling file that owns it in depth. What this file owns
exclusively: preconditions and validation, the exit-code contract, the ordering of operations, what
actually exists on disk after a run, and the flags (``--ignore``, ``--snapshot*``, ``--pre``,
``--dry-run``).

Seams assumed here (flagged for the owner)
------------------------------------------
1. **``molt.commands.version.run``**, not ``molt.cli.commands.version.run``.
   ``openspec/changes/adopt-typer-cli-shell/design.md`` D7 (``from molt.commands.<x> import run``),
   its ``tasks.md`` 1.2 and research doc 03 section 11.5 all agree; ``MILESTONES.md`` and the phase
   file say ``molt.cli.commands.*`` and are the outlier.
2. **Keyword-only options named after the long flags**: ``run(cwd=..., ignore=[...],
   snapshot: str | bool | None = None, pre=None, dry_run=False)``. ``--snapshot`` is **one**
   keyword, not a ``snapshot``/``snapshot_name`` pair: ``None`` means no snapshot, ``True`` means
   ``--snapshot`` with no name, and ``"pr-123"`` means a named snapshot. The shell collapses all
   three spellings (``--snapshot``, ``--snapshot=pr-123``, ``--snapshot-name pr-123``) into it
   (``cli-shell`` spec, "Optional-value ``--snapshot``"; ``design.md`` D4;
   ``tests/cli/test_cli.py`` pins the collapse). Four reasons this is the seam, and not
   ``SnapshotParams`` or the two-keyword pair:

   a. ``SnapshotParams.commit`` **cannot** be filled at the shell boundary. Upstream resolves it in
      the command (``version/index.ts:105-114``): ``git.getCurrentCommitId()`` is called *only*
      when the configured prerelease template mentions ``{commit}``/``{commit-short}``. That needs
      a config read plus a git call -- both command-layer work, both explicit Non-Goals of the CLI
      shell change -- so a shell-built ``SnapshotParams`` would be a half-built object the command
      has to rebuild anyway. It is not a pass-through.
   b. Passing ``SnapshotParams`` would drag an engine type across the CLI seam, defeating
      ``design.md`` D7's lazy-import rule.
   c. Three of the four seams already spell it as one ``snapshot`` keyword -- engine
      ``snapshot=None | SnapshotParams(...)`` (``contracts/test-contract.md`` section 5), apply
      ``apply_release_plan(..., snapshot: str | None)``
      (``tests/apply/fake_release_plan.py:559``, ``tests/apply/test_apply.py:910``), shell
      ``run(..., snapshot=True | "pr-123")`` (``tests/cli/test_cli.py``). The command was the
      outlier.
   d. ``str | bool | None`` is a faithful port of upstream's ``VersionOptions.snapshot?: string |
      boolean`` (``version/index.ts:23``) and, unlike the pair, has **no illegal state** -- the
      pair can spell ``snapshot=False, snapshot_name="x"``, which means nothing. Upstream splits
      the value inside the command, one line above the ``commit`` resolution of (a):
      ``tag: options.snapshot === true ? undefined : options.snapshot`` (``:107``).

   Harness ruling F1/section 2 (reviewer R1's resolution). ``snapshot_name`` as a separate keyword
   is the rejected alternative and appears nowhere in this file outside this paragraph.
3. **Injectable collaborators** ``console=`` / ``git=`` / ``prompts=``, each defaulting to the real
   implementation (design D6: "Commands and the shell depend on the protocols only"). Every test
   injects the ``tests/cli/fake_cli.py`` doubles, so no test here touches a real terminal or a real
   git binary except the one marked ``integration``.
4. **Template coherence failures are user-facing.** Upstream throws them out of
   ``assembleReleasePlan`` and lets the top-level funnel turn them into exit 1
   (``version.test.ts:1742-1825``). molt is expected to convert them to ``ExitError(1)`` in the
   command so the message reaches the user without a traceback. Rows 35/36 assert that shape.
5. **A free-form ``--pre`` value is rejected by the command**, not only by Typer's enum. Defense in
   depth: ``run`` is a public seam and ``website/docs/cli/pre.md`` says arbitrary tags "are not
   allowed", not "are unrepresentable in the parser".

Load-bearing facts pinned here
------------------------------
- **No changesets -> warn + exit 1** (``index.ts:92-98``; ``version.test.ts:79-97``). v3 semantics;
  v2 exited 0. This is the single most important row in the group file.
- **Validation runs before any write** (``index.ts:37-65``): the ``--ignore``/config conflict, an
  unknown ``--ignore`` name and an unskipped dependent of a skipped package are accumulated into
  one message block and exit 1 before ``readChangesets`` is even called.
- **``fixed`` force-releases the whole group, ``linked`` only aligns members that were already
  releasing.** A ``fixed`` member with no changeset still gets a bare ``## <version>`` changelog
  header (``version.test.ts:899-1002``); a ``linked`` member with no changeset is left completely
  alone (``:1052-1092``). Blurring these two is how a monorepo release tool becomes wrong.
- **A dev-group dependent gets its pin rewritten but is never version-bumped, and propagation stops
  there** (``version.test.ts:2140-2222``; ``determine-dependents.ts:108-117``).
- **``--pre`` does not consume the changesets**; a plain ``molt version`` does
  (``website/docs/concepts/prerelease.md:51,58``; upstream ``apply-release-plan/src/index.ts:195``
  deletes only when not in pre mode). ``website/docs/cli/pre.md:45`` asserts the opposite and is a
  docs bug -- see the precedence note above and
  :func:`test_a_pre_run_leaves_the_changesets_on_disk`. ``--snapshot`` *does* consume them
  (``website/docs/concepts/snapshots.md``, "What a snapshot run does").
- **Snapshot mode force-disables commit** (``index.ts:56``; ``version.test.ts:1607-1650``).
- **The summary is written into the changelog literally.** JS's ``String.replace`` expands ``$'``
  and ``$&`` (``version.test.ts:598-647``); Python's ``re.sub`` expands ``\\1`` and ``\\g<0>``. Both
  classes are asserted.

Deliberate divergences from upstream (research README section 3.4; doc 03 section 11.7)
---------------------------------------------------------------------------------------
1. **A failed version commit exits non-zero.** Upstream logs
   ``Changesets ran into trouble committing your files`` and still exits 0
   (``version/index.ts:145-147``, doc 03 section 10.10) -- a silent CI failure. Pinned by
   :func:`test_a_failed_commit_is_fatal`. The upstream spelling is recorded in
   ``tests/cli/test_deliberately_not_ported.py``.
2. **No ``--allow-empty`` on the version commit.** Upstream's ``git.commit`` always passes it
   (``packages/git/src/index.ts:21``, doc 03 section 10.18), so a run that changed nothing still
   creates a commit. molt only commits what it actually wrote; pinned by
   :func:`test_the_version_commit_does_not_use_allow_empty`.
3. **The snapshot version is PEP 440.** ``0.0.0-<tag>-<datetime>`` is illegal, so molt composes
   ``0.0.0.dev<14-digit-datetime>[+<local>]`` (harness Session 2 decision 3, closing research open
   decision #2; ``website/docs/concepts/snapshots.md``). The 9-row template matrix is re-derived
   accordingly -- see :data:`SNAPSHOT_TEMPLATE_CASES`.
4. **``pre.json`` never exists.** Prerelease is the stateless ``--pre {a,b,rc,dev}`` flag (research
   README section 4.2). The whole upstream ``pre`` describe block (19 rows) is a recorded Drop; the
   three rows in it that pin *non-pre* behavior (59, 60, 63) are re-ported here as real tests.
5. **``peerDependencies`` do not exist** (research README section 4.4), so rows 28/29 are Drops and
   row 30 keeps only its dev/optional half.

Why explicit literals instead of syrupy snapshots
-------------------------------------------------
Same reasoning as ``tests/apply/test_apply.py`` and ``tests/engine/test_assemble.py``: a module
behind ``importorskip`` never runs, so ``--snapshot-update`` can produce no baseline, and the first
update run after the product lands would record whatever the implementation emits -- silently
blessing upstream's broken output on exactly the divergence rows that exist to reject it. The
``snapshot`` marker therefore goes unused here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest
from packaging.version import Version
from tests.cli.fake_cli import (
    FROZEN_COMMIT,
    FROZEN_COMMIT_SHORT,
    FakeGit,
    RecordingConsole,
    ScriptedPrompts,
    changeset_ids,
    read_changelog,
    read_manifest,
    read_manifests,
)

pytest.importorskip("molt.errors", reason="build step 9 - molt.errors is a TDD target")
pytest.importorskip("molt.commands.version", reason="build step 6 - `molt version` is a TDD target")

from molt.commands.version import run  # pyrefly: ignore[missing-import]
from molt.errors import ExitError  # pyrefly: ignore[missing-import]

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.conftest import FrozenClock, ProjectBuilder

pytestmark = pytest.mark.functional


# ======================================================================================
# Constants
# ======================================================================================

#: The warning text for an empty ``.changeset/`` (``version/index.ts:96``, echoed verbatim by
#: ``website/docs/cli/version.md``). ``website/docs/guides/versioning.md`` writes "No pending
#: changesets found." instead -- a docs bug, reported to the owner; the CLI reference page is the
#: more specific oracle and wins. Only the substring is asserted so the sentence may be reworded.
NO_CHANGESETS_FRAGMENT = "unreleased changesets"

#: ``vi.setSystemTime("2021-12-13T00:07:30.879Z")`` (``version.test.ts:1847``), i.e. the shared
#: ``frozen_clock``. ``{datetime}`` is ``YYYYMMDDHHmmss``; ``{timestamp}`` is epoch milliseconds.
FROZEN_DATETIME_TOKEN = "20211213000730"
FROZEN_TIMESTAMP_TOKEN = "1639354050879"


# ======================================================================================
# Local helpers
#
# Named so pytest cannot collect them: `python_functions = "test"` is a *prefix* match, so a
# helper called `test_setup` would be collected as a test (harness Session 4 process note).
# ======================================================================================


def run_version(
    root: Path,
    console: RecordingConsole,
    git: FakeGit,
    **options: Any,
) -> Any:
    """Drive ``molt.commands.version.run`` through the seam described in the module docstring."""
    return run(cwd=root, console=console, git=git, **options)


def versions(root: Path) -> dict[str, str | None]:
    """``{package name: [project].version}`` for every workspace member."""
    return {name: manifest.version for name, manifest in read_manifests(root).items()}


def deps(root: Path, name: str) -> list[str]:
    """``[project].dependencies`` of one member, as PEP 508 strings in file order."""
    return list(read_manifest(root, name).dependencies)


def dev_deps(root: Path, name: str) -> list[str]:
    """``[dependency-groups].dev`` of one member (the PEP 735 analogue of devDependencies)."""
    return list(read_manifest(root, name).dev_dependencies)


def optional_deps(root: Path, name: str) -> dict[str, list[str]]:
    """``[project.optional-dependencies]`` of one member, extra -> requirement strings."""
    return {
        extra: list(reqs) for extra, reqs in read_manifest(root, name).optional_dependencies.items()
    }


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` as ``{posix relative path: bytes}``.

    The ``--dry-run`` guard: read as bytes so newline translation cannot make a rewritten file
    look unchanged on Windows (same reasoning as ``ProjectBuilder.write_changeset``).
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def manifest_path(root: Path, package: str | None) -> Path:
    """Path of a member's ``pyproject.toml``, or the workspace root's when ``package`` is None."""
    if package is None:
        return root / "pyproject.toml"
    return root / "packages" / package / "pyproject.toml"


def append_table(root: Path, package: str | None, table: str, lines: Sequence[str]) -> None:
    """Append a TOML table that ``ProjectBuilder`` cannot express, preserving the existing bytes.

    ``tests/conftest.py``'s builder has no writer for ``[tool.uv.sources]`` (the uv analogue of
    npm's ``workspace:`` protocol) nor for root-level dependency groups, and the test contract says
    not to grow it for one suite. Written with ``write_bytes`` for the same reason as everything
    else here.
    """
    path = manifest_path(root, package)
    block = "\n".join(["", f"[{table}]", *lines, ""])
    path.write_bytes(path.read_bytes() + block.encode("utf-8"))


def mark_workspace_sources(root: Path, package: str | None, *names: str) -> None:
    """Declare ``names`` as uv workspace members of ``package``'s manifest.

    ``pkg-b = { workspace = true }`` under ``[tool.uv.sources]`` is molt's analogue of npm's
    ``workspace:`` protocol; the version constraint stays in the PEP 508 requirement string
    (research doc 04 section 2.6; ``tests/apply/fake_release_plan.py``'s ``workspace_sources``).
    So ``workspace:1.0.0`` is ``pkg-b==1.0.0`` plus this marker, and ``workspace:*`` -- upstream's
    "exact pin of the current version, **not** a wildcard" (``determine-dependents.ts:199-202``) --
    is a *bare* marked requirement, which is what distinguishes it from an unconstrained
    dependency that never triggers a bump (research README section 3.3).
    """
    append_table(
        root, package, "tool.uv.sources", [f"{name} = {{ workspace = true }}" for name in names]
    )


def write_file(root: Path, relative: str, text: str) -> Path:
    """Materialize one LF-only file under ``root``; returns its path."""
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def changelog_headings(root: Path, name: str) -> list[str]:
    """Every ``##``/``###`` heading of a member's ``CHANGELOG.md``, in file order."""
    text = read_changelog(root, name)
    if text is None:
        return []
    return [line.strip() for line in text.splitlines() if line.startswith("##")]


# ======================================================================================
# Section 1 -- preconditions and validation (rows 1-4, 65-68)
#
# `version/index.ts:30-65`: read packages -> ensure `.changeset/` -> read config -> accumulate
# every validation message -> print them as one block and exit 1. Nothing is read from
# `.changeset/*.md` and nothing is written until this passes.
# ======================================================================================


def test_no_changesets_warns_and_exits_1(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 1 (Port). ``version.test.ts:79-97``; ``version/index.ts:92-98``.

    **The load-bearing row of the whole group.** v3 exits **1** where v2 exited 0
    (research README section 3.2), and ``website/docs/cli/version.md`` restates it as a documented
    CI contract. A pipeline that treats any non-zero as fatal breaks on a no-op release, so this
    number is part of molt's public surface.

    Both halves matter: it is a ``warn``, not an ``error`` (doc 03 section 9.1 -- recoverable), and
    the code is 1, not 0.
    """
    tmp_project.add_package("pkg-a", "1.0.0")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git)

    assert excinfo.value.code == 1
    assert console.contains(NO_CHANGESETS_FRAGMENT, level="warn"), console.calls
    assert console.errors == [], "an empty buffer is recoverable, so it must not be an error"
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}, "nothing may be written"


def test_an_unknown_ignore_name_is_rejected(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 2 (Port). ``version.test.ts:99-122``; ``validateIgnoredPackageNames`` at
    ``version/index.ts:159-180``.

    A typo in ``--ignore`` must not silently release the package the user meant to skip, so an
    unmatched name is fatal rather than a warning. Note the contrast with the *config* ``ignore``
    list, where an unmatched entry only warns (``tests/config/test_parse.py`` row 24) -- the CLI
    flag is typed once by a human, the config list is a checked-in glob set.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, ignore=["pkg-c"])

    assert excinfo.value.code == 1
    assert len(console.errors) == 1, "every validation message is printed as one block"
    message = console.errors[0]
    assert "pkg-c" in message
    assert "--ignore" in message
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}
    assert changeset_ids(tmp_project.root) == ["some-id-0"], "validation precedes every write"


def test_a_published_dependent_of_a_skipped_package_must_also_be_skipped(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 3 (Port). ``version.test.ts:124-154``; ``validateSkippedDependents`` at
    ``version/index.ts:182-235``.

    Skipping pkg-b while releasing pkg-a would publish a pkg-a whose pin on pkg-b is stale, so
    molt refuses and names the package to add. The message must name **both** packages and the
    flag, because that is the whole remediation.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, ignore=["pkg-b"])

    assert excinfo.value.code == 1
    message = "\n".join(console.errors)
    assert "pkg-a" in message and "pkg-b" in message
    assert "--ignore" in message
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.0"}


def test_a_dev_only_dependent_of_a_skipped_package_is_exempt(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 68 (Port). ``version.test.ts:3845-3875``; ``version/index.ts:191-199``.

    The skipped-dependent graph is built with ``ignoreDevDependencies: true``: a stale dev-group
    range on a skipped package cannot break a consumer's install, so it is not worth failing the
    release over. The comment at ``version/index.ts:191-194`` is explicit that
    ``assemble-release-plan`` uses a *different* graph -- one that does include dev groups --
    because it still has to rewrite the range.
    """
    tmp_project.add_package("pkg-a", "1.0.0", dev_deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, ignore=["pkg-b"])

    assert console.errors == [], "a dev-group dependent cannot break a published consumer"
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0"}


def test_the_ignore_flag_and_the_config_ignore_list_are_mutually_exclusive(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 4 (Port). ``version.test.ts:156-189``; ``version/index.ts:37-45``.

    Upstream refuses to merge the two rather than pick a precedence, and molt keeps that: silently
    unioning them (or silently letting one win) is how a package gets released that somebody
    explicitly listed as skipped. ``website/docs/cli/version.md`` documents the rule under
    "Validation".
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(ignore=["pkg-b"])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, ignore=["pkg-a"])

    assert excinfo.value.code == 1
    message = "\n".join(console.errors)
    assert "--ignore" in message and "config" in message.lower(), (
        "the message must name both sources so the user knows which one to delete"
    )
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.0"}


def test_an_unversioned_private_dependent_does_not_trip_the_skipped_dependent_check(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 65 (Port). ``version.test.ts:3741-3774``; ``version/index.ts:214-221``.

    A private package never uploads, so it can safely depend on a skipped one -- the comment at
    ``:216-220`` says exactly that, and extends it to private packages with non-PyPI publish
    targets, which prebundle their dependencies. Without this exemption every app in a monorepo
    would have to be listed in ``--ignore`` alongside the library it consumes.

    **This row does not discriminate the exemption, and upstream's does not either.** With
    ``private_packages = {version = false}`` *and* pkg-a private, pkg-a is not versionable at all,
    so the second ``shouldSkipPackage`` guard already suppresses the message: deleting the private
    exemption at ``:215`` leaves this row green. It is ported anyway because it is the upstream row
    and because the fixture shape ("an app that consumes an ignored library") is the real-world
    motivation. The row that actually kills the mutant is
    :func:`test_a_versioned_private_package_may_depend_on_an_ignored_package` (row 67), where the
    dependent *is* being versioned so only the private check can exempt it.

    The end-state assertions below are the second half of the row: upstream asserts *only*
    ``mockedLogger.error`` and swallows the throw (``:3767-3771``), which would also pass against a
    run that blew up after the validation block. Both packages stay at ``1.0.0`` -- ``--ignore``
    freezes pkg-b even though a changeset names it (row 6) and ``private_packages.version = false``
    takes pkg-a out of versioning -- and the changeset survives, because a changeset naming a
    skipped package still has work to do later (row 7).
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"], private=True)
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(private_packages={"version": False})
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, ignore=["pkg-b"])

    assert console.errors == []
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.0"}, (
        "both are frozen -- pkg-b because --ignore freezes a package even when a changeset names "
        "it (row 6, test_an_ignored_package_that_is_not_a_dependent_is_left_byte_identical), "
        "pkg-a because private_packages.version = false takes it out of versioning entirely"
    )
    assert changeset_ids(tmp_project.root) == ["some-id-0"], (
        "a changeset naming a skipped package is not consumed either "
        "(apply-release-plan/src/index.ts:209-213)"
    )


def test_a_dev_dependent_on_an_unversioned_private_package_does_not_error(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 66 (Port). ``version.test.ts:3776-3809``.

    Both exemptions at once -- the dependency is dev-group *and* the skipped package is an
    unversioned private one -- so neither arm of ``validateSkippedDependents`` may fire.
    """
    tmp_project.add_package("pkg-a", "1.0.0", dev_deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    tmp_project.set_config(private_packages={"version": False})
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert console.errors == []
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0"}


def test_a_versioned_private_package_may_depend_on_an_ignored_package(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 67 (Port). ``version.test.ts:3811-3843``; ``version/index.ts:215-221``.

    The private check at ``:215`` is on the **dependent**, and it runs *before* the
    "is it also skipped" check -- so a private package that molt is happily versioning is still
    allowed to depend on an ignored one. Upstream runs this row with an empty config object to
    prove the exemption is a default, not something ``privatePackages`` has to opt into.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"], private=True)
    tmp_project.add_package("pkg-b", "1.0.0", private=True)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, ignore=["pkg-b"])

    assert console.errors == []
    assert versions(tmp_project.root)["pkg-a"] == "1.1.0"


def test_every_validation_failure_is_reported_in_one_block(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new. ``version/index.ts:34,59-65`` -- messages accumulate, then one ``log.error``.

    Upstream never asserts the accumulation directly, but it is observable and it is the reason
    ``--ignore typo-a --ignore typo-b`` tells you about both names in one run instead of making
    you fix them one at a time. Asserting ``len(console.errors) == 1`` also pins that molt does
    not print one error per problem.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError):
        run_version(tmp_project.root, console, fake_git, ignore=["typo-a", "typo-b"])

    assert len(console.errors) == 1, console.errors
    assert "typo-a" in console.errors[0] and "typo-b" in console.errors[0]


# ======================================================================================
# Section 2 -- the baseline release (rows 5, 11, 13, 14, 15, 16)
# ======================================================================================


def test_released_packages_are_bumped_and_changelogs_written(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 5 (Port). ``version.test.ts:192-234``.

    The end-to-end baseline: read changesets, assemble, apply. Bump math is owned by
    ``tests/versioning/test_bump.py`` and changelog shape by ``tests/changelog/``; what this row
    owns is that ``molt version`` wires them together and the result reaches disk.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.1"}
    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert "## 1.1.0" in changelog
    assert "### Minor Changes" in changelog
    assert "This is a summary" in changelog


def test_multiple_changesets_aggregate_to_the_highest_bump_per_package(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Rows 14 + 15 (Port). ``version.test.ts:650-695`` and ``:697-748`` (near-duplicates).

    Two changesets, aggregated independently per package: pkg-a takes the highest of
    ``minor``/``patch`` and pkg-b only ever saw a patch. Merged into one test because upstream's
    two rows use the same fixture and differ only in which assertion they emphasise.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    tmp_project.write_changeset(
        "some-id-1", {"pkg-a": "patch", "pkg-b": "patch"}, "This is another summary"
    )

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.1"}
    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert "This is a summary" in changelog
    assert "This is another summary" in changelog, "both summaries land, not just the winner's"


def test_consumed_changeset_files_are_deleted(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 16 (Port). ``version.test.ts:750-797``.

    Upstream counts ``.changeset`` entries 3 -> 1 because its config lives in
    ``.changeset/config.json``. molt's config lives in ``[tool.molt]`` (or ``.molt/config.json``),
    so the directory holds only changesets and the count is 2 -> 0. This is half of the
    no-double-bump guarantee: the delete is part of the same flush as the bump
    (``website/docs/guides/versioning.md``, "Atomic by design").
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    tmp_project.write_changeset("some-id-1", {"pkg-a": "patch"}, "This is another summary")
    assert changeset_ids(tmp_project.root) == ["some-id-0", "some-id-1"]

    run_version(tmp_project.root, console, fake_git)

    assert changeset_ids(tmp_project.root) == []
    assert (tmp_project.root / ".changeset").is_dir(), "the folder itself stays"


def test_a_dot_prefixed_changeset_is_neither_applied_nor_deleted(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 11 (Port). ``version.test.ts:493-547``; ``read/src/index.ts:50-57``.

    Renaming a changeset to ``.something.md`` is the documented way to park it, so both halves are
    load-bearing: it must not be applied (pkg-b stays put) **and** it must survive the run. An
    implementation that filtered dotfiles on read but deleted every ``*.md`` on flush would pass
    the first half and destroy the user's parked work.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    tmp_project.write_changeset(".ignored-temporarily", {"pkg-b": "major"}, "Not yet")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0"}
    assert changeset_ids(tmp_project.root) == [".ignored-temporarily"]


AGENT_FILES = [
    ("README.md", "the changeset folder's own readme (case-insensitive match)"),
    ("ReadMe.MD", "same file, spelled the way a Windows checkout might"),
    ("AGENTS.md", "agent instructions parked next to the changesets"),
    ("CLAUDE.md", "agent instructions parked next to the changesets"),
    ("GEMINI.md", "agent instructions parked next to the changesets"),
]


@pytest.mark.parametrize(("filename", "why"), AGENT_FILES)
def test_non_changeset_markdown_files_survive_the_run(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    filename: str,
    why: str,
) -> None:
    """Row 11's sibling rule -- doc 03 section 10.20; ``read/src/index.ts:9,50-57``.

    The filter is by **filename**, so the fixture is deliberately a *syntactically valid changeset*:
    an ``AGENTS.md`` whose front matter happens to parse is not far-fetched (YAML front matter is
    everywhere), and it is the only fixture that can tell a name filter from a parse failure. A
    content-shaped guard would leave both assertions below green while silently releasing pkg-b.

    ``tests/changeset/test_read.py`` owns the "not read" half at the reader level; what this level
    owns is the destructive half -- the flush must not delete the file. Losing a repo's
    ``CLAUDE.md`` to a release command would be a spectacular bug and upstream has no test for it.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    body = '---\n"pkg-b": major\n---\n\nNotes for agents, not a release.\n'
    parked = write_file(tmp_project.root, f".changeset/{filename}", body)

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0"}, why
    assert parked.exists(), why
    assert parked.read_bytes() == body.encode("utf-8"), why


REPLACEMENT_PATTERNS = [
    (
        "a summary with special replacement patterns `react$` $'",
        "JS `String.replace`: `$'` is the text after the match (askWithEditor / changelog splice)",
    ),
    (
        "a summary with $& in it",
        "JS `String.replace`: `$&` is the whole match",
    ),
    (
        r"a summary with a python backreference \1 in it",
        r"Python `re.sub`: `\1` is group 1 -- the same class of bug, different engine",
    ),
    (
        r"a summary with \g<0> in it",
        r"Python `re.sub`: `\g<0>` is the whole match",
    ),
    (
        "a summary with a literal backslash \\ and $ in it",
        "a lone backslash makes `re.sub` raise `bad escape` rather than silently corrupt",
    ),
]


@pytest.mark.parametrize(("summary", "why"), REPLACEMENT_PATTERNS)
def test_replacement_patterns_in_a_summary_are_written_literally(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    summary: str,
    why: str,
) -> None:
    """Row 13 (Adapt). ``version.test.ts:598-647``.

    Upstream splices the new entry in with ``String.replace``, whose replacement string expands
    ``$'``/``$&``/``$1``; the test exists because a summary containing them was silently mangled.
    The Python analogue is ``re.sub``, whose *replacement* expands ``\\1``/``\\g<0>`` and raises on
    a stray backslash. So both families are asserted, and the rule is the same: the splice must use
    literal replacement (``str`` slicing, or ``re.sub`` with a function).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    write_file(
        tmp_project.root,
        "packages/pkg-a/CHANGELOG.md",
        "# pkg-a\n\n## 1.0.0\n\n### Major Changes\n\n- a very useful summary for the change\n",
    )
    tmp_project.write_changeset("some-id-0", {"pkg-a": "major"}, summary)

    run_version(tmp_project.root, console, fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert summary in changelog, why
    assert "## 2.0.0" in changelog and "## 1.0.0" in changelog, "the old entry survives"


# ======================================================================================
# Section 3 -- `--ignore` and the config `ignore` list (rows 6, 7)
# ======================================================================================


def test_an_ignored_package_that_is_not_a_dependent_is_left_byte_identical(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 6 (Port). ``version.test.ts:236-280``.

    Upstream asserts exact object equality on the whole manifest, which is stronger than "the
    version did not change" -- it also catches a run that reformats or reorders an ignored
    package's manifest. Byte equality is the Python form of that, and it matters more here than in
    JSON-land: ``pyproject.toml`` has comments and a naive tomlkit round-trip can lose them.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["some-external>=1.0.0"])
    tmp_project.set_config(ignore=["pkg-a"])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    before = manifest_path(tmp_project.root, "pkg-a").read_bytes()

    run_version(tmp_project.root, console, fake_git)

    assert manifest_path(tmp_project.root, "pkg-a").read_bytes() == before
    assert read_changelog(tmp_project.root, "pkg-a") is None, "a skipped package gets no changelog"


def test_an_ignored_package_is_frozen_but_its_pin_on_a_released_dependency_still_moves(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 7 (Port; range spelling Adapt). ``version.test.ts:282-338``.

    The subtle half of ``ignore``: the package's **version** is frozen, but its dependency pin is
    still rewritten, because leaving it pinned to a version that no longer exists in the workspace
    would break the tree for everyone else. ``>=1.0.0,<2.0.0`` is the PEP 440 spelling of upstream's
    ``^1.0.0``; ``1.0.1`` stays inside it, and an in-range patch still moves the lower bound under
    the default ``update_internal_dependencies = "patch"``
    (``tests/apply/test_apply.py`` owns that gate).
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(ignore=["pkg-a"])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    tmp_project.write_changeset("some-id-1", {"pkg-b": "patch"}, "This is another summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.1,<2.0.0"]
    assert changeset_ids(tmp_project.root) == ["some-id-0"], (
        "a changeset naming a skipped package is NOT consumed -- it still has work to do once the "
        "package stops being skipped (apply-release-plan/src/index.ts:209-213)"
    )


def test_ignore_matching_is_pep503_normalized(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new (research README section 4.5; harness Session 2 decision 4).

    ``Foo_Bar`` and ``foo-bar`` are the same distribution to PyPI, so ``--ignore foo-bar`` must
    skip a package spelled ``Foo_Bar`` in its manifest -- and must not be reported as "not found in
    the project". Python has no scopes and no case sensitivity in distribution names; this has no
    upstream analogue at all, and getting it wrong turns a skip into a silent release.
    """
    tmp_project.add_package("Foo_Bar", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"Foo_Bar": "minor"}, "This is a summary")
    tmp_project.write_changeset("some-id-1", {"pkg-b": "patch"}, "This is another summary")

    run_version(tmp_project.root, console, fake_git, ignore=["foo-bar"])

    assert console.errors == [], "a normalized name is 'found in the project'"
    assert versions(tmp_project.root) == {"Foo_Bar": "1.0.0", "pkg-b": "1.0.1"}


# ======================================================================================
# Section 4 -- commit behaviour (rows 8, 9, 10 + doc 03 sections 10.10 / 10.18)
# ======================================================================================


def test_nothing_is_committed_when_commit_is_not_configured(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 8 (Port). ``version.test.ts:340-368``; ``version/index.ts:132,152-156``.

    ``commit`` defaults to ``false`` (``tests/config/test_parse.py``), and the default path ends
    with "review them and commit at your leisure". A release tool that commits without being asked
    is a release tool people stop trusting.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert fake_git.commits == []
    assert fake_git.added == []
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}


def test_a_configured_commit_stages_manifests_changelogs_changesets_and_the_lockfile(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 9 (Port; file names Adapt). ``version.test.ts:370-437``; ``version/index.ts:132-138``.

    Upstream stages each ``package.json``, each ``CHANGELOG.md`` and each consumed changeset, using
    paths **relative to cwd**. molt's list adds ``uv.lock`` -- the lockfile update is a
    Python-specific step changesets does not perform (research README section 4.5), and staging the
    manifests without it would commit a workspace whose lockfile is already stale.

    Paths are compared as posix so the assertion is separator-agnostic on Windows.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary too"
    )

    run_version(tmp_project.root, console, fake_git)

    staged = {entry.replace("\\", "/") for entry in fake_git.added}
    assert "packages/pkg-a/pyproject.toml" in staged
    assert "packages/pkg-a/CHANGELOG.md" in staged
    assert "packages/pkg-b/pyproject.toml" in staged
    assert "packages/pkg-b/CHANGELOG.md" in staged
    assert ".changeset/some-id-0.md" in staged
    assert "uv.lock" in staged, "molt updates the lockfile, so it must be part of the same commit"
    assert not any(entry.startswith("/") or ":" in entry for entry in staged), (
        "paths are staged relative to cwd (version/index.ts:137)"
    )


def test_the_commit_message_names_every_release(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 10 (Adapt). ``version.test.ts:439-491``; ``cli/src/commit/index.ts:11-27``.

    Upstream's exact wording (``RELEASING: Releasing 2 package(s)`` ...) is changesets-specific, so
    molt owns the sentence. What must survive the reword is the *content*: one line per released
    package carrying ``name`` and the **new** version, and a count that matches. ``type == "none"``
    releases are excluded from both (``commit/index.ts:13-17``), which is why pkg-c is here.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.add_package("pkg-c", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset(
        "some-id-0",
        {"pkg-a": "minor", "pkg-b": "patch", "pkg-c": "none"},
        "This is a summary too",
    )

    run_version(tmp_project.root, console, fake_git)

    assert len(fake_git.commits) == 1
    message = fake_git.commits[0]
    assert "pkg-a" in message and "1.1.0" in message
    assert "pkg-b" in message and "1.0.1" in message
    assert "pkg-c" not in message, "a `none` release is not a release (commit/index.ts:13-17)"
    assert "2" in message, "the count excludes `none` releases too"


def test_the_version_commit_does_not_use_allow_empty(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Divergence, decided here. Upstream: ``packages/git/src/index.ts:21`` (doc 03 section 10.18).

    Upstream's ``git.commit`` always passes ``--allow-empty``, so a ``version`` run that wrote
    nothing still creates an empty commit -- noise in the history and a "release" tag pointing at
    no change. molt does not: ``version`` only reaches the commit step when it actually wrote
    files, so an empty commit can only mean something went wrong and should surface, not be
    papered over.

    Recorded as a divergence in ``tests/cli/test_deliberately_not_ported.py``. **This is the one
    assertion to change if the owner prefers upstream's behavior.**
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    commit_calls = fake_git.of("commit")
    assert len(commit_calls) == 1
    keywords = dict(commit_calls[0].args[1])
    assert keywords.get("allow_empty", False) is False, keywords


def test_a_failed_commit_is_fatal(tmp_project: ProjectBuilder, console: RecordingConsole) -> None:
    """Divergence (doc 03 sections 10.10 / 11.7 item 5). Upstream: ``version/index.ts:145-147``.

    Upstream logs ``Changesets ran into trouble committing your files`` and **exits 0**, so a CI
    job that versioned but failed to commit reports success and the next job pushes nothing. molt
    treats it as fatal. The files are already written at this point -- the failure is in the commit
    step, not the release -- so the exit code is the only signal available.
    """

    class FailingGit(FakeGit):
        def commit(self, message: str, **kwargs: Any) -> str:
            super().commit(message, **kwargs)
            raise RuntimeError("fatal: unable to write new index file")

    failing_git = FailingGit()
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, failing_git)

    assert excinfo.value.code == 1, "a failed commit must not report success (doc 03 section 10.10)"
    assert console.errors, "and it must say what went wrong"
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}, "the release itself already landed"


def test_version_never_creates_a_git_tag(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new; re-port of row 60's surviving half. ``version.test.ts:3405-3443``.

    Row 60 pins ``privatePackages: {tag: false, version: true}`` -- a private package that is
    versioned but not tagged. molt drops the ``private_packages.tag`` sub-key entirely (PyPI has no
    dist-tags -- ``website/docs/config/options.md``; recorded in
    ``tests/config/test_deliberately_not_ported.py``), so the surviving rule is stronger and
    simpler: **``molt version`` never tags anything**. Tagging is ``molt git-tag``'s job, which is
    what makes "version in one job, tag in another" work at all.
    """
    tmp_project.add_package("pkg-a", "1.0.0", private=True)
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "patch"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1"}, "private packages are versioned"
    assert fake_git.tags == [], "tagging belongs to `molt git-tag`, not to `molt version`"


# ======================================================================================
# Section 5 -- fixed and linked (rows 17-21 + row 63)
#
# THE distinction: `fixed` force-releases the whole group; `linked` only aligns the members that
# were already going to release. Every other difference follows from that one.
# ======================================================================================


def test_fixed_group_members_bump_together(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 17 (Port). ``version.test.ts:800-842``.

    One changeset naming only pkg-a moves pkg-b as well, to the *same* version. Group membership,
    not the dependency graph, is what pulls pkg-b in.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(fixed=[["pkg-a", "pkg-b"]])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.1.0"}


def test_every_fixed_group_member_gets_a_changelog_header(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 19 (Port). ``version.test.ts:899-1002`` (four inline snapshots, two runs).

    **This is the moat row.** A ``fixed`` member with no changeset of its own is genuinely
    released, so it gets a real changelog entry -- a bare ``## 1.1.0`` heading with no ``###``
    section under it, because there is nothing to say. That "empty" heading is the visible proof
    that ``fixed`` force-releases; contrast
    :func:`test_a_linked_member_without_a_changeset_is_left_alone`, where the same fixture under
    ``linked`` produces no changelog at all.

    The second run pins accretion: the new entry goes on top and the previous one survives.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(fixed=[["pkg-a", "pkg-b"]])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert changelog_headings(tmp_project.root, "pkg-b") == ["## 1.1.0"], (
        "a force-released member gets a heading and nothing under it"
    )
    pkg_a_first = read_changelog(tmp_project.root, "pkg-a")
    assert pkg_a_first is not None
    assert "## 1.1.0" in pkg_a_first
    assert "This is a summary" in pkg_a_first
    assert "pkg-b@1.1.0" in pkg_a_first, "the co-bumped member is listed as a dependency update"

    tmp_project.write_changeset("some-id-1", {"pkg-a": "minor"}, "This is a summary")
    run_version(tmp_project.root, console, fake_git)

    assert changelog_headings(tmp_project.root, "pkg-b") == ["## 1.2.0", "## 1.1.0"], (
        "newest entry first; the previous run's entry is not overwritten"
    )
    assert versions(tmp_project.root) == {"pkg-a": "1.2.0", "pkg-b": "1.2.0"}


def test_ignore_overrides_a_fixed_group_cobump(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 18 (Port; range spelling Adapt). ``version.test.ts:844-897``.

    ``ignore`` wins over ``fixed``: pkg-a stays at 1.0.0 even though its group partner moved. Its
    pin on pkg-b still tracks, for the same reason as row 7.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(fixed=[["pkg-a", "pkg-b"]], ignore=["pkg-a"])
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.1,<2.0.0"]


def test_linked_group_members_converge_to_the_group_maximum(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 20 (Port). ``version.test.ts:1005-1050``.

    Both members are already releasing (one minor, one patch), so ``linked`` aligns them onto the
    highest resulting version. pkg-b jumps ``0.1.0 -> 1.1.0`` -- a much bigger move than its own
    ``patch`` -- which is exactly what "linked" buys and what surprises people about it.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "0.1.0")
    tmp_project.set_config(linked=[["pkg-a", "pkg-b"]])
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.1.0"}


def test_a_linked_member_without_a_changeset_is_left_alone(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 21 (Port). ``version.test.ts:1052-1092``.

    The mirror image of :func:`test_every_fixed_group_member_gets_a_changelog_header`, on the same
    fixture: under ``linked``, pkg-b was not going to release, so it does not -- no version change
    and **no changelog file at all**. Upstream's row only asserts "does not crash"; the version and
    the absent changelog are what actually separate ``linked`` from ``fixed``, so both are asserted
    here.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(linked=[["pkg-a", "pkg-b"]])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0"}
    assert read_changelog(tmp_project.root, "pkg-b") is None, (
        "linked aligns releases, it does not create them (contrast the fixed row)"
    )


def test_a_linked_member_is_not_cobumped_by_a_dev_dependency_release(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 63 (re-ported; the group file scores it Drop only because of its ``pre >`` nesting).
    ``version.test.ts:3637-3684``.

    This case never enters pre mode, so the rule is molt's too: a dev-group dependency does not
    make its holder a release, and ``linked`` cannot manufacture one out of a non-release. pkg-a's
    dev pin on pkg-b is still rewritten -- that is the row-42 rule, applied to a ``linked`` group.
    """
    tmp_project.add_package("pkg-a", "1.0.0", dev_deps=["pkg-b==1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(linked=[["pkg-a", "pkg-b"]])
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.1"}
    assert dev_deps(tmp_project.root, "pkg-a") == ["pkg-b==1.0.1"]
    assert read_changelog(tmp_project.root, "pkg-a") is None


# ======================================================================================
# Section 6 -- workspace sources and multi-section pins (rows 22-27, 30)
#
# npm's `workspace:` protocol maps to uv's `[tool.uv.sources] pkg = { workspace = true }` with the
# constraint left in the PEP 508 string. The marker-to-constraint resolution itself is pinned at
# the unit level by `tests/engine/test_dependents_graph.py`
# (`resolve_workspace_range`); these rows check the command wires it through to disk.
# ======================================================================================


def test_a_workspace_sourced_pin_is_rewritten_when_the_dependency_bumps(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 22 (Adapt). ``version.test.ts:1095-1148``.

    Upstream's ``workspace:1.0.0 -> workspace:1.0.1``. The molt spelling of a workspace source
    carrying an explicit constraint is the PEP 508 requirement plus the uv marker
    (harness Session 2: ``workspace:^1.0.0`` is unspellable in PEP 440, so an explicit workspace
    constraint is written as a real specifier set).
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    mark_workspace_sources(tmp_project.root, "pkg-a", "pkg-b")
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b==1.0.1"]


def test_a_bare_workspace_source_patch_bumps_its_dependent_without_changing_the_pin(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 23 (Adapt). ``version.test.ts:1150-1195``; ``determine-dependents.ts:199-202``.

    ``workspace:*`` "actually means the current exact version, and not a wildcard" -- the source
    comment is emphatic because the consequence is counter-intuitive: it **maximises** dependent
    churn. Every bump of pkg-b leaves the implied exact pin, so pkg-a is released every time, while
    the written requirement never changes because there is no specifier in it to rewrite.

    A *bare* requirement without the uv marker is the opposite (unconstrained: never bumps) -- see
    :func:`test_an_unconstrained_dependency_never_triggers_an_out_of_range_bump`. The marker is the
    only difference between the two fixtures, which is what makes this pair discriminating.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b"])
    tmp_project.add_package("pkg-b", "1.0.0")
    mark_workspace_sources(tmp_project.root, "pkg-a", "pkg-b")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b"]


def test_workspace_root_references_to_bumped_members_are_rewritten(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 24 (Adapt). ``version.test.ts:1197-1235``; ``apply-release-plan/src/index.ts:174-188``
    (added in ``3.0.0-next.9``, PR #2163).

    The workspace root is not a release -- its own version never moves -- but it *does* reference
    members, so its pins have to track or ``uv sync`` at the root resolves to a version that no
    longer exists. Upstream's ``workspace:^1.0.0 -> workspace:^1.1.0`` becomes the PEP 440 caret
    expansion with its lower bound moved (``tests/apply/test_apply.py`` owns the rewrite rule).
    """
    tmp_project.add_package("pkg-b", "1.0.0")
    append_table(tmp_project.root, None, "dependency-groups", ['dev = ["pkg-b>=1.0.0,<2.0.0"]'])
    mark_workspace_sources(tmp_project.root, None, "pkg-b")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    root_manifest = read_manifest(tmp_project.root, None)
    assert root_manifest.version == "0.0.0", "the workspace root is never itself released"
    assert list(root_manifest.dev_dependencies) == ["pkg-b>=1.1.0,<2.0.0"]


def test_an_in_range_patch_does_not_bump_a_caret_style_dependent(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 25 (Adapt). ``version.test.ts:1237-1282``.

    The range-satisfaction gate: ``1.0.1`` still satisfies ``>=1.0.0,<2.0.0`` (the PEP 440 caret
    expansion), so pkg-a is not a release at all -- no version change, no changelog, and its
    manifest is never opened, so the pin does not move either. Compare the next row, where a minor
    escapes a tilde and everything happens.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    mark_workspace_sources(tmp_project.root, "pkg-a", "pkg-b")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.0,<2.0.0"]
    assert read_changelog(tmp_project.root, "pkg-a") is None


def test_an_out_of_range_minor_patch_bumps_a_tilde_style_dependent(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 26 (Adapt). ``version.test.ts:1284-1329``.

    ``~=1.0.0`` is PEP 440's compatible-release operator and expands to ``>=1.0.0, ==1.0.*``, so
    ``1.1.0`` leaves it. The dependent takes a **patch** (never the dependency's own bump type --
    ``determine-dependents.ts:100-107``) and the whole ``~=`` token moves as one
    (``tests/apply/test_apply.py`` pins that spelling).
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b~=1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    mark_workspace_sources(tmp_project.root, "pkg-a", "pkg-b")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1", "pkg-b": "1.1.0"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b~=1.1.0"]


def test_a_dependent_changelog_records_the_dependency_update(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 27 (Adapt). ``version.test.ts:1331-1388``.

    A package released only because a dependency moved still gets a changelog, and its single entry
    is the dependency line -- in the **Patch** section, always last (research README section 3.3).
    The exact bullet format is owned by ``tests/changelog/``; what this row owns is that the
    dependent gets a file at all, which is what a consumer reads to find out why the version moved.

    **The filter this row implies -- read it before writing one.** The line is emitted because the
    workspace marker makes this edge ``==1.0.0`` -- a constrained edge that caused the release --
    not because the pin text changed. The pin here is the bare ``pkg-a``, and
    ``tests/apply/test_apply.py::test_a_workspace_source_without_a_constraint_is_not_rewritten``
    says a workspace-sourced bare pin is never rewritten, so the changelog line is emitted **with
    no pin rewrite at all** (asserted below, so an implementer cannot read this row as "the pin
    moved"). Contrast
    ``tests/apply/test_apply.py::test_an_unconstrained_dependency_is_not_listed_in_the_changelog_either``,
    where the same bare string *without* the ``[tool.uv.sources]`` marker is genuinely
    unconstrained, never triggers a release, and gets no line: that module states the rule as "the
    pin was left untouched by design ... so claiming it was updated is simply false", which reads
    as a *text-diff* filter and would make this row red. The only filter satisfying both files is
    **"emit the line iff the dependency edge is version-constrained after workspace-source
    resolution"** (i.e. iff the edge could have caused the release), and neither file said so
    before this note. **Needs owner confirmation** -- see the F1 report.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", deps=["pkg-a"])
    mark_workspace_sources(tmp_project.root, "pkg-b", "pkg-a")
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-b")
    assert changelog is not None, "a dependency-only release still explains itself"
    assert "### Patch Changes" in changelog
    assert "pkg-a@1.1.0" in changelog
    assert "This is a summary" not in changelog, "pkg-a's summary belongs to pkg-a's changelog"
    assert deps(tmp_project.root, "pkg-b") == ["pkg-a"], (
        "the line is emitted even though the bare workspace-sourced pin is never rewritten "
        "(tests/apply/test_apply.py::test_a_workspace_source_without_a_constraint_is_not_rewritten)"
    )


def test_a_path_sourced_dependency_is_treated_as_a_workspace_edge(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """SC-4 (owner ruling, 2026-07-30). A ``{ path = ... }`` ``[tool.uv.sources]`` entry that
    resolves onto a workspace member is an internal edge exactly like ``{ workspace = true }`` --
    ``molt.ecosystem.uv`` already reads it that way (``_workspace_marker``); ``molt.apply.apply``
    now agrees. The only difference from the previous test
    (``test_a_dependent_changelog_records_the_dependency_update``) is how the source is spelled --
    everything else about the fixture and the assertions is identical, which is what makes this the
    first row proving the path-source reading end to end (previously nothing did: `SC-5`).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", deps=["pkg-a"])
    append_table(tmp_project.root, "pkg-b", "tool.uv.sources", ['pkg-a = { path = "../pkg-a" }'])
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-b")
    assert changelog is not None, "a dependency-only release still explains itself"
    assert "### Patch Changes" in changelog
    assert "pkg-a@1.1.0" in changelog
    assert deps(tmp_project.root, "pkg-b") == ["pkg-a"], (
        "the bare path-sourced pin is left untouched, same as a bare workspace = true pin"
    )


def test_a_dependency_named_in_several_sections_is_rewritten_in_all_of_them(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 30 (Adapt). ``version.test.ts:1495-1552``.

    Upstream's fixture puts pkg-b in ``peerDependencies`` **and** ``devDependencies``; peer
    dependencies do not exist in Python (research README section 4.4), so the surviving shape is
    runtime + extras + dev group. All three are rewritten, each keeping its own operator style, and
    pkg-a's own version does not move: a dev-group dependency yields ``none``, and the runtime and
    extras pins are still in range.
    """
    tmp_project.add_package(
        "pkg-a",
        "1.0.0",
        deps=["pkg-b>=1.0.0,<2.0.0"],
        dev_deps=["pkg-b==1.0.0"],
        optional_deps={"extra": ["pkg-b~=1.0.0"]},
    )
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root)["pkg-a"] == "1.0.0"
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.1,<2.0.0"]
    assert dev_deps(tmp_project.root, "pkg-a") == ["pkg-b==1.0.1"]
    assert optional_deps(tmp_project.root, "pkg-a") == {"extra": ["pkg-b~=1.0.1"]}


def test_update_internal_dependencies_gates_an_in_range_pin_rewrite(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new at this level; the gate itself is doc 04 section 2.5 (``utils.ts:20-81``).

    ``update_internal_dependencies = "minor"`` means "only bother rewriting an in-range pin when
    the dependency moved at least a minor". A patch therefore leaves pkg-a's pin exactly as written
    -- the point of the option is to keep release diffs small in a large workspace.

    Included here, thinly, because every other fixture in this file uses the default ``"patch"``,
    where the gate can never block anything: without this row the option is indistinguishable from
    "always rewrite" at the command level. ``tests/apply/test_apply.py`` owns the full ladder.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(update_internal_dependencies="minor")
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.0,<2.0.0"], (
        "a patch does not clear the `minor` threshold, and 1.0.1 is still in range"
    )


# ======================================================================================
# Section 7 -- snapshot releases (rows 31-40)
#
# PEP 440 has no `0.0.0-<tag>-<datetime>` form, so molt composes
# `0.0.0.dev<14-digit-datetime>[+<local>]`: `.devN` is the only numeric segment that sorts below
# every real release (which is the property upstream wanted from the `0.0.0` base), and the local
# segment is PEP 440's only free-form field. Legal because snapshots default to a non-PyPI index
# (research README section 4.3). Harness Session 2 decision 3; pinned at the engine level by
# `tests/engine/test_assemble.py`.
# ======================================================================================


def assert_is_normalized_pep440(version: str) -> None:
    """A snapshot version must be legal **and already normalized**.

    ``Version(...)`` alone is too weak -- ``packaging`` happily parses and then rewrites
    ``0.0.0-test`` (to ``0.0.0.post...``-adjacent forms) or ``+test-test`` (to ``+test.test``), so a
    parse-only check would bless a version string that PyPI stores under a different name than the
    one molt wrote into ``pyproject.toml``. Round-tripping is what catches that.

    Deliberately **no** ``.dev is not None`` assertion. ``website/docs/concepts/snapshots.md:53``
    renders a commit-hash snapshot as ``0.0.0+abcdefg`` -- no ``.devN`` at all -- and
    ``snapshots.md:55`` says "Exactly how tags survive alongside ``.devN`` is an area still being
    refined". Requiring the segment here would make a documented shape illegal *by construction*
    and would silently pre-empt research open decision #2. The composition each row expects is
    pinned by its own explicit literal in :data:`SNAPSHOT_TEMPLATE_CASES`; this helper only lints
    that the literal is a version PyPI would store under the same name.
    """
    assert str(Version(version)) == version, f"{version!r} is not a normalized PEP 440 version"


def test_a_snapshot_replaces_every_version_regardless_of_bump_type(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 31 (Adapt). ``version.test.ts:1555-1605``.

    A snapshot discards the computed bump entirely: a ``minor`` and a ``patch`` both land on the
    same throwaway version. ``0.0.0-experimental-<ts>`` is illegal in PEP 440, so the tag moves to
    the local segment.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git, snapshot="experimental")

    expected = f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+experimental"
    assert versions(tmp_project.root) == {"pkg-a": expected, "pkg-b": expected}


def test_a_snapshot_does_not_commit_even_when_commit_is_configured(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 32 (Port). ``version.test.ts:1607-1650``; ``version/index.ts:56``.

    ``commit: options.snapshot ? false : config.commit`` -- the flag overrides the config, not the
    other way round. Ports verbatim: a snapshot is meant to be cut on a throwaway checkout and
    discarded (``website/docs/concepts/snapshots.md``), so committing it would poison the branch it
    was cut from.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, snapshot="experimental")

    assert fake_git.commits == []
    assert fake_git.added == []


NONE_RELEASE_MODES: list[tuple[dict[str, Any], dict[str, Any], str]] = [
    ({}, {}, "row 33's sibling: a plain run never bumps a `none` release"),
    ({}, {"snapshot": True}, "row 33: `none` is frozen in snapshot mode too"),
    (
        {"snapshot": {"use_calculated_version": True}},
        {"snapshot": True},
        "row 39: and under useCalculatedVersion, where a base bump would otherwise be applied",
    ),
    ({}, {"pre": "rc"}, "net-new: `none` short-circuits before the prerelease suffix is attached"),
]


@pytest.mark.parametrize(("config", "options", "why"), NONE_RELEASE_MODES)
def test_an_explicit_none_release_is_never_bumped(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any],
    options: dict[str, Any],
    why: str,
) -> None:
    """Rows 33 + 39 (Port / Adapt). ``version.test.ts:1652-1685`` and ``:1966-2004``.

    ``none`` is the escape hatch for "this changeset documents something that is not a release", so
    it has to survive every mode. ``increment.ts:9-10`` short-circuits before any suffix logic
    runs, which is why the snapshot and prerelease paths cannot reach it either.
    ``website/docs/concepts/snapshots.md`` restates it: "A ``type: none`` release gets no suffix at
    all".
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    if config:
        tmp_project.set_config(**config)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "none"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, **options)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}, why


def test_a_snapshot_freezes_an_ignored_package_but_still_rewrites_its_pin(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 34 (Adapt). ``version.test.ts:1687-1739``.

    ``ignore`` beats snapshot mode, and the ignored package's pin is rewritten to the **bare
    snapshot version** rather than a range -- ``website/docs/concepts/snapshots.md`` is explicit
    that "there is no meaningful range around a ``0.0.0.dev...`` build". Without the exact pin, the
    ignored package would resolve pkg-b from the index instead of the snapshot.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(ignore=["pkg-a"])
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, snapshot=True)

    snapshot_version = f"0.0.0.dev{FROZEN_DATETIME_TOKEN}"
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": snapshot_version}
    assert deps(tmp_project.root, "pkg-a") == [f"pkg-b=={snapshot_version}"]


def test_a_tag_placeholder_without_a_named_snapshot_is_rejected(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 35 (Adapt). ``version.test.ts:1742-1785``.

    The template names a value the invocation never supplied, so composing it would silently
    produce ``0.0.0.dev<ts>+`` (or worse, the literal text ``{tag}``). Failing loudly is the only
    safe answer for a version string, because a bad one is permanent once uploaded.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(snapshot={"prerelease_template": "{tag}.{commit}"})
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, snapshot=True)

    assert excinfo.value.code == 1
    message = "\n".join(console.errors)
    assert "{tag}" in message and "placeholder" in message.lower(), message
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}, "nothing is written"


def test_a_named_snapshot_with_no_tag_placeholder_is_rejected(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 36 (Adapt). ``version.test.ts:1787-1825``.

    The inverse coherence check: the user named the snapshot but the template has nowhere to put
    the name, so every package would get an identical version and the name would be silently lost.
    The message quotes the value so the user can see which name was dropped.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(snapshot={"prerelease_template": "{commit}"})
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, snapshot="test")

    assert excinfo.value.code == 1
    message = "\n".join(console.errors)
    assert "{tag}" in message, message
    assert "test" in message, "the dropped value is named so the user can see what was lost"


#: Row 37 (Adapt), re-derived to PEP 440. ``version.test.ts:1832-1908`` -- an ``it.each`` with
#: **9** parameter sets (the group file says 8; recounted against the source).
#:
#: **STATUS: this composition is molt's proposal, pending owner sign-off on research open decision
#: #2.** Only one piece of it is actually pinned today -- harness Session 2 decision 3 fixed the
#: base shape ``0.0.0.dev<14-digit-datetime>[+<local>]`` for an untagged snapshot, which is what
#: closes the "``0.0.0-<tag>-<datetime>`` is illegal in PEP 440" problem. Everything below about
#: *multi-token* templates is this suite's proposal and has **no** documentation source:
#:
#: * the **numeric** tokens ``{timestamp}`` / ``{datetime}`` supply the ``.devN`` number; the first
#:   one in the template wins, and with none present the datetime is used (only this last clause --
#:   the untagged default -- comes from Session 2 decision 3);
#: * every other token contributes to the ``+local`` segment, in template order, joined by ``.``.
#:
#: ``website/docs/concepts/snapshots.md`` is deliberately **not** cited as the source of those two
#: rules. It states a weaker thing (":55" -- "``{timestamp}`` / ``{datetime}`` snapshots stay as
#: ``.devN``; a ``{tag}`` or ``{commit}`` decoration can only live in the local segment") and it
#: explicitly leaves the rest open: ":55" also says "Exactly how tags survive alongside ``.devN``
#: is an area still being refined", and ":53" renders a commit-hash snapshot as ``0.0.0+abcdefg``
#: -- i.e. with no ``.devN`` at all, which contradicts the "first numeric token wins" rule below.
#: The table takes the ``.devN``-always reading because a version that sorts *above* ``0.0.0`` is
#: the one property upstream's ``0.0.0`` base exists to provide, but that is a choice, not a
#: citation.
#:
#: Literal separators are not preserved because PEP 440 normalizes ``-`` and ``_`` to ``.`` inside
#: a local segment anyway, so ``{tag}-{tag}`` and ``{tag}.{tag}`` cannot be distinguished in the
#: final version.
#:
#: **If the owner rules differently, THIS TABLE is the single place to change** -- every row is an
#: explicit expected literal, the composition is not re-derived anywhere else in the suite, and
#: :func:`assert_is_normalized_pep440` deliberately asserts nothing about which segments are
#: present.
#:
#: One shell-level caveat the driver has to live with: the shell normalizes ``--snapshot=`` (an
#: *empty* optional value) to ``snapshot=True``, because ``""`` is the one falsy ``str`` and is
#: indistinguishable from "flag given with no value" once it reaches the command. So a row whose
#: ``name`` is ``None`` covers both "no name" spellings, and there is no row for ``name=""``.
SNAPSHOT_TEMPLATE_CASES: list[tuple[str, str | None, str, str]] = [
    (
        "{tag}",
        "test",
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+test",
        "upstream 0.0.0-test: a free-form tag is only legal in the local segment",
    ),
    (
        "{tag}-{tag}",
        "test",
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+test.test",
        "upstream 0.0.0-test-test: PEP 440 normalizes the `-` separator to `.`",
    ),
    (
        "{commit}",
        None,
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+{FROZEN_COMMIT}",
        "upstream 0.0.0-abcdef...z: a sha is hex, so it is local-segment only",
    ),
    (
        "{commit-short}",
        None,
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+{FROZEN_COMMIT_SHORT}",
        "upstream 0.0.0-abcdefg: the 7-character form",
    ),
    (
        "{timestamp}",
        None,
        f"0.0.0.dev{FROZEN_TIMESTAMP_TOKEN}",
        "upstream 0.0.0-1639354050879: epoch ms is numeric, so it IS the .devN",
    ),
    (
        "{datetime}",
        None,
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}",
        "upstream 0.0.0-20211213000730: the default composition, spelled explicitly",
    ),
    (
        "{tag}.{timestamp}.{commit}",
        "alpha",
        f"0.0.0.dev{FROZEN_TIMESTAMP_TOKEN}+alpha.{FROZEN_COMMIT}",
        "upstream 0.0.0-alpha.1639354050879.abcdef...z: numeric token wins the .devN, rest local",
    ),
    (
        "{tag}.{commit-short}",
        "alpha",
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+alpha.{FROZEN_COMMIT_SHORT}",
        "upstream 0.0.0-alpha.abcdefg: no numeric token, so the datetime default supplies .devN",
    ),
    (
        "{datetime}-{tag}",
        "alpha",
        f"0.0.0.dev{FROZEN_DATETIME_TOKEN}+alpha",
        "upstream 0.0.0-20211213000730-alpha: the datetime moves into .devN, not the local segment",
    ),
]


@pytest.mark.parametrize(("template", "name", "expected", "why"), SNAPSHOT_TEMPLATE_CASES)
def test_the_snapshot_template_matrix(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    template: str,
    name: str | None,
    expected: str,
    why: str,
) -> None:
    """Row 37 (Adapt). ``version.test.ts:1832-1908`` -- see :data:`SNAPSHOT_TEMPLATE_CASES`.

    Every package in the plan gets the **same** composed version regardless of its own bump type.

    Second half: ``website/docs/concepts/snapshots.md:55`` promises molt "warns loudly when the
    resulting version is not PyPI-uploadable", and a ``+local`` segment is exactly that case
    (``:52`` -- "the tag lives in the **local** segment, which PyPI will **not** accept";
    ``:53`` -- a commit hash is "**not uploadable**"). **Seven** of the nine rows compose one, so
    the warning is gated on the expected literal rather than asserted unconditionally: a run that
    warns on ``0.0.0.dev<datetime>`` would be crying wolf on the one shape the same line calls
    "uploadable everywhere", which is why the ``else`` arm is an assertion too.

    The legality of the table's own literals is a lint, not a behavior, and lives in
    :func:`test_the_snapshot_table_is_legal_pep440`.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(commit=False, snapshot={"prerelease_template": template})
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary too"
    )

    run_version(tmp_project.root, console, fake_git, snapshot=name if name is not None else True)

    assert versions(tmp_project.root) == {"pkg-a": expected, "pkg-b": expected}, why
    if "+" in expected:
        assert console.contains("PyPI", level="warn"), (
            f"{expected!r} carries a local segment, so it cannot be uploaded to PyPI; "
            f"snapshots.md:55 promises a loud warning. Warnings seen: {console.warnings}"
        )
    else:
        assert not console.contains("PyPI", level="warn"), (
            f"{expected!r} is uploadable everywhere (snapshots.md:55), so there is nothing to "
            f"warn about. Warnings seen: {console.warnings}"
        )


def test_the_snapshot_table_is_legal_pep440() -> None:
    """A lint on :data:`SNAPSHOT_TEMPLATE_CASES` itself -- no product code runs.

    Every expected literal in the table has to be a version ``packaging`` round-trips unchanged, or
    PyPI would store the package under a different name than the one molt wrote into
    ``pyproject.toml``. This is a *table* property, not an implementation property: the matrix
    above compares the manifest against the same constant, so asserting legality there would have
    asserted a constant while reading as though it guarded the implementation (which is what it
    used to do). Kept as its own test so a hand-edited row is caught even if the matrix is
    deselected.
    """
    for template, _name, expected, why in SNAPSHOT_TEMPLATE_CASES:
        assert_is_normalized_pep440(expected)
        assert expected.startswith("0.0.0"), f"{template!r} -> {expected!r}: {why}"


def test_use_calculated_version_snapshots_on_top_of_the_computed_bump(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 38 (Adapt). ``version.test.ts:1911-1964``.

    ``use_calculated_version`` keeps the real bump as the base, so the snapshot sorts above the
    last release instead of below everything -- the two packages get *different* versions, which is
    the whole observable difference from row 31. In PEP 440 the suffix becomes a ``.devN`` on the
    computed base: ``1.1.0.dev<datetime>`` sorts below ``1.1.0``, which is exactly what a
    pre-release-of-the-next-release should do.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(snapshot={"use_calculated_version": True})
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )

    run_version(tmp_project.root, console, fake_git, snapshot="experimental")

    assert versions(tmp_project.root) == {
        "pkg-a": f"1.1.0.dev{FROZEN_DATETIME_TOKEN}+experimental",
        "pkg-b": f"1.0.1.dev{FROZEN_DATETIME_TOKEN}+experimental",
    }


def test_use_calculated_version_freezes_an_ignored_package(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 40 (Adapt). ``version.test.ts:2006-2063``.

    Row 34 with ``use_calculated_version`` on: the ignored package is still frozen, and its pin now
    tracks the *calculated* snapshot (``2.0.0.dev...`` for a major) rather than ``0.0.0.dev...``.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(ignore=["pkg-a"], snapshot={"use_calculated_version": True})
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, snapshot=True)

    snapshot_version = f"2.0.0.dev{FROZEN_DATETIME_TOKEN}"
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": snapshot_version}
    assert deps(tmp_project.root, "pkg-a") == [f"pkg-b=={snapshot_version}"]


def test_a_snapshot_consumes_the_pending_changesets(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Net-new. ``website/docs/concepts/snapshots.md``, "What a snapshot run does".

    The documented contrast with ``--pre``: a snapshot "consumes the pending changesets (it does
    not preserve them the way prerelease mode does)". The two throwaway-version paths differ here
    and nowhere else that is visible on disk, so the pair
    (:func:`test_a_pre_run_leaves_the_changesets_on_disk`) is the only thing keeping them apart.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, snapshot=True)

    assert changeset_ids(tmp_project.root) == []


# ======================================================================================
# Section 8 -- update_internal_dependents = "always" (rows 41-44) and unresolvable pins (row 12)
# ======================================================================================


def test_the_always_policy_patch_bumps_an_in_range_dependent(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 41 (Port; range spelling Adapt). ``version.test.ts:2066-2138``;
    ``determine-dependents.ts:93-98``.

    ``always`` bypasses the ``semverSatisfies`` gate, so an in-range dependent is released anyway.
    Teams turn this on when they want every consumer republished in lockstep; the cost is a release
    for every package in the graph, which is why it is not the default.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "This is not a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1", "pkg-b": "1.0.1"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.0.1,<2.0.0"]
    pkg_a_changelog = read_changelog(tmp_project.root, "pkg-a")
    assert pkg_a_changelog is not None and "pkg-b@1.0.1" in pkg_a_changelog


def test_a_dev_dependent_is_pin_rewritten_but_never_bumped_and_propagation_stops(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 42 (Port). ``version.test.ts:2140-2222``; ``determine-dependents.ts:108-117``.

    **Core, and easy to get wrong.** pkg-b depends on pkg-a only in its dev group, so it takes
    ``none``: its pin is rewritten (a stale dev pin still breaks ``uv sync``) but its version does
    not move and it gets no changelog. Because pkg-b was not released, pkg-c -- which depends on
    pkg-b -- is not visited at all. That "propagation stops" clause is what keeps a monorepo from
    republishing itself end to end every time a test-only dependency moves.

    Run with ``always`` precisely because that is the configuration most likely to break the rule:
    ``always`` bypasses the range gate but not the ``devDependencies`` switch case.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0", dev_deps=["pkg-a==1.0.0"])
    tmp_project.add_package("pkg-c", "1.0.0", deps=["pkg-b==1.0.0"])
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0", "pkg-b": "1.0.0", "pkg-c": "1.0.0"}
    assert dev_deps(tmp_project.root, "pkg-b") == ["pkg-a==1.1.0"], "the stale dev pin still moves"
    assert deps(tmp_project.root, "pkg-c") == ["pkg-b==1.0.0"], "propagation stopped at pkg-b"
    assert read_changelog(tmp_project.root, "pkg-a") is not None
    assert read_changelog(tmp_project.root, "pkg-b") is None
    assert read_changelog(tmp_project.root, "pkg-c") is None


def test_a_direct_reference_dependency_never_makes_its_holder_a_dependent(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Rows 12 + 43 (Adapt). ``version.test.ts:549-596`` and ``:2225-2291``.

    Upstream's fixtures pin an npm **dist-tag** (``"latest"``, ``"bulbasaur"``) as the range; PyPI
    has no dist-tags (research README section 4.4), so the molt reframing is "a specifier molt
    cannot resolve to a local package". A PEP 508 **direct reference** is that: ``pkg-b @
    git+https://...`` names a location, not a version, so the workspace member is not what will be
    installed and bumping its holder would be meaningless.

    Run under ``update_internal_dependents = "always"`` **deliberately**: that is what separates
    this rule from the unconstrained rule next door. ``always`` releases every dependent that
    resolves to a local package, so an unconstrained pin *does* bump its holder
    (:func:`test_an_unconstrained_dependency_bumps_its_holder_under_the_always_policy`) while a
    direct reference must not. Without ``always``, both fixtures would pass for either rule --
    ``Requirement("pkg-b @ git+...").specifier`` is an **empty** SpecifierSet, not ``None``, so the
    unconstrained rule alone would mask this one (the mask P4's reviewer found; the range-rewrite
    half is pinned in ``tests/apply/test_apply.py``).
    """
    reference = "pkg-b @ git+https://example.invalid/pkg-b.git"
    tmp_project.add_package("pkg-a", "1.0.0", deps=[reference])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}
    assert deps(tmp_project.root, "pkg-a") == [reference], "a location has no specifier to rewrite"
    assert read_changelog(tmp_project.root, "pkg-a") is None


def test_an_unconstrained_dependency_bumps_its_holder_under_the_always_policy(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """The discriminator for the row above. ``determine-dependents.ts:90-99``;
    research README section 3.3.

    A bare ``pkg-b`` requirement *does* resolve to the local package -- it simply accepts every
    version of it -- so under ``always`` its holder is released like any other dependent, and only
    the pin stays put (there is no specifier region to splice). An implementation that skipped
    every empty specifier would leave pkg-a at ``1.0.0`` and fail here, which is what stops the
    direct-reference row from passing for the wrong reason.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1", "pkg-b": "2.0.0"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b"]


def test_an_unconstrained_dependency_never_triggers_an_out_of_range_bump(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Research README section 3.3 ("Plain ``*`` never triggers a bump"); doc 04 section 2.4.

    Under the default ``out-of-range`` policy an unconstrained pin can never go out of range, so
    its holder is not released -- which is exactly the opposite of a *workspace-sourced* bare
    requirement (see
    :func:`test_a_bare_workspace_source_patch_bumps_its_dependent_without_changing_the_pin`),
    where the same text means "the current exact version". The two fixtures differ only by the
    ``[tool.uv.sources]`` marker, so this pair pins that the marker is actually read.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b"]


def test_a_holder_of_an_unresolvable_pin_is_still_released_by_its_own_changeset(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 44 (Adapt). ``version.test.ts:2293-2371``.

    The complement of row 43: the unresolvable pin only suppresses *propagation*. When pkg-a has a
    changeset of its own it is released normally, from its own summary, and the direct reference is
    still left byte-identical. Without this row, "leave it alone" could be implemented as "never
    touch this package", which would silently swallow real releases.
    """
    reference = "pkg-b @ git+https://example.invalid/pkg-b.git"
    tmp_project.add_package("pkg-a", "1.0.0", deps=[reference])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(update_internal_dependents="always")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "pkg-b's own summary")
    tmp_project.write_changeset("some-id-1", {"pkg-a": "patch"}, "pkg-a's own summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.1", "pkg-b": "1.1.0"}
    assert deps(tmp_project.root, "pkg-a") == [reference]
    pkg_a_changelog = read_changelog(tmp_project.root, "pkg-a")
    assert pkg_a_changelog is not None and "pkg-a's own summary" in pkg_a_changelog


# ======================================================================================
# Section 9 -- private packages (rows 59, 64)
# ======================================================================================


def test_a_private_package_without_a_version_field_is_skipped_cleanly(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 59 (re-ported outside pre mode). ``version.test.ts:3364-3403``.

    Upstream runs it inside pre mode, but nothing in it is prerelease-specific: a private package
    with no ``version`` at all -- an app, a docs site -- must not crash the run and must come back
    untouched. It is also the fixture behind the pre-exit backfill bug molt does **not** port
    (research README section 3.4: ``preVersions.get(name) !== 0`` gives a skipped package a patch
    release with ``newVersion: null``), so a clean skip here is the corrected behavior.
    """
    tmp_project.add_package("pkg-a", "1.0.0", private=True)
    tmp_project.add_package("pkg-b", "1.0.0")
    manifest = manifest_path(tmp_project.root, "pkg-a")
    text = manifest.read_bytes().decode("utf-8")
    manifest.write_bytes(
        re.sub(r'(?m)^version = "1\.0\.0"\r?\n', "", text, count=1).encode("utf-8")
    )
    before = manifest.read_bytes()
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": None, "pkg-b": "1.0.1"}
    assert manifest.read_bytes() == before, "a version-less package is left byte-identical"


def test_a_version_less_package_is_skipped_but_its_pin_still_moves(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new; row 59's rule made reachable. ``version.test.ts:3364-3403``; research README
    section 3.4 (the pre-exit backfill bug).

    Row 59's own fixture has no dependencies, so the version-less package never enters the release
    plan and any "is it skipped?" logic is unexercised. Here it *is* a dependent, with a pin that
    the release escapes -- which is exactly the shape of the upstream bug molt does not port
    (``preVersions.get(name) !== 0`` gives a skipped package a ``patch`` release with
    ``newVersion: null``). Correct behavior: no version key appears where there was none, and the
    pin is still rewritten so the workspace stays installable.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b==1.0.0"], private=True)
    tmp_project.add_package("pkg-b", "1.0.0")
    manifest = manifest_path(tmp_project.root, "pkg-a")
    text = manifest.read_bytes().decode("utf-8")
    manifest.write_bytes(
        re.sub(r'(?m)^version = "1\.0\.0"\r?\n', "", text, count=1).encode("utf-8")
    )
    tmp_project.write_changeset("some-id-0", {"pkg-b": "patch"}, "a very useful summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": None, "pkg-b": "1.0.1"}
    assert "version = " not in manifest.read_bytes().decode("utf-8"), (
        "molt must not invent a version field for a package that deliberately has none"
    )
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b==1.0.1"]
    assert read_changelog(tmp_project.root, "pkg-a") is None


def test_unversioned_private_packages_are_frozen_but_their_pins_still_move(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Row 64 (Port; range spelling Adapt). ``version.test.ts:3687-3739``.

    ``private_packages = false`` (which normalizes to ``{version = false}`` --
    ``website/docs/config/options.md``) takes private packages out of versioning entirely, but
    their pins still track, for the same reason as row 7: the workspace has to stay installable.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"], private=True)
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(private_packages={"version": False})
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0", "pkg-b": "1.1.0"}
    assert deps(tmp_project.root, "pkg-a") == ["pkg-b>=1.1.0,<2.0.0"]


# ======================================================================================
# Section 9b -- the seams `version` is the first command to reach end to end
#
# Every row below pins an owner ruling of 2026-07-30 that had no covering test when this command
# was written; the gap it closes is named in each docstring. They are net-new to molt, so none of
# them has an upstream row: changesets has no entry template, no forge seam and no commit-provider
# plugin. See `openspec/GAPS.md`.
# ======================================================================================


#: A changelog-entry template that is unmistakably *not* the default layout, so a run that quietly
#: ignored the configured template renders something this row can tell apart. Written with the
#: documented context keys only (``website/docs/guides/changelog-templates.md``, "Customizing the
#: template"), because those keys are the public contract a user's template is written against.
CUSTOM_TEMPLATE = """## {{ release.name }} {{ release.new_version }}\
{% if config.dates %} ({{ release.date.strftime("%Y-%m-%d") }}){% endif %}

{% for line in release.minor %}{{ line }}
{% endfor %}"""


def test_a_configured_changelog_template_shapes_the_entry(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Closes ``CT-1`` / ``CT-2`` / ``CT-3``: templating is reachable from a real run at last.

    Three separate holes, one row. ``CT-1``: ``changelog = { template = ... }`` is a config
    setting, so a project can turn entry templating on. ``CT-2``: ``molt.apply`` calls
    :func:`molt.changelog.render_changelog` when it is set, where before *nothing* did and the
    templated path was reachable only from a library call. ``CT-3``: the value is a **filename**
    and the command owns the read -- the renderer still takes Jinja2 source text and never touches
    the filesystem.

    The template lives in a subdirectory and the command runs from a **nested** package directory,
    which is what discriminates the ruling's resolution rule: relative to the *workspace root*, not
    to the current directory. Resolving against the cwd would fail to find the file at all.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog={"template": "templates/entry.md.jinja"})
    write_file(tmp_project.root, "templates/entry.md.jinja", CUSTOM_TEMPLATE)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run(cwd=tmp_project.root / "packages" / "pkg-a", console=console, git=fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert "## pkg-a 1.1.0" in changelog, "the configured template shaped the heading"
    assert "This is a summary" in changelog
    assert "### Minor Changes" not in changelog, (
        "the default layout must be gone entirely, not merely decorated"
    )


def test_changelog_dates_reach_the_template(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    frozen_clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second half of ``CT-1``, and the reason ``CT-5``'s hint exists.

    The written key and the template key are the same word since the ``VC-4`` ruling: a project
    writes ``changelog = { dates = true }`` and a template reads ``config.dates``. The date itself
    is one timestamp for the whole run, taken from the ``molt.clock`` seam --
    two packages released together must not be stamped with the duration of the run. With the key
    off, ``release.date`` is a ``StrictUndefined`` carrying the hint, so a dated template reports a
    missing date instead of raising ``AttributeError`` on ``None``.
    """
    frozen_clock.freeze(monkeypatch)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog={"template": "entry.md.jinja", "dates": True})
    write_file(tmp_project.root, "entry.md.jinja", CUSTOM_TEMPLATE)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert "## pkg-a 1.1.0 (2021-12-13)" in changelog


def test_a_missing_changelog_template_is_reported_by_name(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """``CT-3``'s failure half: the filename is quoted back, and nothing is written.

    A typo in a template filename is a configuration mistake, so it has to name the file and the
    place molt looked -- and it must not leave a half-released tree behind, which is what the
    version assertion checks.
    """
    from molt.errors import MoltError  # pyrefly: ignore[missing-import]

    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog={"template": "does-not-exist.md.jinja"})
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(MoltError) as excinfo:
        run_version(tmp_project.root, console, fake_git)

    assert "does-not-exist.md.jinja" in str(excinfo.value)
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}


def test_a_changeset_naming_a_version_less_package_is_refused(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Keeps ``RPE-4``'s loud refusal reachable from the command, and pins ``VC-1``'s boundary.

    A package with no ``[project].version`` is *skipped* by this command, which is what makes a
    repository containing an app releasable at all (rows 59, 64 and the version-less pin row all
    require it). That skip must not quietly extend to a package somebody **asked** to release: by
    the time the engine sees it, a versionless package is indistinguishable from an ignored one, so
    its changeset would be neither applied nor consumed and nothing would say why -- the exact
    "silently drop half a repo" failure ``RPE-4`` exists to prevent.

    The contrast with :func:`test_a_private_package_without_a_version_field_is_skipped_cleanly` is
    the whole point: same manifest, and the only difference is whether a changeset names it.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    manifest = manifest_path(tmp_project.root, "pkg-b")
    text = manifest.read_bytes().decode("utf-8")
    manifest.write_bytes(
        re.sub(r'(?m)^version = "1\.0\.0"\r?\n', "", text, count=1).encode("utf-8")
    )
    tmp_project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git)

    assert excinfo.value.code == 1
    message = "\n".join(console.errors)
    assert "pkg-b" in message and "some-id-0" in message, message
    assert versions(tmp_project.root)["pkg-a"] == "1.0.0", "nothing is written"
    assert changeset_ids(tmp_project.root) == ["some-id-0"], "and the changeset survives"


def test_a_broken_changelog_template_is_reported_without_a_traceback(
    tmp_project: ProjectBuilder,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Closes ``CT-4``: ``MoltTemplateError`` takes the funnel's **friendly** branch.

    Driven through ``molt.cli.main`` rather than through ``run`` because that is where the claim
    lives: the funnel catches ``MoltError`` and prints one sentence (``cli.py``'s ``except
    MoltError``), while anything else falls through to ``except Exception``, which prints a
    traceback and an issue-report URL. ``MoltTemplateError`` derives from **both** ``MoltError`` and
    ``ValueError``; only the two conformance suites' ``ValueError`` half was pinned before this row,
    so dropping the ``MoltError`` base would have sent a user's template typo down the traceback
    branch with every suite still green. ``CliRunner`` cannot see this -- it catches the exception
    itself, before ``main`` does -- so the entry point is called directly and ``capfd`` reads the
    console at the file-descriptor level.

    The template asks for a name the context does not carry, which ``StrictUndefined`` turns into a
    hard error at render time -- the same class of mistake as a misspelled ``release.new_verison``.
    """
    pytest.importorskip("typer", reason="the CLI shell lands with adopt-typer-cli-shell")
    import sys

    from tests.cli.fake_cli import strip_ansi

    from molt import cli as molt_cli  # pyrefly: ignore[missing-import]

    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog={"template": "entry.md.jinja"})
    write_file(tmp_project.root, "entry.md.jinja", "## {{ release.no_such_field }}\n")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    monkeypatch.setattr(sys, "argv", ["molt", "version", "--cwd", str(tmp_project.root)])
    try:
        code = molt_cli.main()
    except SystemExit as exit_request:  # a funnel that exits rather than returning
        code = 1 if not isinstance(exit_request.code, int) else exit_request.code
    captured = capfd.readouterr()
    output = strip_ansi(captured.out + captured.err)

    assert code == 1
    assert "Traceback" not in output, output
    assert "no_such_field" in output, output
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}, "nothing is written"


def test_the_github_changelog_generator_is_given_a_forge(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Closes ``CG-3``: ``molt version`` wires a forge through to the changelog layer.

    The ``github`` generator refuses ``forge=None`` with ``MoltForgeError`` before it does anything
    else (ratified 2026-07-30 -- a linkless changelog under the ``github`` name is worse than a
    loud failure). ``molt.apply`` passed no forge at all until this change, so a project configured
    with ``changelog = "github"`` could not run at all. **This row passes only if a real forge
    instance reached the generator**, which is what makes it the discriminator: the run below makes
    no network request, because a changeset with no commit and no ``pr:``/``commit:`` directive has
    nothing to look up.

    The repository comes from the generator's own ``repo`` option so the row does not depend on
    ``GITHUB_REPOSITORY`` being set or unset in the environment running the suite.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog=["github", {"repo": "acme/acme"}])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    changelog = read_changelog(tmp_project.root, "pkg-a")
    assert changelog is not None
    assert "This is a summary" in changelog
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}


def test_a_github_changelog_with_no_repository_fails_loudly(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of ``CG-3``: no repository is an error, not a linkless changelog.

    ``GITHUB_REPOSITORY`` is cleared explicitly rather than assumed absent -- the suite may well be
    running inside an Action, where it is set, and a row whose outcome depends on the environment
    it runs in is not a row.
    """
    from molt.errors import MoltForgeError  # pyrefly: ignore[missing-import]

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog="github")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(MoltForgeError):
        run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}, "nothing is written"


SKIP_CI_SETTINGS = [
    (True, "commit = true normalizes to {skip_ci = 'version'} (config.ts:177-179)"),
    (
        ["molt.commit.default", {"skip_ci": True}],
        "a bare `true` is the command's own target: `version` here, never `add`",
    ),
    (
        ["molt.commit.default", {"skip_ci": "version"}],
        "the literal spelling, which is the only form the 8-row commit gate ever exercised",
    ),
]


@pytest.mark.parametrize(("setting", "why"), SKIP_CI_SETTINGS)
def test_the_version_commit_carries_the_skip_ci_marker(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    setting: Any,
    why: str,
) -> None:
    """Closes the version half of ``CM-1`` and pins ``CM-2``'s narrowed ``skip_ci``.

    ``CM-1``: nothing read ``config.commit`` for ``molt version`` and nothing called
    ``get_version_message`` -- the provider is now loaded through the ``molt.commit`` entry-point
    group, exactly as ``molt add`` loads it, so a project can substitute its own convention for
    both commit points at once.

    ``CM-2``: ``skip_ci`` used to accept ``bool | str`` with an untested ``is True`` branch in both
    message functions. It is now ``Literal["add", "version"] | False``, and the *resolution layer*
    turns a written ``true`` into the concrete target. All three written spellings must therefore
    produce the same commit message, which is what the parameter table asserts.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(commit=setting)
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert len(fake_git.commits) == 1, why
    assert fake_git.commits[0].endswith("[skip ci]\n"), why


def test_a_skip_ci_target_of_add_leaves_the_version_commit_unmarked(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """``CM-2``'s discriminator: the *other* command's target is a deliberate no-op.

    One ``skip_ci`` value gates one commit point (``commit/index.ts:6-19``), which is the whole
    reason the option takes a command name rather than a boolean. Without this row, resolving every
    written value to "this command" would pass the table above.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(commit=["molt.commit.default", {"skip_ci": "add"}])
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert len(fake_git.commits) == 1
    assert "[skip ci]" not in fake_git.commits[0]


# ======================================================================================
# Section 10 -- molt-native surface: --dry-run, --pre, the lockfile, exit codes
# ======================================================================================


def test_dry_run_writes_nothing_at_all(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new (``website/docs/cli/version.md``; ``guides/dry-run-and-plans.md``).

    changesets has no ``--dry-run`` on ``version`` at all, so this is molt surface with no upstream
    oracle. The assertion is deliberately total -- **every** file under the workspace root, byte
    for byte -- rather than "the versions did not change", because the failure mode worth catching
    is a partial write: the changelog rendered, or the changeset deleted, while the manifests were
    correctly left alone.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.set_config(commit=True)
    tmp_project.write_changeset(
        "some-id-0", {"pkg-a": "minor", "pkg-b": "patch"}, "This is a summary"
    )
    before = tree_bytes(tmp_project.root)

    run_version(tmp_project.root, console, fake_git, dry_run=True)

    assert tree_bytes(tmp_project.root) == before
    assert fake_git.calls == [], "a dry run does not stage, commit or tag either"


def test_dry_run_prints_every_version_transition(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new (``website/docs/guides/dry-run-and-plans.md``: "a faithful preview").

    Writing nothing is only half the contract -- a dry run that printed nothing would pass the
    test above. Each planned release must be legible, including the dependent that is only in the
    plan because pkg-b moved out of its range.
    """
    tmp_project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-b": "major"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, dry_run=True)

    printed = console.text()
    assert "pkg-b" in printed and "2.0.0" in printed
    assert "pkg-a" in printed and "1.0.1" in printed, "the cascaded dependent is part of the plan"


PRE_PHASES = [
    ("a", "1.1.0a0", "alpha -- website/docs/cli/pre.md, 'The --pre values'"),
    ("b", "1.1.0b0", "beta"),
    ("rc", "1.1.0rc0", "release candidate"),
    ("dev", "1.1.0.dev0", "developmental release: `.devN` is dot-prefixed, unlike aN/bN/rcN"),
]


@pytest.mark.parametrize(("phase", "expected", "why"), PRE_PHASES)
def test_pre_attaches_a_pep440_prerelease_identifier(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    phase: str,
    expected: str,
    why: str,
) -> None:
    """Net-new; the replacement for the dropped ``pre enter`` lifecycle
    (``website/docs/cli/pre.md``; research README section 4.2).

    The first ``--pre`` run computes the target stable version from the pending changesets and
    appends the identifier at counter ``0``. The arithmetic is owned by
    ``tests/versioning/test_pre.py``; this row owns that the flag reaches it.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, pre=phase)

    assert versions(tmp_project.root) == {"pkg-a": expected}, why


def test_a_pre_run_leaves_the_changesets_on_disk(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new. ``website/docs/concepts/prerelease.md:51``: "Your changesets stay in place while
    you iterate"; upstream's equivalent is ``apply-release-plan/src/index.ts:195-228``, which
    deletes only when not in pre mode.

    This is what makes the counter work at all: each prerelease is a preview of the *same* stable
    release, so the changesets that describe it have to survive until that release is cut.

    **Docs conflict, resolved against the CLI reference page.** ``website/docs/cli/pre.md:45`` says
    the opposite -- "**First ``--pre`` run** consumes the pending changesets, computes the target
    bump, and appends the prerelease identifier at counter ``0``". The module docstring's usual
    precedence rule ("the CLI reference page is the more specific oracle") would hand that page the
    win, and it must not here: ``cli/pre.md:45`` is a **docs bug**, reported to the owner. Three
    independent reasons:

    1. It is mechanically impossible. ``cli/pre.md:46`` in the very next bullet says each
       subsequent ``--pre`` run increments the counter and "The highest bump type across the
       accumulated changesets is retained" -- there is nothing to accumulate or recompute from if
       run 1 deleted the buffer. :func:`test_the_pre_counter_advances_and_a_plain_run_finalizes`
       runs ``--pre rc`` twice; under ``cli/pre.md:45`` the second run would hit the
       empty-buffer warn-and-exit-1 path instead of writing ``1.1.0rc1``.
    2. Two other pages agree with ``prerelease.md``: ``prerelease.md:58`` marks the *plain* run as
       the one that "consumes the changesets", and ``concepts/snapshots.md:61`` describes a
       snapshot as consuming them "(it does not preserve them the way prerelease mode does)" --
       a contrast that has no meaning if prerelease mode consumes them too.
    3. Upstream behaves the way ``prerelease.md`` describes
       (``apply-release-plan/src/index.ts:195``: delete only when not in pre mode), and the
       ``pre.json`` removal was never supposed to change *that* half.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, pre="rc")

    assert changeset_ids(tmp_project.root) == ["some-id-0"]


def test_the_pre_counter_advances_and_a_plain_run_finalizes(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new. ``website/docs/concepts/prerelease.md:41-61``; ``website/docs/cli/pre.md``.

    The whole ``pre.json`` state machine, replaced by three invocations. The state lives in the
    version string on disk, so a second ``--pre rc`` reads ``1.1.0rc0`` and writes ``1.1.0rc1``,
    and a plain run strips the identifier rather than bumping again -- ``patch``/``minor`` applied
    to a version that is already a prerelease of the target is a no-op on the release tuple
    (``tests/versioning/test_bump.py``). Cutting ``1.1.0`` here rather than ``1.2.0`` is the
    property that makes "exit pre mode" unnecessary.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git, pre="rc")
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0rc0"}

    run_version(tmp_project.root, console, fake_git, pre="rc")
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0rc1"}

    run_version(tmp_project.root, console, fake_git)
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}
    assert changeset_ids(tmp_project.root) == [], "the finalizing run consumes them"


FREE_FORM_PRE_TAGS = [
    ("next", "changesets' own default tag; `1.0.1-next.0` is not a PEP 440 version"),
    ("alpha", "the spelled-out word is not the PEP 440 identifier -- `a` is"),
    ("beta", "likewise: `b`"),
    ("canary", "an arbitrary channel name has no PEP 440 rendering at all"),
]


@pytest.mark.parametrize(("tag", "why"), FREE_FORM_PRE_TAGS)
def test_a_free_form_pre_tag_is_rejected(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    tag: str,
    why: str,
) -> None:
    """Net-new. ``website/docs/concepts/prerelease.md:35``; ``website/docs/cli/pre.md``.

    "There is no ``--pre next``; a 'next' channel has no PEP 440 rendering, and PyPI would reject
    or renormalize it." Rejecting at the command level and not only in Typer's enum matters because
    ``run`` is a public seam, and because a silently renormalized version is unrecoverable once
    uploaded (research README section 4.3).
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, pre=tag)

    assert excinfo.value.code == 1, why
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}


def test_pre_and_snapshot_cannot_be_combined(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new, but the direct descendant of upstream's "Snapshot release is not allowed in pre
    mode" guard (``version/index.ts:72-80``; doc 03 section 9.2).

    Upstream's version is a *state* check ("you are in pre mode"); molt's is an *argument* check,
    which is the whole point of killing ``pre.json``. The two flags want incompatible version
    shapes -- ``1.1.0rc0`` versus ``0.0.0.dev<ts>`` -- so there is nothing sensible to compose.
    ``website/docs/cli/version.md`` lists the combination in its exit-code table.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, pre="rc", snapshot=True)

    assert excinfo.value.code == 1
    assert console.errors, "the conflict is explained, not just exited on"
    assert versions(tmp_project.root) == {"pkg-a": "1.0.0"}


def test_no_pre_state_file_is_ever_created(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new. ``website/docs/cli/pre.md``: "There is no ``pre.json``, no mode to enter, no mode to
    exit, and no leftover state on the branch."

    The negative half of the ``--pre`` design, asserted after both a prerelease and a finalizing
    run because ``pre.json`` is written on enter *and* rewritten on exit upstream
    (``apply-release-plan/src/index.ts:113-126``). Also asserted: a stale ``pre.json`` left behind
    by a previous tool is neither read nor deleted -- the docs say "delete it; molt does not read
    it", so molt must not act on it in either direction.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")
    stale = write_file(tmp_project.root, ".changeset/pre.json", '{"mode": "pre", "tag": "next"}\n')

    run_version(tmp_project.root, console, fake_git, pre="rc")
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0rc0"}, "a stale pre.json is not read"

    run_version(tmp_project.root, console, fake_git)

    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}
    assert stale.read_bytes() == b'{"mode": "pre", "tag": "next"}\n', "and not rewritten"


def test_version_never_prompts(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    prompts: ScriptedPrompts,
) -> None:
    """Net-new (doc 03 section 9.3: the non-TTY hazard table).

    ``version`` is the command CI runs unattended, and it is deliberately absent from the "can
    block forever without a TTY" list. The empty :class:`ScriptedPrompts` turns any prompt into a
    ``PromptExhausted`` failure, so this asserts the absence rather than merely not observing one.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run(cwd=tmp_project.root, console=console, git=fake_git, prompts=prompts)

    assert prompts.calls == []
    assert versions(tmp_project.root) == {"pkg-a": "1.1.0"}


@pytest.mark.integration
@pytest.mark.slow
def test_the_lockfile_is_refreshed(
    tmp_project: ProjectBuilder, console: RecordingConsole, fake_git: FakeGit
) -> None:
    """Net-new (research README section 4.5: "``uv.lock`` must be updated ... load-bearing here").

    changesets performs no lockfile step anywhere in v3 (doc 03 section 3.2, the boxed note), which
    is merely cosmetic in npm and wrong in Python: an unlocked workspace resolves the *old*
    versions on the next ``uv sync``. Marked ``integration`` because it spawns the real ``uv``;
    the fixture is a single dependency-free member so the resolve is offline.

    The sentinel content is what makes this non-vacuous -- asserting only that ``uv.lock`` exists
    would pass against an implementation that never touched a pre-existing one.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    lockfile = write_file(tmp_project.root, "uv.lock", "# stale sentinel\n")
    tmp_project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")

    run_version(tmp_project.root, console, fake_git)

    assert lockfile.read_bytes() != b"# stale sentinel\n"
    assert b"pkg-a" in lockfile.read_bytes()


# ======================================================================================
# The exit-code contract (cli-shell spec, "Exit-code contract"; doc 03 section 11.6)
#
# `version` owns four rows of that table. They are asserted individually above; this matrix is the
# completeness check -- it is what fails when a new failure mode is added without deciding its
# code. Ctrl-C -> 0 is not reachable here (`version` never prompts, asserted above) and belongs to
# `tests/cli/test_cli.py`, which owns the exit funnel.
# ======================================================================================


def scenario_success(project: ProjectBuilder) -> None:
    project.add_package("pkg-a", "1.0.0")
    project.write_changeset("some-id-0", {"pkg-a": "minor"}, "This is a summary")


def scenario_no_changesets(project: ProjectBuilder) -> None:
    project.add_package("pkg-a", "1.0.0")


def scenario_only_parked_changesets(project: ProjectBuilder) -> None:
    project.add_package("pkg-a", "1.0.0")
    project.write_changeset(".parked", {"pkg-a": "minor"}, "Not yet")


def scenario_unknown_ignore(project: ProjectBuilder) -> None:
    scenario_success(project)


def scenario_ignore_conflict(project: ProjectBuilder) -> None:
    scenario_success(project)
    project.add_package("pkg-b", "1.0.0")
    project.set_config(ignore=["pkg-b"])


def scenario_skipped_dependent(project: ProjectBuilder) -> None:
    project.add_package("pkg-a", "1.0.0", deps=["pkg-b>=1.0.0,<2.0.0"])
    project.add_package("pkg-b", "1.0.0")
    project.write_changeset("some-id-0", {"pkg-b": "minor"}, "This is a summary")


EXIT_CODE_CASES: list[tuple[Any, dict[str, Any], int, str]] = [
    (scenario_success, {}, 0, "versions applied -> 0 (cli/version.md exit-code table)"),
    (scenario_success, {"dry_run": True}, 0, "a printed dry run is a success -> 0"),
    (
        scenario_no_changesets,
        {},
        1,
        "no unreleased changesets -> 1 (v3 semantics; index.ts:92-98). THE row.",
    ),
    (
        scenario_only_parked_changesets,
        {},
        1,
        "a dot-prefixed file is not a changeset, so the buffer is still empty -> 1",
    ),
    (
        scenario_no_changesets,
        {"dry_run": True},
        1,
        "validation precedes the plan, so --dry-run does not soften the empty-buffer exit",
    ),
    (
        scenario_unknown_ignore,
        {"ignore": ["pkg-c"]},
        1,
        "unknown --ignore name -> 1 (index.ts:176)",
    ),
    (
        scenario_ignore_conflict,
        {"ignore": ["pkg-a"]},
        1,
        "--ignore together with a config ignore list -> 1 (index.ts:37-45)",
    ),
    (
        scenario_skipped_dependent,
        {"ignore": ["pkg-b"]},
        1,
        "an unskipped dependent of a skipped package -> 1 (index.ts:229-232)",
    ),
    (
        scenario_success,
        {"pre": "rc", "snapshot": True},
        1,
        "--pre with --snapshot -> 1 (cli/version.md, 'The --snapshot optional value')",
    ),
    (scenario_success, {"pre": "next"}, 1, "a free-form --pre tag -> 1 (cli/pre.md)"),
]


@pytest.mark.parametrize(("scenario", "options", "code", "why"), EXIT_CODE_CASES)
def test_the_version_exit_code_contract(
    tmp_project: ProjectBuilder,
    console: RecordingConsole,
    fake_git: FakeGit,
    scenario: Any,
    options: dict[str, Any],
    code: int,
    why: str,
) -> None:
    """The ``version`` rows of the exit-code contract, in one place.

    ``cli-shell`` spec ("Exit-code contract"), doc 03 section 11.6 and
    ``website/docs/cli/version.md`` all carry the same table; success is modelled as "``run``
    returns without raising" because the command signals failure with ``ExitError``, not a return
    value (harness ruling: command-logic tests assert the exception, CLI-level tests assert
    ``result.exit_code``).
    """
    scenario(tmp_project)

    if code == 0:
        run_version(tmp_project.root, console, fake_git, **options)
        return

    with pytest.raises(ExitError) as excinfo:
        run_version(tmp_project.root, console, fake_git, **options)
    assert excinfo.value.code == code, why
