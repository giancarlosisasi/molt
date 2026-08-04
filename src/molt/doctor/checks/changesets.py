"""The changeset queue: is there one, what is in it, and does it name real packages.

``read_changesets`` fails the whole read on the first unparseable file, deliberately -- skipping one
would drop a release its author intended, which is a wrong version number in the direction nobody
notices. That contract is right for every command that acts on the queue and wrong for a report, so
this check parses each file itself and turns a failure into a row naming that file. Every other
changeset in the directory is still counted, which is the whole difference between "molt cannot
read your changesets" and "this one file is malformed".

Package names are compared through :func:`molt.names.normalize_name` on **both** sides. A changeset
written against ``Foo_Bar`` must find the member declaring ``foo-bar``; one forgotten call is a
package that silently never matches itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["DirectoryCheck", "PendingCheck"]


class DirectoryCheck(CheckBase):
    """Whether the changeset directory exists.

    A missing one is a failure with ``molt init`` as its remedy, and it does not stop the report:
    "my project is not set up" is the exact condition a user runs ``doctor`` to diagnose, which is
    why this command is the one verb that does not refuse to start without it.
    """

    id: ClassVar[str] = "changesets.directory"
    group: ClassVar[CheckGroup] = CheckGroup.CHANGESETS

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        from molt.changeset import CHANGESET_DIR

        directory = ctx.root / CHANGESET_DIR
        if directory.is_dir():
            yield self.ok("directory", str(directory))
            return
        yield self.fail(
            "directory",
            f"There is no {CHANGESET_DIR} directory in {ctx.root}, so this project cannot record "
            "or release changes.",
            "Run `molt init` to create one.",
        )


class PendingCheck(CheckBase):
    """How many changesets are waiting, which files are malformed, and which names are unknown."""

    id: ClassVar[str] = "changesets.pending"
    group: ClassVar[CheckGroup] = CheckGroup.CHANGESETS
    requires: ClassVar[tuple[str, ...]] = ("changesets.directory",)

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        from molt.changeset import CHANGESET_DIR, IGNORED_MD_FILES, parse_changeset
        from molt.errors import MoltParseError
        from molt.names import normalize_name

        directory = ctx.root / CHANGESET_DIR
        pending = 0
        for path in sorted(_changeset_files(directory, IGNORED_MD_FILES)):
            try:
                changeset = parse_changeset(
                    path.read_bytes().decode("utf-8-sig"),
                    id=path.name.removesuffix(".md"),
                    path=str(path),
                )
            except (MoltParseError, OSError) as error:
                yield self.fail(
                    str(path),
                    f"This changeset could not be read: {error}",
                    "Fix or delete the file; every command that reads the queue stops on it.",
                )
                continue

            pending += 1
            if ctx.workspace is None:
                continue
            known = {normalize_name(name) for name in ctx.workspace.names}
            for release in changeset.releases:
                if normalize_name(release.name) not in known:
                    yield self.fail(
                        str(path),
                        f'names "{release.name}", which is not a package in this workspace.',
                        "Correct the package name, or delete the changeset.",
                    )

        # Zero pending is `ok`, not a warning: an empty queue is the normal state of a repository
        # between releases, and warning about it would train users to ignore warnings.
        yield self.ok("pending", f"{pending} changeset{'' if pending == 1 else 's'} waiting")


def _changeset_files(directory: Path, ignored: frozenset[str]) -> list[Path]:
    """The changeset documents in ``directory``, applying ``read_changesets``' own inclusion rules.

    Reproduced here rather than reused because ``read_changesets`` returns parsed changesets and
    this check needs the file list *before* parsing -- that is the only way a malformed file can be
    named individually instead of taking the whole read down with it.
    """
    return [
        entry
        for entry in directory.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.name.endswith(".md")
        and entry.name.lower() not in ignored
    ]
