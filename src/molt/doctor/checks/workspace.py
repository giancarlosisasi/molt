"""Where molt decided the project starts, and what it found inside it.

Everything downstream of discovery is meaningless without it -- which package a changeset names,
which versions resolve, which globs match -- so these two checks are what the rest of the report
declares in ``requires``. A directory that is not a Python project at all is the case worth getting
right: it fails here with what molt searched for, and the checks that need packages report *not
applicable* rather than each inventing its own version of the same failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["PackagesCheck", "RootCheck"]

_MANIFEST_NAME = "pyproject.toml"

#: How each backend found the root, in the backend's own words. ``find_workspace_root`` prefers the
#: nearest ancestor declaring a workspace and falls back to the nearest ancestor holding a manifest,
#: and the backend that discovery selected is what tells the two apart -- without this module having
#: to learn which table any one packaging tool writes.
_BACKEND_DESCRIPTIONS: dict[str, str] = {
    "uv": "a uv workspace declaration",
    "single": "the nearest pyproject.toml (single-package repository)",
}


class RootCheck(CheckBase):
    """The workspace root, and whether there is a Python project here at all."""

    id: ClassVar[str] = "workspace.root"
    group: ClassVar[CheckGroup] = CheckGroup.WORKSPACE

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        if (ctx.root / _MANIFEST_NAME).is_file():
            yield self.ok("root", str(ctx.root))
            return
        yield self.fail(
            "root",
            f"No {_MANIFEST_NAME} was found in {ctx.cwd} or in any directory above it, so molt "
            "cannot tell where this project starts.",
            f"Run molt from inside a Python project, use --cwd, or create a {_MANIFEST_NAME}.",
        )


class PackagesCheck(CheckBase):
    """Which backend discovery selected, and how many packages it found."""

    id: ClassVar[str] = "workspace.packages"
    group: ClassVar[CheckGroup] = CheckGroup.WORKSPACE
    requires: ClassVar[tuple[str, ...]] = ("workspace.root",)

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        if ctx.workspace is None:
            yield self.fail(
                "packages",
                ctx.workspace_error or "The packages in this workspace could not be discovered.",
                "Fix the manifest named above; every other check needs the package list.",
            )
            return

        found = _BACKEND_DESCRIPTIONS.get(ctx.workspace.backend, ctx.workspace.backend)
        count = len(ctx.workspace.packages)
        if count == 0:
            yield self.warn(
                "packages",
                f"No packages were found from {found}.",
                "Check that the workspace members pattern matches your package directories.",
            )
            return
        # The packages themselves are named one row each by the version check, which is the row a
        # user actually needs: "pkg-a exists" is worth much less than "pkg-a will be skipped".
        yield self.ok("packages", f"{count} found from {found}")
