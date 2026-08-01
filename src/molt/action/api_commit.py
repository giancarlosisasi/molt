"""Turn a working tree into the file changes a host commit API can carry.

Ports ``@changesets/ghcommit`` ``src/git.ts::getFileChanges`` -- the helper
``changesets/action`` v1.9.0 reaches through when its ``commitMode`` is the host API instead of the
git command line (both fetched and read 2026-08-01). Two git reads, one refusal set, no network:
what comes out is handed to :meth:`molt.forge.Forge.create_commit`, which owns everything about the
host.

This lives in its own module rather than in :mod:`molt.action.run` because ``run.py`` has never had
a filesystem section and should not grow one to serve one branch of one mode (api-commits
design D6).

The one choice that produces green tests and wrong behaviour if reversed
-------------------------------------------------------------------------
**The comparison is the working tree against the BASE commit, not against ``HEAD``.** Upstream's
own docstring is the warning: *"If previous commits have been made locally and not pushed, you need
to set base to the last commit that is known to be in the remote repository."* A project configured
with ``commit = true`` runs a version script that makes its own local commit, and diffing against
``HEAD`` would silently drop everything that commit contains -- a release whose changelog and
version bumps are simply missing from the pull request, with no error anywhere.

What is refused, and why refusing is the honest answer
-------------------------------------------------------
A host commit API that carries no file mode -- GitHub's ``FileAddition`` has none, everything is
committed as ``100644`` -- would commit an executable as non-executable, a symbolic link as a
regular file holding its target path, and a submodule not at all. Each of those is silent. molt
refuses all three by name, exactly as ``@changesets/ghcommit`` does; it is upstream behaviour worth
keeping and it costs one ``lstat`` per changed file.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molt.errors import MoltError

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["collect_file_changes"]

#: The ``--name-status`` letter that means "this path is gone". Everything else -- ``A``, ``M``,
#: ``C``, ``T``, and the ``U`` of an unresolved merge -- describes a path that still exists and is
#: therefore an addition, because the commit API replaces a file's contents wholesale rather than
#: applying a patch.
_DELETED = "D"


def collect_file_changes(git: Any, *, base: str) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """``(additions, deletions)`` for everything the working tree changes against ``base``.

    ``additions`` maps a repository-relative POSIX path to the file's **bytes**; ``deletions`` is
    the sorted tuple of paths that are gone. Both are exactly what
    :meth:`molt.forge.Forge.create_commit` takes.

    Contents are read with :meth:`pathlib.Path.read_bytes` and never ``read_text``: a release
    rewrites ``pyproject.toml``, ``CHANGELOG.md`` and ``uv.lock``, and any of them may hold a byte
    sequence that is not UTF-8 or a line ending the checkout is meant to keep. Text mode would
    translate CRLF on a Windows runner and raise on the first non-UTF-8 byte.

    Paths are used exactly as git spelled them -- repository-relative, forward slashes, on every
    platform. They are deliberately **not** round-tripped through :class:`~pathlib.Path` and back,
    which on Windows would produce ``src\\molt\\x.py`` and commit a file with backslashes in its
    name.

    ``git`` is the injected :class:`molt.git.Git` seam, taken structurally so this module never
    imports it: the only members read are ``root``, ``diff_name_status`` and ``list_untracked``.
    """
    root = Path(git.root)

    deletions: list[str] = []
    addition_paths: list[str] = []
    for status, path in git.diff_name_status(base):
        if status.startswith(_DELETED):
            deletions.append(path)
        else:
            addition_paths.append(path)
    addition_paths.extend(git.list_untracked())

    additions = {path: _contents(root, path) for path in _unique(addition_paths)}
    return additions, tuple(sorted(deletions))


def _unique(paths: Iterable[str]) -> list[str]:
    """``paths`` with duplicates removed, first occurrence winning, order preserved.

    The two reads can name the same path only if the working tree changed under molt mid-run, but
    reading a file twice would double the memory a large release costs for no benefit, and a
    ``dict`` keyed on the path would have silently collapsed them anyway.
    """
    return list(dict.fromkeys(paths))


def _contents(root: Path, path: str) -> bytes:
    """The bytes at ``path``, or a refusal naming it when the commit API cannot represent it.

    The check is ``lstat``, never ``stat``: ``stat`` follows a symbolic link and would report the
    **target's** mode, so a link to a regular file would pass and then be committed as a copy of
    what it points at rather than as a link.

    The executable test is ``st_mode & 0o111`` and is therefore inert on Windows, where the bit does
    not exist -- which is also where git records ``100644`` anyway, so the check and the platform
    agree there. git's own recorded mode is the strictly better oracle and is recorded as
    ``openspec/GAPS.md`` ``ACM-6``.
    """
    target = root / path
    try:
        info = target.lstat()
    except OSError as error:  # pragma: no cover - a path git listed and the filesystem lost
        raise MoltError(
            f"molt could not read {path}, which the release changed: {error}"
        ) from error

    if stat.S_ISLNK(info.st_mode):
        raise _cannot_represent(path, "a symbolic link")
    if stat.S_ISDIR(info.st_mode):
        raise _cannot_represent(path, "a submodule")
    if info.st_mode & 0o111:
        raise _cannot_represent(path, "an executable file")
    return target.read_bytes()


def _cannot_represent(path: str, kind: str) -> MoltError:
    """The one refusal sentence, so all three kinds read the same and each names its own path."""
    return MoltError(
        f"molt cannot commit {path} through the host's API: it is {kind}, and the commit API "
        f"carries no file mode -- it would be committed as an ordinary file with the wrong kind, "
        f"silently. Add it to .gitignore, or release with `commit-mode: git-cli`."
    )
