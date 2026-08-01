"""Conformance tests for ``molt status``.

Ports the 12 rows of ``packages/cli/src/commands/status/__tests__/status.test.ts`` catalogued in
``roadmap/research/test-suite/06-cli-commands.md`` (status section), against the behaviour spec in
``roadmap/research/changesets-03-cli-and-ux.md`` section 5 -- 5.1 (flow), 5.2 (the CI exit-code
contract), 5.3 (the exact ``--output`` JSON schema), 5.4 (human output) -- plus gotcha 10.11.
Website docs: ``website/docs/cli/status.md`` and ``website/docs/guides/status.md``.

Deviations from the group file, all deliberate
----------------------------------------------
- **Module path.** Command logic lives in ``molt.commands.status``, not
  ``molt.cli.commands.status``:
  ``openspec/changes/adopt-typer-cli-shell/design.md`` D7 ("``from molt.commands.<x> import run``"),
  ``tasks.md`` 1.2 (creates ``src/molt/commands/``) and research doc 03 section 11.5 all agree.
  ``MILESTONES.md`` and the phase brief say ``molt.cli.commands.*`` and are superseded.
- **Entry point.** ``run(*, cwd, ...)`` with each option named after its long flag (dashes ->
  underscores) and injectable seams as keyword arguments. ``console=`` is injected here; ``git`` is
  **not**, because ``status`` needs a real repository for the since-ref comparison (which is why
  every test in this module is marked ``integration`` + ``git``).
- **``--output json`` prints to stdout** (``cli/status.md`` options table, ``guides/status.md``
  "Machine-readable output"). Upstream's ``status --output <file>`` wrote a JSON *file*
  (``status/index.ts:53-58``), so group-file row 7 is adapted rather than ported.
- **Plan JSON is snake_case** (``old_version`` / ``new_version``). ``guides/status.md`` and
  ``guides/dry-run-and-plans.md`` show snake_case; ``cli/status.md``, ``concepts/release-plan.md``
  and ``concepts/linked-vs-fixed.md`` show camelCase because they transcribe upstream's schema
  verbatim (research doc 03 section 5.3). That is a docs bug, not a spec: snake_case wins.
- **``preState`` does not exist.** ``pre.json`` and global pre mode are removed (research README
  section 4.2); the key is asserted *absent*, not merely ignored.
- **Gotcha 10.11 is fixed, not ported.** Upstream evaluates the CI gate before honouring
  ``--output``, so a failing gate leaves the JSON unwritten (``status/index.ts:43-59``). molt emits
  the plan first and trips the gate second (research doc 03 section 11.7 item 4).

The CI gate is exactly: exit 1 **iff** at least one versionable package changed **and** zero
changesets matched (``status/index.ts:43-51``). The four negative cases are parametrized as a matrix
precisely because a suite of only-positive gate tests is vacuous -- it would pass against an
implementation that always exits 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import RecordingConsole, scrub_ids, strip_ansi

from molt.versioning import BumpType

pytest.importorskip("molt.commands.status", reason="build step 6 - `molt status` is a TDD target")
pytest.importorskip("molt.errors", reason="build step 6 - molt.errors lands with the cli shell")

from molt.commands.status import run
from molt.errors import ExitError, MoltError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from tests.conftest import GitRepo

pytestmark = [pytest.mark.integration, pytest.mark.git]


# --------------------------------------------------------------------------------------
# Local workspace scaffolding
#
# `tmp_project` builds under `tmp_path/project` while `git_repo` owns `tmp_path/repo`, and the
# since-ref rows need the workspace *inside* the repository. These helpers write the same shapes
# `ProjectBuilder` writes, as LF bytes on every platform (`Path.write_text` would emit CRLF on
# Windows). They are file-writing helpers, not a redefinition of the shared fixtures.
# --------------------------------------------------------------------------------------

ROOT_PYPROJECT = """\
[project]
name = "workspace-root"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/*"]
"""

#: `.changeset/` must exist before any command runs (`ensureChangesetFolder`, commands/shared.ts).
#: A `README.md` in there is never read as a changeset (research doc 03 section 10.20), and being a
#: tracked file it keeps the directory present across branch switches.
CHANGESET_README = "# Changesets\n\nThis folder holds molt changeset files.\n"


@dataclass(frozen=True)
class Pkg:
    """One workspace member: name, version, PEP 508 dependency strings, private flag."""

    name: str
    version: str = "1.0.0"
    deps: tuple[str, ...] = ()
    private: bool = False
    files: Mapping[str, str] = field(default_factory=dict)


def write_lf(path: Path, text: str) -> None:
    """Write ``text`` as LF-only bytes, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def member_toml(pkg: Pkg) -> str:
    lines = [
        "[project]",
        f'name = "{pkg.name}"',
        f'version = "{pkg.version}"',
        f"dependencies = {json.dumps(list(pkg.deps))}",
    ]
    if pkg.private:
        # The Python analogue of npm's `"private": true` (research doc 02 section 12.1), the same
        # marker `ProjectBuilder.add_package(private=True)` writes.
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    return "\n".join(lines) + "\n"


def scaffold(repo: GitRepo, packages: Sequence[Pkg], *, config: str = "") -> None:
    """Write a uv workspace plus ``.changeset/`` into ``repo``.

    ``config`` is the body of the root ``[tool.molt]`` table.
    """
    root = repo.root
    text = ROOT_PYPROJECT
    if config:
        text += f"\n[tool.molt]\n{config.strip()}\n"
    write_lf(root / "pyproject.toml", text)
    write_lf(root / ".changeset" / "README.md", CHANGESET_README)
    for pkg in packages:
        write_lf(root / "packages" / pkg.name / "pyproject.toml", member_toml(pkg))
        for relative, body in pkg.files.items():
            write_lf(root / "packages" / pkg.name / relative, body)


def write_changeset(
    root: Path, cs_id: str, releases: Mapping[str, str], summary: str = "This is a summary"
) -> Path:
    """Write ``.changeset/<cs_id>.md``, byte-for-byte like ``ProjectBuilder.write_changeset``."""
    lines = ["---"]
    lines.extend(f'"{name}": {bump}' for name, bump in releases.items())
    lines.extend(["---", "", summary, ""])
    path = root / ".changeset" / f"{cs_id}.md"
    write_lf(path, "\n".join(lines))
    return path


def commit_all(repo: GitRepo, message: str) -> None:
    repo.run("add", "-A")
    repo.commit(message)


def branch_off(repo: GitRepo, name: str = "new-branch") -> None:
    """Commit the scaffold on ``main``, then switch to a feature branch (every row does this)."""
    commit_all(repo, "chore: scaffold workspace")
    repo.run("checkout", "-b", name)


def rename_default_branch(repo: GitRepo, name: str) -> None:
    """Rename ``main`` to ``name``, so ``main`` no longer exists in this repository.

    The since-ref rows below turn on ``main`` being *absent*. Every other row in this module
    branches from ``main`` and passes ``since="main"``, which makes the ``--since`` value and the
    configured base branch the same string -- and an implementation that ignored ``--since``, or
    hardcoded ``"main"``, would satisfy all of them. Removing the name is what makes the
    difference observable: a hardcoded ``git diff main...HEAD`` cannot resolve a ref that is not
    there, and fails loudly instead of silently agreeing.
    """
    repo.run("branch", "-m", "main", name)


def plan_json(captured: str) -> Any:
    """Parse ``--output json`` stdout.

    ``json.loads`` over the *whole* stream is the assertion: it fails if anything else -- a banner,
    a progress line, a stray log -- is written to stdout, which is exactly the contract
    ``molt status --output json > plan.json`` depends on.
    """
    return json.loads(strip_ansi(captured))


def release_tuples(plan: Any) -> list[tuple[str, BumpType, str, str, list[str]]]:
    """``(name, type, old_version, new_version, changesets)`` per release, in plan order."""
    return [
        (r.name, r.type, r.old_version, r.new_version, list(r.changesets)) for r in plan.releases
    ]


# --------------------------------------------------------------------------------------
# Row 1 / row 2 - status returns the release plan
# --------------------------------------------------------------------------------------


def test_returns_the_release_plan_for_a_simple_changeset(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Row 1 (`status.test.ts:45-107`): one minor changeset -> the assembled plan is returned.

    Upstream returns the ``ReleasePlan`` object when no ``--output`` is given
    (``status/index.ts:66``); molt keeps that, since ``status`` is the read-only face of the same
    computation ``version`` performs (``guides/status.md``).
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    plan = run(cwd=git_repo.root, since="main", console=console)

    assert [cs.id for cs in plan.changesets] == ["tidy-eels-return"]
    assert [cs.summary for cs in plan.changesets] == ["This is a summary"]
    assert release_tuples(plan) == [
        ("pkg-a", BumpType.MINOR, "1.0.0", "1.1.0", ["tidy-eels-return"])
    ]
    # `BumpType` is a `StrEnum`, so `"minor" == BumpType.MINOR` and the tuple comparison above is
    # satisfied by a plain `str` leaking out of the parser. The plan is a typed object the engine
    # consumes, not a bag of strings, so the exact type is part of the contract. (The same trap is
    # guarded for `--pre` at `test_cli.py`'s `--pre` row.)
    assert all(type(release.type) is BumpType for release in plan.releases), (
        "releases carry BumpType members, not bare strings that merely compare equal"
    )


@pytest.mark.parametrize("attribute", ["pre_state", "preState"])
def test_the_plan_carries_no_pre_state(
    git_repo: GitRepo, console: RecordingConsole, attribute: str
) -> None:
    """Row 1, adapted: ``preState`` is *absent* in molt, not merely undefined.

    Upstream's inline snapshot pins ``"preState": undefined`` (`status.test.ts:88`). molt deletes
    ``pre.json`` and global pre mode outright -- prerelease is the per-invocation
    ``molt version --pre {a,b,rc,dev}`` flag (research README section 4.2, section 6 decision 1) --
    so the field must not exist at all. Asserted rather than ignored: an implementation that ported
    the key as a permanent ``None`` would resurrect a concept molt does not have.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: changeset only")

    plan = run(cwd=git_repo.root, since="main", console=console)

    assert not hasattr(plan, attribute), "pre.json is gone; the plan has no pre-mode field"


def test_defaults_to_the_configured_base_branch(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Row 2 (`status.test.ts:109-173`): with no ``--since``, compare against ``base_branch``.

    ``cli/status.md`` documents the ``--since`` default as "base branch"; the config key is
    ``base_branch`` (``config/options.md``, pinned by ``tests/config/test_parse.py``).

    The base branch here is ``trunk`` and ``main`` **does not exist**. Written that way
    deliberately: with ``base_branch = "main"`` in a repo branched from ``main``, this row is
    equally satisfied by an implementation that never reads the config and hardcodes ``"main"``
    -- which is exactly the mutation that survived the first pass of this module.
    """
    scaffold(git_repo, [Pkg("pkg-a")], config='base_branch = "trunk"')
    commit_all(git_repo, "chore: scaffold workspace")
    rename_default_branch(git_repo, "trunk")
    git_repo.run("checkout", "-b", "new-branch")
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    plan = run(cwd=git_repo.root, console=console)

    assert release_tuples(plan) == [
        ("pkg-a", BumpType.MINOR, "1.0.0", "1.1.0", ["tidy-eels-return"])
    ]


def test_since_overrides_the_configured_base_branch(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """``--since`` is the flag the CI gate depends on (``status.md:106``, ``--since origin/main``).

    Both refs exist and they select **different** changed-package sets, so the two halves cannot
    both pass unless the ref actually reaches the diff:

    * ``main`` -> ``trunk`` added a change to ``pkg-a`` that no changeset covers -> the gate fires.
    * ``trunk`` (the configured base branch, used when ``--since`` is absent) -> the feature branch
      changed nothing -> the gate holds.

    An implementation that ignored ``--since`` fails the first half; one that hardcoded a single
    ref fails the second. Asserted in one test on purpose: split apart, each half is satisfied by
    the mutation the other half catches.
    """
    scaffold(git_repo, [Pkg("pkg-a")], config='base_branch = "trunk"')
    commit_all(git_repo, "chore: scaffold workspace")
    git_repo.run("checkout", "-b", "trunk")
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    commit_all(git_repo, "feat: updated a on trunk")
    git_repo.run("checkout", "-b", "new-branch")
    git_repo.commit("chore: nothing to release", allow_empty=True)

    plan = run(cwd=git_repo.root, console=console)

    assert list(plan.releases) == [], "nothing changed since trunk, so the gate holds"

    console.reset()
    with pytest.raises(ExitError) as excinfo:
        run(cwd=git_repo.root, since="main", console=console)

    assert excinfo.value.code == 1, "since=main sees trunk's uncovered change to pkg-a"
    assert "molt add" in console.text()


# --------------------------------------------------------------------------------------
# Rows 3-5, 8-12 - the CI exit-code contract (research doc 03 section 5.2)
# --------------------------------------------------------------------------------------


def gate_plain_change(repo: GitRepo) -> None:
    """Row 3 (`status.test.ts:175-204`): a versionable package changed, nobody wrote a changeset."""
    scaffold(repo, [Pkg("pkg-a")])
    branch_off(repo)
    write_lf(repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    commit_all(repo, "feat: updated a")


def gate_pattern_match_change(repo: GitRepo) -> None:
    """Row 9 (`status.test.ts:392-424`): the change matches ``changed_file_patterns``."""
    scaffold(
        repo,
        [Pkg("pkg-a", files={"src/a.py": 'VALUE = "a"\n'})],
        config='changed_file_patterns = ["src/**"]',
    )
    branch_off(repo)
    write_lf(repo.root / "packages" / "pkg-a" / "src" / "a.py", 'VALUE = "updated a"\n')
    commit_all(repo, "feat: updated a")


GATE_TRIPS_CASES: list[tuple[Callable[[GitRepo], None], str]] = [
    (gate_plain_change, "row 3: changed package + zero changesets is the whole point of the gate"),
    (gate_pattern_match_change, "row 9: a pattern-matched change is still a change"),
]


@pytest.mark.parametrize(("setup", "why"), GATE_TRIPS_CASES)
def test_ci_gate_exits_one(
    git_repo: GitRepo,
    console: RecordingConsole,
    setup: Callable[[GitRepo], None],
    why: str,
) -> None:
    """The gate fires: ``ExitError(1)`` (``status/index.ts:43-51``).

    Upstream only asserts ``rejects.toThrow()``; molt pins the code because the exit-code contract
    (research doc 03 section 11.6, ``cli-shell`` spec) makes 1 load-bearing for CI. The guidance
    text is pinned too -- it is the only thing that tells a contributor what to do next
    (``guides/status.md``, "The CI gate").
    """
    setup(git_repo)

    with pytest.raises(ExitError) as excinfo:
        run(cwd=git_repo.root, since="main", console=console)

    assert excinfo.value.code == 1, why
    text = console.text()
    assert "molt add" in text, why
    assert "--empty" in text, why


def gate_no_changes(repo: GitRepo) -> None:
    """Row 4 (`status.test.ts:206-235`): nothing changed at all."""
    scaffold(repo, [Pkg("pkg-a")])
    branch_off(repo)
    repo.commit("chore: empty", allow_empty=True)


def gate_unmatched_pattern(repo: GitRepo) -> None:
    """Row 8 (`status.test.ts:351-390`): the change misses ``changed_file_patterns``."""
    scaffold(repo, [Pkg("pkg-a")], config='changed_file_patterns = ["src/**"]')
    branch_off(repo)
    write_lf(repo.root / "packages" / "pkg-a" / "unrelated.json", "{}\n")
    commit_all(repo, "chore: add unrelated thing")


def gate_only_ignored(repo: GitRepo) -> None:
    """Row 11 (`status.test.ts:493-537`): only an ``ignore``d package changed."""
    scaffold(repo, [Pkg("pkg-a"), Pkg("pkg-b")], config='ignore = ["pkg-b"]')
    branch_off(repo)
    write_lf(repo.root / "packages" / "pkg-b" / "b.py", 'VALUE = "updated b"\n')
    commit_all(repo, "chore: updated b")


def gate_only_private_unversioned(repo: GitRepo) -> None:
    """Row 12 (`status.test.ts:539-586`): only a non-versionable private package changed.

    Upstream keys this off ``package.json``'s ``private: true`` plus
    ``privatePackages: {version: false}``. The Python marker is the ``Private :: Do Not Upload``
    classifier (research doc 02 section 12.1); the config knob is ``private_packages = false``,
    which normalizes to ``{version: false}`` (``tests/config/test_parse.py``, row 25).
    """
    scaffold(
        repo,
        [Pkg("pkg-a"), Pkg("pkg-b", private=True)],
        config="private_packages = false",
    )
    branch_off(repo)
    write_lf(repo.root / "packages" / "pkg-b" / "b.py", 'VALUE = "updated b"\n')
    commit_all(repo, "chore: updated b")


GATE_HOLDS_CASES: list[tuple[Callable[[GitRepo], None], str]] = [
    (gate_no_changes, "row 4: nothing changed -> an empty plan is not an error"),
    (gate_unmatched_pattern, "row 8: the change does not match changed_file_patterns"),
    (gate_only_ignored, "row 11: an ignored package is not versionable"),
    (gate_only_private_unversioned, "row 12: private_packages=false makes pkg-b unversionable"),
]


@pytest.mark.parametrize(("setup", "why"), GATE_HOLDS_CASES)
def test_ci_gate_does_not_fire(
    git_repo: GitRepo,
    console: RecordingConsole,
    setup: Callable[[GitRepo], None],
    why: str,
) -> None:
    """The four negative cases, as a matrix.

    Research doc 03 section 5.2 spells the rule out: the gate does **not** fire when nothing
    changed, when only ignored packages changed, when only non-versionable private packages
    changed, or when the changes do not match ``changedFilePatterns``. Written as a matrix on
    purpose -- an implementation that deleted the gate entirely passes every positive test, and one
    that raises unconditionally passes none of these.
    """
    setup(git_repo)

    plan = run(cwd=git_repo.root, since="main", console=console)

    assert list(plan.changesets) == [], why
    assert list(plan.releases) == [], why


def test_a_changed_package_with_a_changeset_does_not_trip_the_gate(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Row 5 (`status.test.ts:237-276`): change + changeset is the happy path."""
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    plan = run(cwd=git_repo.root, since="main", console=console)

    assert [cs.id for cs in plan.changesets] == ["tidy-eels-return"]


def test_pattern_matched_change_with_a_changeset_returns_the_plan(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Row 10 (`status.test.ts:426-491`): ``changed_file_patterns`` set, changeset present.

    Note the upstream fixture edits ``a.js`` -- *outside* ``src/**`` -- so the plan is non-empty
    purely because a changeset exists. Ported as written: it proves the changeset path is
    independent of the changed-file filter.
    """
    scaffold(
        git_repo,
        [Pkg("pkg-a", files={"src/a.py": 'VALUE = "a"\n'})],
        config='changed_file_patterns = ["src/**"]',
    )
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    plan = run(cwd=git_repo.root, since="main", console=console)

    assert release_tuples(plan) == [
        ("pkg-a", BumpType.MINOR, "1.0.0", "1.1.0", ["tidy-eels-return"])
    ]


# --------------------------------------------------------------------------------------
# Row 6 - the verbose flag. Upstream is an `it.todo`; molt implements it.
# --------------------------------------------------------------------------------------


def test_verbose_adds_the_projected_version_and_the_changeset_paths(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Row 6 (`status.test.ts:278`, an unimplemented ``it.todo``) -- written as a real test.

    ``printStatus`` already supports it upstream (``status/index.ts:96-105``: append
    ``-> newVersion`` then one ``.changeset/<id>.md`` line per contributing changeset); only the
    test was never written. molt ships it and documents the exact shape in ``cli/status.md``
    ("Verbose, with projected versions and sources") and ``guides/status.md``.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    run(cwd=git_repo.root, since="main", verbose=True, console=console)

    text = console.text()
    assert "Packages to be bumped:" in text
    assert "minor" in text
    assert "pkg-a -> 1.1.0" in text
    assert ".changeset/tidy-eels-return.md" in text


def test_non_verbose_omits_versions_and_sources(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """The complement of row 6: without ``--verbose`` the grouped list is bare.

    Research doc 03 section 5.4 shows both renderings side by side. Asserting the *absence* is what
    stops "verbose" from silently becoming the only mode.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    run(cwd=git_repo.root, since="main", console=console)

    text = console.text()
    assert "pkg-a" in text
    assert "1.1.0" not in text
    assert ".changeset/" not in text


def test_verbose_labels_a_propagated_release_as_a_dependency_bump(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """molt-NEW: a release nobody wrote a changeset for is labelled, not left blank.

    ``guides/status.md`` renders it as ``- (dependency bump)`` under the propagated package. The
    plan already distinguishes the two cases -- a propagated release carries ``changesets: []``
    (``guides/status.md``, "A release driven only by propagation ... carries an empty changesets
    array") -- so verbose output has something true to say instead of printing nothing.
    """
    scaffold(git_repo, [Pkg("pkg-a"), Pkg("pkg-b", deps=("pkg-a==1.0.0",))])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    plan = run(cwd=git_repo.root, since="main", verbose=True, console=console)

    by_name = {r.name: r for r in plan.releases}
    assert by_name["pkg-b"].type == BumpType.PATCH
    assert list(by_name["pkg-b"].changesets) == [], "propagated releases cite no changeset"
    assert "(dependency bump)" in console.text()


def test_nothing_to_release_still_prints_the_header(
    git_repo: GitRepo, console: RecordingConsole
) -> None:
    """Research doc 03 section 5.4: with nothing pending the header prints with an empty body.

    Also ``guides/status.md`` ("When there is nothing pending, the header prints with an empty
    body"). Silence would be indistinguishable from a crash.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    git_repo.commit("chore: empty", allow_empty=True)

    run(cwd=git_repo.root, since="main", console=console)

    assert "Packages to be bumped:" in console.text()


# --------------------------------------------------------------------------------------
# Row 7 - `--output json`, adapted: stdout, not a file (research doc 03 section 5.3)
# --------------------------------------------------------------------------------------

#: The plan for the single-minor-changeset fixture, id-scrubbed. Written as a literal rather than a
#: syrupy snapshot on purpose: this module never runs until `molt.commands.status` lands, so the
#: first `--snapshot-update` would bless whatever the implementation emitted -- including the
#: camelCase keys this literal exists to reject.
EXPECTED_PLAN = {
    "changesets": [
        {
            "id": "~changeset-0~",
            "summary": "This is a summary",
            "releases": [{"name": "pkg-a", "type": "minor"}],
        }
    ],
    "releases": [
        {
            "name": "pkg-a",
            "type": "minor",
            "old_version": "1.0.0",
            "new_version": "1.1.0",
            "changesets": ["~changeset-0~"],
        }
    ],
}


def test_output_json_prints_the_plan_to_stdout(
    git_repo: GitRepo, console: RecordingConsole, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 7, adapted (`status.test.ts:280-349`; ``status/index.ts:53-58``).

    Upstream writes ``JSON.stringify(releasePlan, undefined, 2)`` to the path given by ``--output``
    and returns ``undefined``. molt's ``--output json`` prints the plan to **stdout** so it can be
    piped (``cli/status.md``: "Emit the release plan as JSON on stdout"), which is what makes
    ``molt status --output json > plan.json`` work without the command choosing a filename.

    ``scrub_ids`` is the port of upstream's ``replaceHumanIds`` (`status.test.ts:11-35`); the
    comparison is an explicit dict rather than a snapshot, which is strictly stronger because it
    also pins the key spelling.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: updated a")

    run(cwd=git_repo.root, since="main", output="json", console=console)

    assert scrub_ids(plan_json(capsys.readouterr().out)) == EXPECTED_PLAN


def test_output_json_keys_are_snake_case(
    git_repo: GitRepo, console: RecordingConsole, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan is snake_case; upstream's camelCase spelling does not survive the port.

    ``guides/status.md`` and ``guides/dry-run-and-plans.md`` both show ``old_version`` /
    ``new_version``. ``cli/status.md``, ``concepts/release-plan.md`` and
    ``concepts/linked-vs-fixed.md`` still show ``oldVersion`` / ``newVersion`` because they
    transcribe upstream's ``ReleasePlan`` verbatim (research doc 03 section 5.3) -- three docs bugs,
    not three specs. Asserted on the raw text so a camelCase alias cannot satisfy it.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: changeset only")

    run(cwd=git_repo.root, since="main", output="json", console=console)

    raw = strip_ansi(capsys.readouterr().out)
    assert '"old_version"' in raw
    assert '"new_version"' in raw
    assert "oldVersion" not in raw
    assert "newVersion" not in raw
    assert "preState" not in raw
    assert "pre_state" not in raw


def test_output_json_is_emitted_even_when_the_ci_gate_trips(
    git_repo: GitRepo, console: RecordingConsole, capsys: pytest.CaptureFixture[str]
) -> None:
    """DELIBERATE DIVERGENCE -- research doc 03 section 10.11, fixed per section 11.7 item 4.

    Upstream evaluates the gate at ``status/index.ts:43-51`` and only honours ``--output`` at
    ``:53``, so *the one run a CI job most wants machine-readable output from* -- the failing one --
    produces no JSON at all. Every consumer then has to special-case a missing file.

    molt emits the plan first and trips the gate second. Both halves are asserted in the same test
    on purpose: separately, each would pass against an implementation that kept upstream's order.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_lf(git_repo.root / "packages" / "pkg-a" / "a.py", 'VALUE = "updated a"\n')
    commit_all(git_repo, "feat: updated a")

    with pytest.raises(ExitError) as excinfo:
        run(cwd=git_repo.root, since="main", output="json", console=console)

    assert excinfo.value.code == 1
    assert plan_json(capsys.readouterr().out) == {"changesets": [], "releases": []}


def test_molt_output_env_backfills_the_output_flag(
    git_repo: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """``MOLT_OUTPUT`` back-fills ``--output`` when the flag is absent.

    Row 10 of the reference ``cli.test.ts`` block does this for ``git-tag`` + ``CHANGESETS_OUTPUT``
    (``cli.ts:120, 175``); the ``cli-shell`` spec generalizes it ("Output env var backfills the
    flag"). Back-filling happens in the CLI's ``normalize_options`` pass, not in ``run``, so this
    one row drives the Typer app -- guarded per-test so the rest of the module still runs the moment
    ``molt.commands.status`` lands, whether or not the shell is finished.
    """
    pytest.importorskip("typer", reason="build step 6 - typer lands with the cli-shell change")
    pytest.importorskip("molt.cli", reason="build step 6 - the CLI shell is a TDD target")
    from tests.cli.fake_cli import invoke_cli, require_cli_app

    import molt.cli

    if getattr(molt.cli, "app", None) is None:
        pytest.skip("molt.cli is still the placeholder stub: no Typer `app` attribute")

    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: changeset only")
    capsys.readouterr()

    result = invoke_cli(
        require_cli_app(),
        ["status", "--cwd", str(git_repo.root), "--since", "main"],
        env={"MOLT_OUTPUT": "json"},
    )

    assert result.exit_code == 0
    # Sliced from the first `{` rather than parsed whole: the strict "stdout is exactly one JSON
    # document" contract is pinned at the run() seam above, where the emission happens. Here the
    # only claim under test is that the env var reached the flag.
    raw = strip_ansi(result.stdout)
    assert scrub_ids(json.loads(raw[raw.index("{") :])) == EXPECTED_PLAN


# --------------------------------------------------------------------------------------
# SC-3 - `--output` accepts only `json` (owner ruling 2026-07-31, session 6)
# --------------------------------------------------------------------------------------


def test_the_output_option_help_promises_only_json() -> None:
    """``SC-3``, ruled 2026-07-31 -- the help text stopped promising a file path.

    ``molt status --output plan.json`` is refused, but the registered help read "Write the plan
    here (or `json` for stdout)", which describes a filename molt does not accept. Help text is the
    surface a user reads before they type the command, so a wrong one costs a failed run.
    """
    import typer

    import molt.cli

    # `get_command` is annotated as returning a plain `click.Command`; a Typer app with several
    # commands always yields a `TyperGroup`, which is what carries `.commands`. Narrowed with an
    # `isinstance` rather than suppressed, so a genuinely command-less app fails here loudly.
    group = typer.main.get_command(molt.cli.app)
    assert isinstance(group, typer.core.TyperGroup)
    command = group.commands["status"]
    help_text = next(p for p in command.params if "--output" in p.opts).help or ""

    assert "json" in help_text
    assert "file" not in help_text.lower()
    assert "here" not in help_text.lower()


def test_a_non_json_output_value_is_refused(git_repo: GitRepo, console: RecordingConsole) -> None:
    """A filename is refused, and no file is created (``SC-3``).

    ``status`` writing to disk is exactly the promise the command makes it does not make: the
    redirection belongs to the shell, where the user can see it. Upstream's ``--output <file>``
    did write the plan to disk, so the refusal is the divergence, and it is now ratified rather
    than merely shipped.
    """
    scaffold(git_repo, [Pkg("pkg-a")])
    branch_off(git_repo)
    write_changeset(git_repo.root, "tidy-eels-return", {"pkg-a": "minor"})
    commit_all(git_repo, "feat: changeset only")

    with pytest.raises(MoltError) as excinfo:
        run(cwd=git_repo.root, since="main", output="plan.json", console=console)

    assert "json" in str(excinfo.value)
    assert not (git_repo.root / "plan.json").exists()
