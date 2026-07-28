"""Reading a ``.changeset`` directory into an ordered list of changesets.

Ports ``packages/read/src/index.ts:25-65``; the semantics, including the inclusion/exclusion table,
are written up in research doc 02 section 7.

Two divergences, both deliberate:

- **The result is sorted.** Upstream returns raw ``readdir`` order, which is hash order on ext4 and
  case-insensitive alphabetical on NTFS. That order is observable -- it reaches the release plan and
  then the changelog -- so leaving it to the filesystem produces diffs that churn between machines.
  molt sorts on the changeset **id**, with Python's codepoint comparison rather than any
  locale-aware collation (doc 02 section 12.6).
- **The id strips only the final ``.md``.** Upstream computes ``file.replace(".md", "")``, and JS's
  single-argument ``replace`` removes only the first occurrence, so it gets ``some.md.md`` right by
  luck. The naive Python transcription -- ``str.replace`` -- removes *every* occurrence and silently
  yields ``some``. The rule is one ``removesuffix(".md")`` (design D4).

An **empty** directory and a **missing** one are different outcomes and, deliberately, different
code paths (design D5). "No pending changesets" is a normal state ``status`` reports calmly; "there
is no ``.changeset`` directory" means the project was never initialized and the fix is ``molt
init``. Collapsing them would have ``status`` report a healthy repo for an uninitialized one.

A changeset that fails to parse **fails the whole read** (design D6). Skipping it would drop a
release the author intended, which is a wrong version number in the one direction users cannot
notice.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from molt.changeset.parse import Changeset, parse_changeset
from molt.errors import GitError, MoltError

__all__ = ["CHANGESET_DIR", "IGNORED_MD_FILES", "read_changesets"]

#: Where changesets live, relative to the directory handed to :func:`read_changesets`.
CHANGESET_DIR = ".changeset"

#: Markdown files that share the directory but are not changesets (``read/src/index.ts:9``), held
#: lowercased because the membership test folds case.
#:
#: Upstream matches ``README.md`` case-insensitively and the other three with an exact string
#: compare, so a ``Claude.md`` is read as a changeset and blows up the whole directory. Doc 02
#: section 12.6 calls that gotcha 14 and asks for all four to fold case; molt does. The
#: **extension** check stays case-sensitive, matching upstream's ``endsWith(".md")``.
IGNORED_MD_FILES = frozenset({"readme.md", "agents.md", "claude.md", "gemini.md"})

#: What ``molt init`` fixes. The first sentence is ported verbatim (``read/src/index.ts:35``)
#: because users search for it and the action's error handling keys off it; the second is molt's
#: addition, since a friendly error that does not say what to do next is only half of one.
_MISSING_DIR_MESSAGE = (
    "There is no .changeset directory in this project. Run `molt init` to create one."
)

#: Repo-relative paths a ``git diff`` may report for a changeset. The leading dot is escaped, unlike
#: upstream's ``/.changeset\/[^/]+\.md$/`` where it is a wildcard; nesting still matches, so
#: ``library/.changeset/x.md`` is found.
_CHANGESET_PATH_RE = re.compile(r"\.changeset/[^/]+\.md$")


def read_changesets(root_dir: Path | str, *, since_ref: str | None = None) -> list[Changeset]:
    """Read ``<root_dir>/.changeset`` and return its changesets, sorted by id.

    ``root_dir`` is the directory that owns the changeset directory, **not** the git root: a
    monorepo nested inside a larger repository keeps its changesets next to itself, and
    ``read_changesets(repo / "library")`` reads ``repo/library/.changeset``.

    ``since_ref`` restricts the result to changesets whose file changed since that ref, which is how
    ``molt status --since main`` answers "what is new on this branch". Staged-but-uncommitted files
    count, so it works on a dirty tree.
    """
    base = Path(root_dir) / CHANGESET_DIR
    if not base.is_dir():
        raise MoltError(_MISSING_DIR_MESSAGE)

    # Directories are excluded here rather than left to blow up on read: upstream's `readdir` has no
    # file-type filter, so a subdirectory named `foo.md` passes its name filter and then fails with
    # a bare EISDIR (doc 02 section 7.1).
    filenames = [entry.name for entry in base.iterdir() if entry.is_file()]
    if since_ref is not None:
        filenames = _changed_since_ref(filenames, base, since_ref)

    # Sorted on the id, not the filename: the two orders differ (`a-b.md` sorts before `a.md`, while
    # `a` sorts before `a-b`), and the id is what downstream sees.
    ordered = sorted((name.removesuffix(".md"), name) for name in filenames if _is_changeset(name))
    return [
        parse_changeset(_read_text(base / name), id=changeset_id, path=str(base / name))
        for changeset_id, name in ordered
    ]


def _is_changeset(filename: str) -> bool:
    """Whether a directory entry is a changeset document (``read/src/index.ts:52``).

    The leading-dot rule is not incidental: renaming a changeset to ``.<name>.md`` to park it is a
    real, documented feature (doc 02 section 12.6), not merely dotfile hygiene.
    """
    return (
        not filename.startswith(".")
        and filename.endswith(".md")
        and filename.lower() not in IGNORED_MD_FILES
    )


def _read_text(path: Path) -> str:
    """Read a changeset as text without newline translation.

    ``Path.read_text`` opens with ``newline=None``, which rewrites CRLF on the way in and would hide
    from every test whether the parser actually handles it. ``utf-8-sig`` tolerates a byte-order
    mark, which Windows editors add and molt itself never writes.
    """
    return path.read_bytes().decode("utf-8-sig")


def _changed_since_ref(filenames: list[str], base: Path, ref: str) -> list[str]:
    """Keep only the changesets whose file changed since ``ref`` (``read/src/index.ts:11-23``).

    **This is a placeholder for ``molt.git``.** The git wrapper is a later build step and will own
    ``get_changed_changeset_files_since_ref``; when it lands this function becomes a call into it.
    Until then the two commands are inlined here rather than blocking the changeset slice on them.

    One divergence, deliberate: upstream swallows a git failure and returns "no changesets", so a
    typo in ``--since`` silently reports nothing to release. That is the same silent-drop hazard
    design D6 rejects for an unparseable changeset, one scope larger, so it raises instead.
    """
    merge_base = _git(base, "merge-base", ref, "HEAD").strip()
    changed = _git(base, "diff", "--name-only", "--diff-filter=d", "--no-relative", merge_base)
    touched = {
        line.rpartition("/")[2]
        for line in (raw.strip() for raw in changed.splitlines())
        if _CHANGESET_PATH_RE.search(line)
    }
    return [name for name in filenames if name in touched]


def _git(cwd: Path, *args: str) -> str:
    """Run one git command, or raise :class:`GitError` carrying its exit code and stderr."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise GitError(1, f"could not run git: {exc}") from exc
    if completed.returncode != 0:
        raise GitError(
            completed.returncode, f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout
