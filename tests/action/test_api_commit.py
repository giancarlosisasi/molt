r"""Conformance tests for ``collect_file_changes`` -- a working tree read as an API commit.

Port source
-----------
``@changesets/ghcommit`` ``src/git.ts::getFileChanges``, fetched and read 2026-08-01: the helper
``changesets/action`` v1.9.0 reaches through when its ``commitMode`` is the host API. It runs
``git diff --name-status <ref>`` plus ``git ls-files --others --exclude-standard``, base64-encodes
each addition's contents, sorts both lists by path, and refuses a symbolic link and an executable
file with a named error.

What molt changes, and why these rows must FAIL against a literal port
----------------------------------------------------------------------
1. **``-z`` on both reads.** Upstream parses git's default output, which applies ``core.quotePath``
   -- so a path holding a space or a byte outside ASCII arrives as its own escaped rendering and
   would be committed under that spelling. This is the Windows-and-internationalization
   correctness molt claims as a differentiator, and it costs one flag.
2. **``--no-renames`` instead of ``--diff-filter=ACDMRT`` plus rename handling.** With rename
   detection on, git reports ``R`` and molt would have to split it back into a deletion and an
   addition; with it off, git reports both directly.
3. **A submodule is refused too**, not only a symbolic link and an executable. GitHub's
   ``FileAddition`` has no mode field at all, so a gitlink cannot be expressed either.

The load-bearing row
--------------------
``test_a_change_the_script_committed_itself_is_still_collected`` is the one that fails if the diff
is taken against ``HEAD`` rather than against the base commit. Every other row here passes under
that mutation, because in every other row the working tree is dirty. A project configured with
``commit = true`` runs a version script that commits its own work, and the ``HEAD`` reading would
drop exactly that work out of the release with no error anywhere.

No row reaches a host and no row opens a socket: there is no forge here at all. The git side is the
real binary through the real :class:`molt.git.Git` seam, on the shared ``git_repo`` fixture.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.action", reason="molt.action.api_commit is this suite's target")

from molt.action.api_commit import collect_file_changes
from molt.errors import MoltError
from molt.git import Git

if TYPE_CHECKING:
    from tests.conftest import GitRepo

pytestmark = [pytest.mark.functional, pytest.mark.git]

#: U+00E9 (LATIN SMALL LETTER E WITH ACUTE) written as an escape, never as the character: this
#: repo's sources are ASCII-only because a cp1252 console corrupts anything else in a pytest
#: failure report. In UTF-8 it is two bytes, which is why ``core.quotePath`` renders it as two
#: octal escapes and why ``-z`` is what keeps it intact.
ACCENTED = "caf\u00e9"


# ======================================================================================
# Helpers (never named test_* -- `python_functions = "test"` is a PREFIX match)
# ======================================================================================


def write(root: Path, relative: str, content: bytes) -> Path:
    """Write ``content`` at ``relative`` as **bytes**, creating parents.

    ``write_bytes`` rather than ``write_text``, matching every other fixture in this suite: text
    mode translates newlines, so a row asserting that CRLF survives would be asserting the
    platform's behaviour rather than molt's.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def seed(repo: GitRepo, files: dict[str, bytes]) -> str:
    """Write ``files``, commit them, and return the sha that commit landed on.

    That sha is the ``base`` every row measures against -- the analogue of the commit a CI checkout
    leaves behind before the version script runs.
    """
    for relative, content in files.items():
        write(repo.root, relative, content)
    repo.run("add", "-A")
    return repo.commit("chore: seed")


def collect(repo: GitRepo, base: str) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """Run the collection over ``repo``'s working tree through the real git seam."""
    return collect_file_changes(Git(repo.root), base=base)


def force_lstat_mode(monkeypatch: pytest.MonkeyPatch, target: Path, mode: int) -> None:
    """Make ``target.lstat()`` report ``mode``, leaving every other path alone.

    Used only where the platform cannot produce the real thing: creating a symbolic link on Windows
    needs Developer Mode or an elevated process, and the executable bit does not exist there at all.
    The alternative was a skipped row, and a row that is green on one platform and absent on another
    is a guarantee nobody can read off the suite -- so the refusal branch is exercised everywhere
    and the *cause* is real wherever the platform allows it.

    The gap this leaves is recorded: on Windows these two rows prove the refusal logic, not that
    molt classifies a real link or a real executable correctly (``openspec/GAPS.md`` ``ACM-6``).
    """
    real_lstat = Path.lstat
    resolved = target.resolve()

    def fake_lstat(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        info = real_lstat(self, *args, **kwargs)
        if self.resolve() == resolved:
            return os.stat_result((mode, *tuple(info)[1:]))
        return info

    monkeypatch.setattr(Path, "lstat", fake_lstat)


def make_symlink(monkeypatch: pytest.MonkeyPatch, root: Path, relative: str) -> str:
    """A symbolic link at ``relative``, real where the platform allows one and simulated where not.

    See :func:`force_lstat_mode` for why this never skips.
    """
    target = root / relative
    try:
        target.symlink_to(root / "keep.txt")
    except (OSError, NotImplementedError):
        write(root, relative, b"keep.txt")
        force_lstat_mode(monkeypatch, target, stat.S_IFLNK | 0o777)
    return relative


def make_executable(monkeypatch: pytest.MonkeyPatch, root: Path, relative: str) -> str:
    """An executable file at ``relative``, real on POSIX and simulated on Windows.

    Windows has no executable bit, which is also why git records ``100644`` there and why molt's
    check and the platform agree. See :func:`force_lstat_mode`.
    """
    path = write(root, relative, b"#!/bin/sh\necho hi\n")
    if os.name == "posix":
        path.chmod(0o755)
    else:
        force_lstat_mode(monkeypatch, path, stat.S_IFREG | 0o755)
    return relative


# ======================================================================================
# Rows 1-6: what a release's working tree becomes
# ======================================================================================


def test_a_modification_an_addition_and_a_deletion_become_the_right_changes(
    git_repo: GitRepo,
) -> None:
    """The three things a version run does to a repository, in one row.

    ``molt version`` rewrites a manifest, writes a changelog for a package that had none, and
    deletes every changeset it consumed. Only the deletion arrives through the diff as a ``D``; the
    brand-new changelog is **untracked** and reaches the commit only because the second read exists
    at all -- which is the half a reader is most likely to leave out, and the half that silently
    drops the first changelog of every new package.
    """
    base = seed(
        git_repo,
        {
            "packages/pkg-a/pyproject.toml": b'[project]\nversion = "1.0.0"\n',
            ".changeset/strange-words-combine.md": b"---\n---\n",
        },
    )
    write(git_repo.root, "packages/pkg-a/pyproject.toml", b'[project]\nversion = "1.1.0"\n')
    write(git_repo.root, "packages/pkg-a/CHANGELOG.md", b"# pkg-a\n\n## 1.1.0\n")
    (git_repo.root / ".changeset" / "strange-words-combine.md").unlink()

    additions, deletions = collect(git_repo, base)

    assert sorted(additions) == [
        "packages/pkg-a/CHANGELOG.md",
        "packages/pkg-a/pyproject.toml",
    ]
    assert additions["packages/pkg-a/pyproject.toml"] == b'[project]\nversion = "1.1.0"\n'
    assert deletions == (".changeset/strange-words-combine.md",)


def test_a_file_inside_a_new_directory_is_listed_individually(git_repo: GitRepo) -> None:
    """A brand-new package is a directory of files, and the commit API takes files, not trees.

    ``git ls-files --others`` reports each file rather than the directory unless ``--directory`` is
    passed, and passing it would send the host a path that is not a file -- which GitHub answers
    with a validation failure rather than by walking the tree.
    """
    base = seed(git_repo, {"README.md": b"# repo\n"})
    write(git_repo.root, "packages/pkg-new/pyproject.toml", b"[project]\n")
    write(git_repo.root, "packages/pkg-new/src/pkg_new/__init__.py", b"")

    additions, deletions = collect(git_repo, base)

    assert sorted(additions) == [
        "packages/pkg-new/pyproject.toml",
        "packages/pkg-new/src/pkg_new/__init__.py",
    ]
    assert deletions == ()


def test_an_ignored_file_is_not_committed(git_repo: GitRepo) -> None:
    """``--exclude-standard`` is what keeps a release commit from carrying a build directory.

    Without it every ignored path becomes an addition, so a real project would try to send its
    ``.venv/`` and ``dist/`` through an API that base64-encodes each file into one request. The
    failure would not be subtle, but it would only appear on somebody's real repository.
    """
    base = seed(git_repo, {".gitignore": b"dist/\n*.log\n"})
    write(git_repo.root, "dist/pkg-a-1.0.0.tar.gz", b"\x1f\x8b")
    write(git_repo.root, "build.log", b"noise\n")
    write(git_repo.root, "CHANGELOG.md", b"# changes\n")

    additions, _deletions = collect(git_repo, base)

    assert sorted(additions) == ["CHANGELOG.md"]


def test_a_path_with_a_space_and_a_non_ascii_character_survives_both_reads(
    git_repo: GitRepo,
) -> None:
    """The ``-z`` guarantee, end to end.

    Without it git prints ``"caf\\303\\251 note.md"`` -- quotes and octal escapes included -- and
    molt would send *that string* to the host as the path to commit. The tracked file exercises the
    diff read and the untracked one exercises the listing, because they are two different git
    commands and a dropped flag on either produces the same broken commit.
    """
    tracked = f"docs/{ACCENTED} note.md"
    untracked = f"docs/new {ACCENTED}.md"
    base = seed(git_repo, {tracked: b"one\n"})
    write(git_repo.root, tracked, b"one changed\n")
    write(git_repo.root, untracked, b"two\n")

    additions, _deletions = collect(git_repo, base)

    assert sorted(additions) == sorted([tracked, untracked])
    assert additions[tracked] == b"one changed\n"
    assert all("\\" not in path for path in additions), "paths stay POSIX on every platform"


def test_a_rename_becomes_a_deletion_of_the_old_path_and_an_addition_of_the_new(
    git_repo: GitRepo,
) -> None:
    """``--no-renames`` -- the commit API has no "rename" operation, so molt must not receive one.

    With git's rename detection on (its default for ``git diff`` since 2.9) the same change arrives
    as a single ``R100`` entry naming two paths, which molt would have to split back apart. Turning
    detection off asks git for the shape the host actually takes, and removes any dependence on
    git's similarity threshold -- a renamed *and* edited file would otherwise cross it or not
    depending on how much was edited.
    """
    base = seed(git_repo, {"docs/old-name.md": b"# same contents everywhere\n"})
    (git_repo.root / "docs" / "old-name.md").rename(git_repo.root / "docs" / "new-name.md")
    git_repo.run("add", "-A")

    additions, deletions = collect(git_repo, base)

    assert sorted(additions) == ["docs/new-name.md"]
    assert deletions == ("docs/old-name.md",)


def test_contents_cross_as_bytes_so_crlf_and_non_utf8_survive(git_repo: GitRepo) -> None:
    """Design D3 -- ``read_bytes``, never ``read_text``.

    Two failures in one row, and the quieter one is worse. A file that is not valid UTF-8 would
    **raise** on any path that decoded it, which at least fails loudly; CRLF would be *translated*,
    so the release would succeed and the committed file would differ from the one on the runner's
    disk. A lockfile or a vendored fixture is exactly where both live.
    """
    base = seed(git_repo, {"README.md": b"# repo\n"})
    write(git_repo.root, "fixtures/latin1.bin", b"\xff\xfe\x00\x01\x80")
    write(git_repo.root, "fixtures/windows.txt", b"first\r\nsecond\r\n")

    additions, _deletions = collect(git_repo, base)

    assert additions["fixtures/latin1.bin"] == b"\xff\xfe\x00\x01\x80"
    assert additions["fixtures/windows.txt"] == b"first\r\nsecond\r\n"


# ======================================================================================
# Rows 7-8: what the host cannot represent is refused by name
# ======================================================================================


@pytest.mark.parametrize(
    ("kind", "relative"),
    [
        pytest.param("symlink", "link-to-keep", id="symbolic-link"),
        pytest.param("executable", "scripts/tool", id="executable-file"),
    ],
)
def test_a_file_the_commit_api_cannot_represent_is_refused_by_name(
    kind: str, relative: str, git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ported refusals: GitHub's ``FileAddition`` has no file-mode field at all.

    Both would be committed *silently* as an ordinary ``100644`` file: an executable would lose its
    bit and a symbolic link would become a regular file holding its target path. A release that
    quietly broke a project's entry-point script is the kind of failure nobody traces back to the
    release tool, so molt names the path and says what to do instead.

    The refusal is asserted to name the path, not merely to raise: "molt cannot commit this tree" is
    not an actionable sentence in a monorepo of thirty packages.
    """
    base = seed(git_repo, {"keep.txt": b"keep\n"})
    if kind == "symlink":
        make_symlink(monkeypatch, git_repo.root, relative)
    else:
        make_executable(monkeypatch, git_repo.root, relative)

    with pytest.raises(MoltError) as excinfo:
        collect(git_repo, base)

    message = str(excinfo.value)
    assert relative in message, message
    assert ".gitignore" in message, "the refusal says what to do about it"


class StubGit:
    """A git seam that reports a fixed answer, over a real directory.

    :func:`collect_file_changes` reads the seam structurally -- ``root``, ``diff_name_status`` and
    ``list_untracked`` -- so a stub is enough, and for the submodule case it is the only practical
    harness: git does **not** list an unregistered nested repository at all, and registering a real
    submodule needs a second repository plus ``protocol.file.allow=always`` to clone it over
    ``file://``. The status letter and path below are exactly what a real changed gitlink produces.
    """

    def __init__(self, root: Path, entries: list[tuple[str, str]]) -> None:
        self.root = root
        self._entries = entries

    def diff_name_status(self, ref: str) -> list[tuple[str, str]]:
        return list(self._entries)

    def list_untracked(self) -> list[str]:
        return []


def test_a_submodule_is_refused_by_name(tmp_path: Path) -> None:
    """A gitlink cannot be expressed by the commit API in any form, so it is named and refused.

    A submodule appears in ``--name-status`` as an ordinary changed path whose working-tree entry is
    a **directory**. Reading it as a file would fail with a bare ``IsADirectoryError``; sending its
    contents is not possible at all. Refusing by name is the only honest answer, and it is the one
    ``@changesets/ghcommit`` gives.
    """
    (tmp_path / "vendor" / "library").mkdir(parents=True)

    with pytest.raises(MoltError) as excinfo:
        collect_file_changes(StubGit(tmp_path, [("M", "vendor/library")]), base="0" * 40)

    assert "vendor/library" in str(excinfo.value)
    assert "submodule" in str(excinfo.value)


# ======================================================================================
# Row 9: the base commit, not HEAD -- the single most important row in this file
# ======================================================================================


def test_a_change_the_script_committed_itself_is_still_collected(git_repo: GitRepo) -> None:
    """Design D7 -- the comparison is the working tree against **base**, never against ``HEAD``.

    A project that sets ``commit = true`` runs a version script that makes its own local commit
    before the phase finishes. After that commit the working tree is clean, so a diff against
    ``HEAD`` reports **nothing** -- and the release's version bumps and changelog would simply be
    absent from the pull request, with no error anywhere and a green workflow.

    This is the only row in the file that discriminates the two readings: every other one leaves the
    tree dirty, where the two answers agree. Upstream's own docstring is the same warning: *"If
    previous commits have been made locally and not pushed, you need to set base to the last commit
    that is known to be in the remote repository."*
    """
    base = seed(git_repo, {"packages/pkg-a/pyproject.toml": b'[project]\nversion = "1.0.0"\n'})
    # What the version script does when the project configures its own commit step.
    write(git_repo.root, "packages/pkg-a/pyproject.toml", b'[project]\nversion = "1.1.0"\n')
    write(git_repo.root, "packages/pkg-a/CHANGELOG.md", b"# pkg-a\n\n## 1.1.0\n")
    git_repo.run("add", "-A")
    git_repo.commit("chore: version packages")
    assert git_repo.run("status", "--porcelain").stdout.strip() == "", "the tree is clean now"

    additions, deletions = collect(git_repo, base)

    assert sorted(additions) == [
        "packages/pkg-a/CHANGELOG.md",
        "packages/pkg-a/pyproject.toml",
    ]
    assert additions["packages/pkg-a/pyproject.toml"] == b'[project]\nversion = "1.1.0"\n'
    assert deletions == ()
