"""Conformance tests for ``molt.git`` -- the typed ``subprocess`` wrapper around ``git``.

Ports all 30 rows of ``packages/git/src/index.test.ts`` @ v3.0.0-next.9, catalogued in
``roadmap/research/test-suite/08-infra-forge-utils.md`` (the ``packages/git/src/index.test.ts``
section: 20 Port / 10 Adapt), plus the molt-native rows listed under "Divergences" below.

**Every behavioral row runs against a real ``git`` binary** through the frozen ``git_repo``
fixture (``tests/conftest.py``). None of them mock the subprocess -- this file carries the whole
Windows-correctness burden of the git layer (research README section 5 item 15, "Windows-correct
from day one"). Arg construction is therefore never asserted directly; it is asserted through
behavior that only the right args can produce (see ``diff.relative`` below).

Deviation from the test contract, section 5 (noted here as the contract instructs)
-----------------------------------------------------------------------------------
The contract lists ``molt.git``'s surface as ``add, commit, tag, rev_parse, diff_name_only, log,
merge_base, status``. Those are kept, but only as the **low-level primitives**. The high-level
queries use the ``get_*`` prefix, because two other sources already pin those spellings and a
committed test double outranks a prose contract:

* ``tests/cli/fake_cli.py::FakeGit`` (frozen, shared by the four P5 CLI writers) pins
  ``add(*paths)``, ``commit(message, **kwargs) -> str``, ``tag(name, message=None)``,
  ``get_current_commit_id()``, ``get_commits_that_add_files(paths) -> dict[str, str]``,
  ``get_all_tags() -> set[str]`` and ``tag_exists(name)``. ``molt.git.Git`` must be substitutable
  for that double.
* ``tests/cli/test_add.py`` additionally uses ``diff_name_only(ref)`` and
  ``get_changed_packages_since_ref(ref)``.
* Upstream itself spells them ``getAllTags`` / ``getCommitsThatAddFiles`` / ``getCurrentCommitId``.

Names pinned here for the first time (nothing else had named them):
``get_diverged_commit(ref)``, ``get_changed_files_since(ref, *, full_path=False)``,
``get_changed_changeset_files_since_ref(ref)``.

Divergences from upstream asserted by this file
------------------------------------------------
1. **A failed ``commit`` raises.** Upstream's ``commit`` returns ``exitCode === 0`` and every
   caller ignores it, so a failed commit leaves the process exiting 0 -- research README section
   3.4, "Failed ``git commit`` only logged; exit code stays 0 -> Silent CI failure".
   ``packages/git/src/index.ts:20-25``. ``tests/cli/test_version.py::test_a_failed_commit_is_fatal``
   already assumes the raising shape; this file pins it at the git layer.
   ``add`` gets the same treatment (upstream ``index.ts:9-18`` ``console.log``s and returns
   ``false``).
2. **``get_commits_that_add_files`` returns ``dict[path, sha]``**, not upstream's positional list
   with ``undefined`` holes (``index.ts:141``). A dict preserves insertion order *and*
   missing-ness without a sentinel, and is what the frozen ``FakeGit`` already returns. Row 16
   ("blanks for missing") therefore ports as "the missing path is absent from the dict and the
   present paths keep their order".
3. **``get_all_tags()`` on a tagless repo returns ``set()``.** Upstream splits an empty string on
   ``\\n`` and returns ``Set { "" }`` (``index.ts:34-36``) -- a phantom empty tag name.
4. **``get_changed_packages_since_ref`` returns package names** (``list[str]``), matching the
   ``tests/cli/test_add.py`` double, not upstream's ``Package[]`` objects.

Names are compared PEP 503-normalized project-wide (research README section 4.5); every fixture
here already uses normalized names, so no row depends on the normalizer.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("molt.errors", reason="build step 9 - molt.errors is a TDD target")
pytest.importorskip("molt.git", reason="build step 9 - molt.git is a TDD target")

from molt.errors import GitError
from molt.git import Git

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tests.conftest import GitRepo

pytestmark = [pytest.mark.git, pytest.mark.integration]

#: Roughly how far the deepening loop extends a shallow clone per round
#: (``index.ts:129`` -- ``deepenCloneBy({ by: 50 })``). Rows 13-16 are sized against it.
DEEPEN_BY = 50


# ======================================================================================
# Local helpers. Named write_/run_/build_ so pytest's `python_functions = "test"` PREFIX
# match cannot collect them as tests.
# ======================================================================================


def write_file(path: Path, content: str) -> Path:
    """Create parents and write ``content`` as **LF-only bytes on every platform**.

    ``Path.write_text`` translates newlines, so on Windows every seeded file would land as CRLF
    while the same fixture on Linux produced LF. The ``git_repo`` fixture pins
    ``.gitattributes = * text=auto eol=lf`` for the same reason; writing bytes here keeps the
    working tree and the index agreeing about what "unchanged" means.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def write_tree(repo: GitRepo, files: Mapping[str, str]) -> None:
    """Write ``{relative path: content}`` into the working tree without staging anything."""
    for rel, content in files.items():
        write_file(repo.root / rel, content)


def run_seed(repo: GitRepo, files: Mapping[str, str], message: str = "seed") -> str:
    """Write ``files``, stage everything, commit; return the new HEAD sha.

    Port of the reference ``gitdir({...})`` helper (``@changesets/test-utils``), except the repo
    already exists: ``git_repo`` seeds a root commit holding ``.gitattributes``.
    """
    write_tree(repo, files)
    repo.run("add", "-A")
    return repo.commit(message)


def run_commit_file(repo: GitRepo, rel: str, content: str, message: str | None = None) -> str:
    """Write one file, stage just that file, commit; return the new HEAD sha."""
    write_file(repo.root / rel, content)
    repo.run("add", "--", rel)
    return repo.commit(message or f"add file {rel}")


def run_dummy_commits(repo: GitRepo, count: int) -> None:
    """Port of the reference ``createDummyCommits`` (``index.test.ts:280-284``).

    Empty commits push an interesting commit out of a shallow clone's window.
    """
    for index in range(count):
        repo.commit(f"dummy commit {index}", allow_empty=True)


def run_commit_count(repo: GitRepo) -> int:
    """Port of the reference ``getCommitCount`` (``index.test.ts:20-25``)."""
    return int(repo.run("rev-list", "--count", "HEAD").stdout.strip())


def read_staged(repo: GitRepo) -> list[str]:
    """``git diff --name-only --cached`` as a list; git always reports forward slashes."""
    out = repo.run("diff", "--name-only", "--cached").stdout
    return [line for line in out.split("\n") if line.strip()]


def read_commit_of(repo: GitRepo, ref: str) -> str:
    """The commit a ref points at -- ``rev-list -n 1`` peels an annotated tag to its commit."""
    return repo.run("rev-list", "-n", "1", ref).stdout.strip()


ROOT_PYPROJECT = """\
[project]
name = "workspace-root"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = [{members}]
"""

MEMBER_PYPROJECT = """\
[project]
name = "{name}"
version = "1.0.0"
dependencies = []
"""


def build_workspace(
    repo: GitRepo,
    members: Sequence[str],
    packages: Mapping[str, str],
    *,
    commit: bool = True,
) -> None:
    """Scaffold a uv workspace: root ``[tool.uv.workspace].members`` + member ``pyproject.toml``s.

    The Adapt half of this group (rows 21-26): upstream's root ``package.json`` with
    ``"workspaces": [...]`` and member ``package.json`` files with ``"name"`` become a root
    ``[tool.uv.workspace] members`` glob list and member ``[project].name`` keys.
    ``packages`` maps a directory relative to the repo root -> the declared project name, so a row
    can prove the name comes from the manifest rather than from the directory.

    Not reusing the ``tmp_project`` fixture is deliberate: it builds under ``tmp_path/project``
    while ``git_repo`` lives in ``tmp_path/repo``, so its tree is not inside the repository.
    """
    rendered = ", ".join(f'"{m}"' for m in members)
    write_file(repo.root / "pyproject.toml", ROOT_PYPROJECT.format(members=rendered))
    for rel_dir, name in packages.items():
        write_file(repo.root / rel_dir / "pyproject.toml", MEMBER_PYPROJECT.format(name=name))
    if commit:
        repo.run("add", "-A")
        repo.commit("chore: scaffold workspace")


CHANGESET_BODY = '---\n"pkg-a": minor\n---\n\nAwesome summary\n'


def write_changeset_file(repo: GitRepo, changeset_id: str = "strange-words-combine") -> str:
    """Write ``.changeset/<id>.md`` and stage it; return the id.

    Port of the reference's ``writeChangeset(...)`` + ``add(".changeset", cwd)``
    (``index.test.ts:766-778``). The file must be **staged**: ``git diff <commit>`` compares the
    working tree to a commit and never reports untracked files.
    """
    write_file(repo.root / ".changeset" / f"{changeset_id}.md", CHANGESET_BODY)
    repo.run("add", "--", ".changeset")
    return changeset_id


# ======================================================================================
# getDivergedCommit -- rows 1-2
# ======================================================================================


def test_get_diverged_commit_returns_head_when_branches_have_not_diverged(
    git_repo: GitRepo,
) -> None:
    """Row 1 (``index.test.ts:29-44``): ``merge-base main HEAD`` is HEAD when still on main."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n', "b.js": 'export default "b"\n'})

    first_sha = git.get_current_commit_id()
    write_file(git_repo.root / "b.js", 'export default "updated b"\n')
    git.add("b.js")
    second_sha = git.commit("update b")

    assert first_sha != second_sha, "the second commit must actually move HEAD"
    assert git.get_diverged_commit("main") == second_sha


def test_get_diverged_commit_finds_where_the_branch_diverged(git_repo: GitRepo) -> None:
    """Row 2 (``index.test.ts:46-70``): after ``checkout -b``, merge-base is the main sha."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n', "b.js": 'export default "b"\n'})
    main_sha = git.get_current_commit_id()

    git_repo.run("checkout", "-b", "my-branch")
    write_file(git_repo.root / "b.js", 'export default "updated b"\n')
    git.add("b.js")
    branch_sha = git.commit("update b")

    assert main_sha != branch_sha
    assert git.get_diverged_commit("main") == main_sha


def test_get_diverged_commit_raises_on_an_unknown_ref(git_repo: GitRepo) -> None:
    """molt-native. ``index.ts:54-59`` throws when ``merge-base`` exits non-zero.

    Load-bearing: ``get_changed_changeset_files_since_ref`` swallows exactly this error
    (``index.ts:262-265``), so if it never fires the swallow row below is vacuous.
    """
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    with pytest.raises(GitError):
        Git(git_repo.root).get_diverged_commit("no-such-ref")


# ======================================================================================
# add -- rows 3-5
# ======================================================================================


def test_add_stages_a_file(git_repo: GitRepo) -> None:
    """Row 3 (``index.test.ts:74-92``): ``add("a.js")`` -> ``diff --cached --name-only`` is a.js."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n', "b.js": 'export default "b"\n'})

    write_file(git_repo.root / "a.js", 'export default "updated a"\n')
    git.add("a.js")

    assert read_staged(git_repo) == ["a.js"]


ADD_MULTIPLE_CASES = [
    ("separate", "one path per call, as upstream does (index.test.ts:103-104)"),
    ("variadic", "molt's `add(*paths)` -- the frozen FakeGit signature -- in a single call"),
]


@pytest.mark.parametrize("style,why", ADD_MULTIPLE_CASES)
def test_add_stages_multiple_files(git_repo: GitRepo, style: str, why: str) -> None:
    """Row 4 (``index.test.ts:94-117``): both files staged, order preserved.

    ``molt.git.Git.add`` is variadic (``add(*paths)``, pinned by ``tests/cli/fake_cli.py``), so
    both call shapes have to produce the same index.
    """
    git = Git(git_repo.root)
    run_seed(
        git_repo,
        {
            "a.js": 'export default "a"\n',
            "b.js": 'export default "b"\n',
            "c.js": 'export default "c"\n',
        },
    )

    write_file(git_repo.root / "a.js", 'export default "updated a"\n')
    write_file(git_repo.root / "c.js", 'export default "updated c"\n')
    if style == "separate":
        git.add("a.js")
        git.add("c.js")
    else:
        git.add("a.js", "c.js")

    assert read_staged(git_repo) == ["a.js", "c.js"], why


def test_add_stages_a_directory(git_repo: GitRepo) -> None:
    """Row 5 (``index.test.ts:119-144``): ``add("foo")`` stages ``foo/a.js`` and ``foo/b.js``."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"foo/a.js": 'export default "a"\n', "foo/b.js": 'export default "b"\n'})

    write_file(git_repo.root / "foo" / "a.js", 'export default "updated a"\n')
    write_file(git_repo.root / "foo" / "b.js", 'export default "updated b"\n')
    git.add("foo")

    assert read_staged(git_repo) == ["foo/a.js", "foo/b.js"]


def test_add_accepts_native_paths_and_absolute_paths(git_repo: GitRepo) -> None:
    """molt-native Windows row, extending row 5 (``index.test.ts:119-144``).

    Upstream only ever passes a repo-relative POSIX string. molt's callers hold ``pathlib.Path``
    objects (the changeset dir, a member's ``pyproject.toml``), and ``str(Path("foo/bar"))`` is
    ``"foo\\\\bar"`` on Windows while an absolute path is ``"C:\\\\...\\\\foo\\\\bar\\\\a.js"``.
    Both forms must reach git intact and both must be reported back by git in its own canonical
    forward-slash, repo-relative spelling. On POSIX this is the same assertion as row 5; on
    Windows it is the one that fails if a caller's ``Path`` is mangled on the way through.
    """
    git = Git(git_repo.root)
    run_seed(
        git_repo,
        {
            "foo/bar/a.js": 'export default "a"\n',
            "foo/bar/b.js": 'export default "b"\n',
        },
    )

    write_file(git_repo.root / "foo" / "bar" / "a.js", 'export default "updated a"\n')
    write_file(git_repo.root / "foo" / "bar" / "b.js", 'export default "updated b"\n')

    git.add(git_repo.root / "foo" / "bar" / "a.js")  # absolute, native separators
    assert read_staged(git_repo) == ["foo/bar/a.js"]

    git.add(git_repo.root / "foo" / "bar")  # absolute directory, native separators
    assert read_staged(git_repo) == ["foo/bar/a.js", "foo/bar/b.js"]


def test_a_failed_add_raises(git_repo: GitRepo) -> None:
    """molt-native. Upstream ``index.ts:9-18`` ``console.log``s the stderr and returns ``false``.

    Nobody checks that boolean, so a failed ``add`` is invisible -- the same silent-failure shape
    research README section 3.4 records for ``commit``. molt raises.
    """
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    with pytest.raises(GitError):
        Git(git_repo.root).add("this-path-does-not-exist.js")


# ======================================================================================
# commit -- row 6 + the net-new "a failed commit is not silent" row
# ======================================================================================


def test_commit_records_the_message_and_returns_the_new_head(git_repo: GitRepo) -> None:
    """Row 6 (``index.test.ts:148-163``): ``log -1 --pretty=%B`` equals the message.

    Also pins the return value: ``tests/cli/fake_cli.py::FakeGit.commit`` returns a sha, so the
    real wrapper returns the **new** HEAD sha rather than upstream's boolean (``index.ts:24``).
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    before = git.get_current_commit_id()

    write_file(git_repo.root / "a.js", 'export default "updated a"\n')
    git.add("a.js")
    returned = git.commit("update a.js")

    message = git_repo.run("log", "-1", "--pretty=%B").stdout.strip()
    assert message == "update a.js"
    assert returned == git.get_current_commit_id() != before, "commit returns the NEW head sha"


def run_break_commit(repo: GitRepo, mode: str) -> None:
    """Arrange for the next ``git commit`` in ``repo`` to exit non-zero."""
    if mode == "pre_commit_hook":
        hook = repo.root / ".git" / "hooks" / "pre-commit"
        write_file(hook, "#!/bin/sh\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    elif mode == "index_lock":
        write_file(repo.root / ".git" / "index.lock", "")
    else:  # pragma: no cover - guards a typo in the parametrization
        raise AssertionError(f"unknown break mode {mode!r}")


def run_unbreak_commit(repo: GitRepo, mode: str) -> None:
    target = (
        repo.root / ".git" / "hooks" / "pre-commit"
        if mode == "pre_commit_hook"
        else repo.root / ".git" / "index.lock"
    )
    target.unlink(missing_ok=True)


FAILED_COMMIT_CASES = [
    ("pre_commit_hook", "a repo policy hook rejects the commit (git exits 1)"),
    ("index_lock", "a stale .git/index.lock -- a concurrent git run (git exits 128)"),
]


@pytest.mark.parametrize("mode,why", FAILED_COMMIT_CASES)
def test_a_failed_commit_raises_instead_of_reporting_success(
    git_repo: GitRepo, mode: str, why: str
) -> None:
    """NET-NEW (research README section 3.4). Upstream bug molt deliberately does NOT port.

    ``index.ts:20-25`` returns ``gitCmd.exitCode === 0`` and every call site discards it, so a
    ``version`` run whose commit failed still exits 0 -- "Silent CI failure". molt surfaces it.
    ``tests/cli/test_version.py::test_a_failed_commit_is_fatal`` pins the same rule one layer up.

    Both failure modes are real and neither depends on ``--allow-empty``, which upstream hardcodes
    (``index.ts:21``): an implementation that swallowed the exit code would return normally here
    and this test would be the only thing that noticed.
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    head_before = git.get_current_commit_id()

    write_file(git_repo.root / "a.js", 'export default "updated a"\n')
    git.add("a.js")

    run_break_commit(git_repo, mode)
    try:
        with pytest.raises(GitError):
            git.commit("this commit cannot succeed")
    finally:
        run_unbreak_commit(git_repo, mode)

    assert git.get_current_commit_id() == head_before, why


# ======================================================================================
# getAllTags / tag / tagExists -- rows 7-11
# ======================================================================================


def test_get_all_tags_returns_every_tag(git_repo: GitRepo) -> None:
    """Row 7 (``index.test.ts:167-177``): both tags are in the returned set."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})

    git.tag("test_tag")
    git.tag("test_tag2")

    assert git.get_all_tags() == {"test_tag", "test_tag2"}


def test_get_all_tags_is_empty_on_a_repo_without_tags(git_repo: GitRepo) -> None:
    """Divergence 3. ``index.ts:34-36`` splits ``"".trim()`` on ``\\n`` -> ``Set { "" }``.

    A phantom empty tag name makes ``tagExists("")`` style checks and any ``len(tags)`` reasoning
    wrong. molt returns an empty set.
    """
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    assert Git(git_repo.root).get_all_tags() == set()


def test_tag_points_at_the_current_head_and_is_annotated(git_repo: GitRepo) -> None:
    """Row 8 (``index.test.ts:181-196``): ``rev-list -n1 <tag>`` equals HEAD.

    The annotated-ness assertion is the load-bearing half. ``index.ts:41-44`` uses ``-m`` on
    purpose -- "it's important we use the -m flag to create annotated tag otherwise
    'git push --follow-tags' won't actually push the tags". A lightweight tag still satisfies
    ``rev-list -n1``, so without ``cat-file -t`` this row cannot tell the two apart and the
    release would silently never reach the remote. ``tests/cli/test_git_tag.py`` pins the same
    rule against the double (``git.tag(name, message)`` with ``message == name``).
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    head = git.get_current_commit_id()

    git.tag("tag_message")

    assert read_commit_of(git_repo, "tag_message") == head
    kind = git_repo.run("cat-file", "-t", "tag_message").stdout.strip()
    assert kind == "tag", "annotated tag object; a lightweight tag would report 'commit'"

    # The two-argument form the CLI layer uses (`tests/cli/test_git_tag.py` asserts the call is
    # `git.tag(name, message)`), so the message has to actually reach the tag object.
    git.tag("v2.0.0", "release notes for v2.0.0")
    body = git_repo.run("tag", "-l", "--format=%(contents)", "v2.0.0").stdout.strip()
    assert body == "release notes for v2.0.0"


def test_tag_then_commit_then_tag_resolve_to_their_own_heads(git_repo: GitRepo) -> None:
    """Row 9 (``index.test.ts:198-232``): each tag resolves to the head it was created at."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n', "b.js": 'export default "b"\n'})
    initial_head = git.get_current_commit_id()

    git.tag("tag_message")
    write_file(git_repo.root / "b.js", 'export default "updated b"\n')
    git.add("b.js")
    new_head = git.commit("update b")
    git.tag("new_tag")

    assert read_commit_of(git_repo, "tag_message") == initial_head
    assert read_commit_of(git_repo, "new_tag") == new_head
    assert initial_head != new_head


TAG_EXISTS_CASES = [
    (False, "row 10 (index.test.ts:236-242): `tag -l` prints nothing for an absent tag"),
    (True, "row 11 (index.test.ts:244-252): non-empty output -> True"),
]


@pytest.mark.parametrize("create,why", TAG_EXISTS_CASES)
def test_tag_exists(git_repo: GitRepo, create: bool, why: str) -> None:
    """Rows 10-11 (``index.test.ts:235-253``) -- ``index.ts:301-308``."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    if create:
        git.tag("tag_message")

    assert git.tag_exists("tag_message") is create, why


def test_get_current_commit_id_short_is_a_prefix_of_the_full_sha(git_repo: GitRepo) -> None:
    """molt-native, pins the ``short`` flag of ``index.ts:310-326`` (used by snapshot templates)."""
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})

    full = git.get_current_commit_id()
    short = git.get_current_commit_id(short=True)

    assert len(full) == 40
    assert 0 < len(short) < len(full)
    assert full.startswith(short)


# ======================================================================================
# getCommitsThatAddFiles -- row 12 + the shallow-clone deepening group, rows 13-16
# ======================================================================================


def test_get_commits_that_add_files_returns_the_adding_commit(git_repo: GitRepo) -> None:
    """Row 12 (``index.test.ts:256-267``): ``log --diff-filter=A --max-count=1 --pretty=%H:%p``.

    Divergence 2: a ``dict[path, sha]``, not upstream's positional list (``index.ts:141``).
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    head_sha = git.get_current_commit_id()

    assert git.get_commits_that_add_files(["a.js"]) == {"a.js": head_sha}


@pytest.mark.slow
def test_shallow_clone_is_not_deepened_when_the_commit_is_already_present(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Row 13 (``index.test.ts:293-314``): depth stays 5; no ``fetch --deepen`` at all.

    The clone goes through the fixture's ``file://`` URL: ``git clone --depth`` is silently
    ignored for a plain local path (git hard-links the object store instead), so a path-based
    clone would make every row in this group vacuous.
    """
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    run_dummy_commits(git_repo, 10)
    original_commit = run_commit_file(git_repo, "b.js", 'export default "b"\n')

    clone = git_repo.shallow_clone(tmp_path / "clone", depth=5)

    assert Git(clone.root).get_commits_that_add_files(["b.js"]) == {"b.js": original_commit}
    assert run_commit_count(clone) == 5, "the head commit was already in the clone"


@pytest.mark.slow
def test_shallow_clone_is_deepened_partially_until_the_commit_is_found(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Row 14 (``index.test.ts:316-340``): deepen by 50 until found -- and no further.

    ``index.ts:127-131`` only deepens when ``rev-parse --is-shallow-repository`` says it can, and
    only ``by: 50``. The upper bound is the assertion that matters: ``fetch --unshallow`` would
    also make the lookup succeed, so without ``< original_depth`` this row cannot distinguish
    "deepened enough" from "downloaded the entire history of a large repo" (community pain #517).
    """
    padding = (
        DEEPEN_BY * 2
    ) // 3  # 33 -- deep enough to miss depth 5, shallow enough for one round
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    run_dummy_commits(git_repo, padding)
    original_commit = run_commit_file(git_repo, "b.js", 'export default "b"\n')
    run_dummy_commits(git_repo, padding)

    clone = git_repo.shallow_clone(tmp_path / "clone", depth=5)
    original_depth = run_commit_count(git_repo)

    assert Git(clone.root).get_commits_that_add_files(["b.js"]) == {"b.js": original_commit}

    deepened = run_commit_count(clone)
    assert deepened > 5, "the commit was outside the original window, so it had to deepen"
    assert deepened < original_depth, "but it must NOT have fully unshallowed the clone"
    assert deepened == 5 + DEEPEN_BY, (
        "exactly one round of `fetch --deepen=50` from depth 5; the step size is the fact under "
        "test, and the bounds above are satisfied by any step between 30 and 62"
    )


@pytest.mark.slow
def test_shallow_clone_copes_with_a_parentless_root_commit(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Row 15 (``index.test.ts:342-362``): deepen all the way; cope with a commit with no parent.

    The loop's terminating condition is "no parent AND the repo is no longer shallow"
    (``index.ts:131-137``); a root commit genuinely has no parent, so an implementation that
    treats "empty ``%p``" as "must deepen" loops forever once the clone is complete.

    ``git_repo`` seeds its root commit with ``.gitattributes``, which makes it the parent-less
    file-add this row needs -- the reference used the first file of its own ``gitdir()`` seed.
    """
    git = Git(git_repo.root)
    root_commit = git.get_current_commit_id()  # HEAD is still the fixture's root commit
    run_dummy_commits(git_repo, DEEPEN_BY + 10)  # two deepening rounds from depth 5

    clone = git_repo.shallow_clone(tmp_path / "clone", depth=5)

    found = Git(clone.root).get_commits_that_add_files([".gitattributes"])
    assert found == {".gitattributes": root_commit}
    assert run_commit_count(clone) == run_commit_count(git_repo), "fully deepened, as required"


@pytest.mark.slow
def test_get_commits_that_add_files_keeps_order_and_omits_missing_paths(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Row 16 (``index.test.ts:364-389``): upstream returns ``[sha1, undefined, sha2]``.

    Divergence 2 in full: molt returns ``{"b.js": sha1, "c.js": sha2}`` -- the missing path is
    **absent** rather than a ``None`` hole, and insertion order still records which requested path
    each sha belongs to. Both halves are asserted, because a list-returning implementation would
    satisfy neither and a set/dict-comprehension that lost order would satisfy only the first.
    """
    padding = 20
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    run_dummy_commits(git_repo, padding)
    first_commit = run_commit_file(git_repo, "b.js", 'export default "b"\n')
    run_dummy_commits(git_repo, padding)
    second_commit = run_commit_file(git_repo, "c.js", 'export default "c"\n')

    clone = git_repo.shallow_clone(tmp_path / "clone", depth=5)
    found = Git(clone.root).get_commits_that_add_files(["b.js", "this-file-does-not-exist", "c.js"])

    assert found == {"b.js": first_commit, "c.js": second_commit}
    assert list(found) == ["b.js", "c.js"], "requested order is preserved"
    assert "this-file-does-not-exist" not in found


# ======================================================================================
# getChangedFilesSince -- rows 17-20
# ======================================================================================


NO_CHANGE_CASES = [
    (False, "row 17 (index.test.ts:394-407): fullPath false -> []"),
    (True, "row 18 (index.test.ts:409-422): fullPath true -> [] (not [repo_root])"),
]


@pytest.mark.parametrize("full_path,why", NO_CHANGE_CASES)
def test_get_changed_files_since_is_empty_without_changes(
    git_repo: GitRepo, full_path: bool, why: str
) -> None:
    """Rows 17-18 -- ``index.ts:224-232``.

    Row 18 is not a duplicate of row 17: the ``full_path`` branch maps every line through a path
    resolve, and an implementation that forgot to drop git's trailing empty line would return
    ``[str(repo_root)]`` here while row 17 still passed.
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    head = git.get_current_commit_id()

    assert git.get_changed_files_since(head, full_path=full_path) == [], why


def test_get_changed_files_since_lists_committed_files(git_repo: GitRepo) -> None:
    """Row 19 (``index.test.ts:424-453``): since-first -> b.js + d.js, since-second -> d.js."""
    git = Git(git_repo.root)
    run_seed(
        git_repo,
        {
            "a.js": 'export default "a"\n',
            "b.js": 'export default "b"\n',
            "c.js": 'export default "c"\n',
            "d.js": 'export default "d"\n',
        },
    )

    first_ref = git.get_current_commit_id()
    write_file(git_repo.root / "b.js", 'export default "updated b"\n')
    git.add("b.js")
    git.commit("update b.js")

    second_ref = git.get_current_commit_id()
    write_file(git_repo.root / "d.js", 'export default "updated d"\n')
    git.add("d.js")
    git.commit("update d.js")

    assert git.get_changed_files_since(first_ref) == ["b.js", "d.js"]
    assert git.get_changed_files_since(second_ref) == ["d.js"]


def test_get_changed_files_since_includes_uncommitted_worktree_changes(
    git_repo: GitRepo,
) -> None:
    """molt-native, pinning the diff form ``index.ts:213-217`` chose.

    ``git diff --name-only <divergedAt>`` compares the **working tree** to a commit, not
    ``HEAD``. Upstream relies on this without saying so -- row 22 (``index.test.ts:555-571``)
    edits two manifests, never stages them, and commits with ``--allow-empty``, so the only reason
    it sees the edits at all is this behavior. ``git diff --cached`` or ``git diff <ref> HEAD``
    would return nothing here.
    """
    git = Git(git_repo.root)
    run_seed(git_repo, {"a.js": 'export default "a"\n', "b.js": 'export default "b"\n'})
    ref = git.get_current_commit_id()

    write_file(git_repo.root / "b.js", 'export default "updated b"\n')  # not staged, not committed

    assert git.get_changed_files_since(ref) == ["b.js"]


def test_get_changed_files_since_diffs_the_merge_base_not_the_ref_tip(git_repo: GitRepo) -> None:
    """molt-native, pinning the ``merge-base`` step of ``index.ts:211``.

    With main advanced past the branch point, ``git diff main`` reports main-only work as a
    difference of the feature branch. Only ``git diff $(merge-base main HEAD)`` answers "what did
    THIS branch change", which is what every caller (``version``, ``status``, ``add --since``)
    means. Nothing in the ported rows distinguishes the two, because they all use a linear history.
    """
    git = Git(git_repo.root)
    run_seed(
        git_repo,
        {
            "a.js": 'export default "a"\n',
            "b.js": 'export default "b"\n',
            "c.js": 'export default "c"\n',
        },
    )

    git_repo.run("checkout", "-b", "feature")
    write_file(git_repo.root / "b.js", 'export default "feature b"\n')
    git.add("b.js")
    git.commit("feature: update b")

    git_repo.run("checkout", "main")
    write_file(git_repo.root / "c.js", 'export default "main c"\n')
    git.add("c.js")
    git.commit("main: update c")

    git_repo.run("checkout", "feature")

    assert git.get_changed_files_since("main") == ["b.js"], (
        "c.js changed on main after the branch point and is not this branch's work"
    )


FULL_PATH_CWD_CASES = [
    ("", False, "row 20 (index.test.ts:454-496): from the repo root"),
    ("packages", False, "row 20: from packages/ -- same answer, different cwd"),
    (
        "",
        True,
        "molt-native: diff.relative=true must not change the answer at the repo root",
    ),
    (
        "packages",
        True,
        "molt-native: --no-relative must override an explicit diff.relative=true from a subdir",
    ),
]


@pytest.mark.parametrize("subdir,diff_relative,why", FULL_PATH_CWD_CASES)
def test_get_changed_files_since_full_paths_are_cwd_independent(
    git_repo: GitRepo, subdir: str, diff_relative: bool, why: str
) -> None:
    """Row 20 (``index.test.ts:454-496``) -- the Windows path-resolution row.

    Two independent behaviors have to hold at once (``index.ts:214`` + ``index.ts:231-232``):

    * ``--no-relative`` forces git to print repo-root-relative paths whatever the cwd is. Without
      it, the answer silently depends on where the process happens to be -- and the two
      ``diff_relative=True`` rows are the only ones that can prove the flag is really there,
      because ``diff.relative`` defaults to false, so dropping ``--no-relative`` is invisible in
      the ported row 20 alone.
    * the resolve base is the **repo root** (``rev-parse --show-cdup``), not the cwd. Resolving
      against the cwd yields ``<root>/packages/packages/pkg-b/b.js`` from the subdirectory.

    The native-form assertion is the Windows-specific half: git always emits forward slashes, so a
    full path that was never round-tripped through ``pathlib``/``os.path`` still contains ``/``
    and ``str(Path(p)) != p`` on Windows.
    """
    git_root = Git(git_repo.root)
    run_seed(
        git_repo,
        {
            "packages/pkg-a/a.js": 'export default "a"\n',
            "packages/pkg-b/b.js": 'export default "b"\n',
            "packages/pkg-c/c.js": 'export default "c"\n',
        },
    )
    ref = git_root.get_current_commit_id()

    write_file(git_repo.root / "packages" / "pkg-b" / "b.js", 'export default "updated b"\n')
    git_root.add("packages/pkg-b/b.js")
    git_root.commit("update b.js")
    write_file(git_repo.root / "packages" / "pkg-c" / "c.js", 'export default "updated c"\n')
    git_root.add("packages/pkg-c/c.js")
    git_root.commit("update c.js")

    if diff_relative:
        git_repo.run("config", "diff.relative", "true")

    cwd = git_repo.root / subdir if subdir else git_repo.root
    changed = Git(cwd).get_changed_files_since(ref, full_path=True)

    expected = [
        (git_repo.root / "packages" / "pkg-b" / "b.js").resolve(),
        (git_repo.root / "packages" / "pkg-c" / "c.js").resolve(),
    ]
    assert [Path(p) for p in changed] == expected, why
    assert all(Path(p).is_absolute() for p in changed)
    assert all(str(Path(p)) == p for p in changed), (
        "full paths come back in native form (os.sep), not git's forward slashes"
    )


# ======================================================================================
# getChangedPackagesSinceRef -- rows 21-26 (Adapt: package.json/workspaces -> uv workspace)
# ======================================================================================


def test_get_changed_packages_since_ref_is_empty_when_nothing_changed(git_repo: GitRepo) -> None:
    """Row 21 (``index.test.ts:500-528``): changed on main, then branched -> no diff vs main."""
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"}, commit=False)
    run_seed(git_repo, {"packages/pkg-a/a.js": 'export default "a"\n'})

    write_file(git_repo.root / "packages" / "pkg-a" / "a.js", 'export default "updated a"\n')
    git.add("packages/pkg-a/a.js")
    git.commit("update a.js")

    git_repo.run("checkout", "-b", "new-branch")

    assert git.get_changed_packages_since_ref("main") == []


def test_get_changed_packages_since_ref_finds_packages_changed_on_a_branch(
    git_repo: GitRepo,
) -> None:
    """Row 22 (``index.test.ts:530-582``): editing pkg-b and pkg-d manifests -> those two.

    Order is asserted set-wise on purpose. Upstream's order is an artefact of sorting packages by
    directory-name length (``index.ts:280``) for the closest-package rule; the four dirs here are
    the same length, so the order the reference asserts is really ``getPackages`` discovery order
    and pinning it would over-constrain the port.
    """
    git = Git(git_repo.root)
    build_workspace(
        git_repo,
        ["packages/*"],
        {f"packages/pkg-{s}": f"pkg-{s}" for s in ("a", "b", "c", "d")},
    )

    git_repo.run("checkout", "-b", "new-branch")
    for suffix in ("b", "d"):
        write_file(
            git_repo.root / "packages" / f"pkg-{suffix}" / "pyproject.toml",
            MEMBER_PYPROJECT.format(name=f"pkg-{suffix}").replace("1.0.0", "1.0.1"),
        )
        git.add(f"packages/pkg-{suffix}/pyproject.toml")
        git.commit(f"update pkg-{suffix}")

    assert sorted(git.get_changed_packages_since_ref("main")) == ["pkg-b", "pkg-d"]


def test_get_changed_packages_since_ref_reads_the_name_from_the_manifest(
    git_repo: GitRepo,
) -> None:
    """Adapt detail behind rows 21-26: the name is ``[project].name``, not the directory name.

    Upstream reads ``pkg.packageJson.name`` (``index.test.ts:578``); the Python analogue is
    ``[project].name`` in the member ``pyproject.toml`` (test-suite doc 08, row 22 note). Every
    other row uses a directory whose name matches its project name, so only this one can tell an
    implementation that shortcut to ``dir.name`` apart from one that read the manifest.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/some-dir": "renamed-package"})

    git_repo.run("checkout", "-b", "new-branch")
    write_file(git_repo.root / "packages" / "some-dir" / "src.py", "x = 1\n")
    git.add("packages/some-dir/src.py")
    git.commit("add source")

    assert git.get_changed_packages_since_ref("main") == ["renamed-package"]


CLOSEST_PACKAGE_CASES = [
    (
        ["packages/*", "packages/*/examples/*"],
        "row 23 (index.test.ts:584-621): shorter workspace pattern listed first",
    ),
    (
        ["packages/*/examples/*", "packages/*"],
        "row 24 (index.test.ts:623-660): longer workspace pattern listed first",
    ),
]


@pytest.mark.parametrize("members,why", CLOSEST_PACKAGE_CASES)
def test_get_changed_packages_since_ref_picks_the_closest_package(
    git_repo: GitRepo, members: list[str], why: str
) -> None:
    """Rows 23-24 (``index.test.ts:584-660``): the nested package wins, whatever the glob order.

    ``index.ts:280`` sorts packages by **directory length, longest first**, and each package
    *consumes* the changed files it claims (``index.ts:284-291``), so the deepest enclosing
    package takes the file and its parent is left with nothing. Rows 23 and 24 are byte-identical
    apart from the member order, which is exactly what makes them a pair: an implementation that
    iterated in workspace-glob (or filesystem-discovery) order would return ``pkg-a`` for one of
    them and pass the other.
    """
    git = Git(git_repo.root)
    build_workspace(
        git_repo,
        members,
        {
            "packages/pkg-a": "pkg-a",
            "packages/pkg-a/examples/example-a": "example-a",
        },
    )

    git_repo.run("checkout", "-b", "new-branch")
    new_file = "packages/pkg-a/examples/example-a/file.py"
    write_file(git_repo.root / new_file, "print('hello world')\n")
    git.add(new_file)
    git.commit("new file in the example")

    assert git.get_changed_packages_since_ref("main") == ["example-a"], why


CHANGED_FILE_PATTERN_CASES = [
    (
        "packages/pkg-a/__tests__/file.py",
        [],
        "row 25 (index.test.ts:662-692): a test file does not match `src/**`",
    ),
    (
        "packages/pkg-a/src/index.py",
        ["pkg-a"],
        "row 26 (index.test.ts:694-729): `src/index.py` matches `src/**`",
    ),
]


@pytest.mark.parametrize("changed_file,expected,why", CHANGED_FILE_PATTERN_CASES)
def test_get_changed_packages_since_ref_honours_changed_file_patterns(
    git_repo: GitRepo, changed_file: str, expected: list[str], why: str
) -> None:
    """Rows 25-26 (``index.test.ts:662-729``) -- ``index.ts:294-297`` + ``globMatchSome``.

    Patterns are matched against each changed file's path **relative to its own package**, so the
    pattern is ``src/**`` and not ``packages/pkg-a/src/**``.

    Row 26 is a genuine Windows row: the relative path is derived from resolved absolute paths,
    so on Windows it reads ``src\\\\index.py`` and would never match the POSIX-shaped glob.
    Upstream normalizes separators before matching for exactly this reason
    (``index.ts:349-351``); an implementation that skips that step passes on Linux, fails here.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})

    git_repo.run("checkout", "-b", "new-branch")
    write_file(git_repo.root / changed_file, "ANSWER = 42\n")
    git.add(changed_file)
    git.commit("new file")

    changed = git.get_changed_packages_since_ref("main", changed_file_patterns=["src/**"])
    assert changed == expected, why


def test_get_changed_packages_since_ref_defaults_to_matching_every_file(
    git_repo: GitRepo,
) -> None:
    """The ``changedFilePatterns = ["**"]`` default of ``index.ts:271``.

    Guards rows 25-26 against a filter that only appears to work because the parameter is being
    ignored: with the default, the very file row 25 rejects must be accepted.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})

    git_repo.run("checkout", "-b", "new-branch")
    write_file(git_repo.root / "packages/pkg-a/__tests__/file.py", "ANSWER = 42\n")
    git.add("packages/pkg-a/__tests__/file.py")
    git.commit("new test file")

    assert git.get_changed_packages_since_ref("main") == ["pkg-a"]


# ======================================================================================
# getChangedChangesetFilesSinceRef -- rows 27-30
# ======================================================================================


def test_get_changed_changeset_files_since_ref_is_empty_without_changesets(
    git_repo: GitRepo,
) -> None:
    """Row 27 (``index.test.ts:733-751``): nothing added -> ``[]``."""
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})

    assert git.get_changed_changeset_files_since_ref("main") == []


def test_get_changed_changeset_files_since_ref_swallows_a_git_error(git_repo: GitRepo) -> None:
    """Row 27's note: the whole body is wrapped in ``try { } catch (GitError) { return [] }``.

    ``index.ts:243-265``. The failure this actually absorbs is ``getDivergedCommit`` throwing on a
    ref that does not exist locally -- a CI checkout without the base branch fetched. Asserted
    separately from row 27 because row 27's ``ref`` resolves fine and never enters the catch, so
    row 27 alone cannot tell a wrapped implementation from an unwrapped one.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})
    write_changeset_file(git_repo)

    assert git.get_changed_changeset_files_since_ref("origin/no-such-base") == []


def test_get_changed_changeset_files_since_ref_returns_repo_relative_paths(
    git_repo: GitRepo,
) -> None:
    """Row 28 (``index.test.ts:753-785``): ``.changeset/<id>.md``, repo-root-relative, POSIX.

    ``index.ts:248`` uses ``--diff-filter=d`` (lowercase: exclude deletions) and the regex
    ``/.changeset\\/[^/]+\\.md$/`` (``index.ts:254``). Ported verbatim, unescaped leading dot and
    all -- it is a wildcard upstream too, and tightening it is a behavior change, not a typo fix.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})
    changeset_id = write_changeset_file(git_repo)

    files = git.get_changed_changeset_files_since_ref("main")

    assert files == [f".changeset/{changeset_id}.md"]


def test_get_changed_changeset_files_since_ref_ignores_deleted_changesets(
    git_repo: GitRepo,
) -> None:
    """molt-native, guarding the ``--diff-filter=d`` of ``index.ts:248``.

    Lowercase ``d`` means *exclude* deletions. It matters because ``molt version`` consumes
    changesets by deleting them: on a release branch every applied changeset shows up in the diff
    as a deletion, and without the filter the GitHub Action would re-announce releases that
    already happened. No upstream row covers it -- rows 27-30 only ever add files -- so dropping
    the flag is invisible to the port.
    """
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})
    write_changeset_file(git_repo, "already-applied")
    git_repo.commit("chore: add a changeset")

    git_repo.run("checkout", "-b", "release")
    (git_repo.root / ".changeset" / "already-applied.md").unlink()
    git_repo.run("add", "-A")

    assert git.get_changed_changeset_files_since_ref("main") == []


def test_get_changed_changeset_files_since_ref_works_on_a_non_base_ref(
    git_repo: GitRepo,
) -> None:
    """Row 29 (``index.test.ts:786-822``): the ref may be the feature branch itself."""
    git = Git(git_repo.root)
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})

    git_repo.run("checkout", "-b", "some-branch")
    changeset_id = write_changeset_file(git_repo)

    assert git.get_changed_changeset_files_since_ref("some-branch") == [
        f".changeset/{changeset_id}.md"
    ]


def test_get_changed_changeset_files_since_ref_overrides_diff_relative(
    git_repo: GitRepo,
) -> None:
    """Row 30 (``index.test.ts:824-860``) -- the explicit git-config override row.

    ``diff.relative=true`` is set in the repo config and the wrapper is run with its cwd **inside**
    ``.changeset``. Without the ``--no-relative`` of ``index.ts:248`` git prints ``<id>.md``, the
    ``.changeset/...`` regex rejects it, and the function quietly returns ``[]`` -- a user with
    that one config line set would see ``molt status`` report no changesets. This is the only row
    in the file that can catch a dropped ``--no-relative`` on this function.
    """
    build_workspace(git_repo, ["packages/*"], {"packages/pkg-a": "pkg-a"})
    git_repo.run("config", "diff.relative", "true")
    changeset_id = write_changeset_file(git_repo)

    git = Git(git_repo.root / ".changeset")
    files = git.get_changed_changeset_files_since_ref("main")

    assert files == [f".changeset/{changeset_id}.md"]
    assert all("\\" not in f for f in files), (
        "repo-relative changeset paths stay POSIX-shaped -- the regex depends on `/`"
    )


# ======================================================================================
# Environment sanity -- keeps every row above honest
# ======================================================================================


def test_the_real_git_binary_is_available() -> None:
    """If ``git`` were missing, every row above would error rather than silently pass -- but this
    names the cause in one line instead of thirty stack traces."""
    proc = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False, encoding="utf-8"
    )
    assert proc.returncode == 0, "these tests require a real git binary on PATH"
    assert proc.stdout.startswith("git version")


def test_shallow_clone_fixture_really_shallows(git_repo: GitRepo, tmp_path: Path) -> None:
    """Guard for rows 13-16: prove the fixture's clone is shallow before relying on it.

    ``git clone --depth`` is ignored for a local *path* (git hard-links the object store), so the
    frozen fixture clones from ``file_url()``. If that ever regressed, rows 13-16 would still pass
    -- against a complete clone that never needed deepening -- and the deepening loop would be
    untested. ``tests/conftest.py`` is frozen, so this asserts the property rather than fixing it.
    """
    run_seed(git_repo, {"a.js": 'export default "a"\n'})
    run_dummy_commits(git_repo, 10)

    clone = git_repo.shallow_clone(tmp_path / "clone", depth=5)

    assert clone.run("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert run_commit_count(clone) == 5 < run_commit_count(git_repo)
    assert git_repo.file_url().startswith("file:"), "a path clone would ignore --depth"
