"""What ``ignore``, ``fixed`` and ``linked`` actually cover in *this* workspace.

The configuration layer already resolves these three options against the live package list and
warns about an entry that matches nothing, and the configuration check renders those warnings. What
it cannot show is the *result*: which packages a ``fixed`` group ties together, and which packages a
release will never touch. That is what this check reports, and it is nowhere else in molt's output.

Matching goes through :func:`molt.globs.glob_match` -- the picomatch port, negation included --
and **never** ``fnmatch``: ``fnmatch``'s ``*`` crosses ``/``, its ``?`` matches ``/``, it has no
negation, and its case folding depends on the platform. An entry that matches nothing is a warning,
never a failure: a glob that legitimately covers no package today is a fact about the workspace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["FiltersCheck"]


class FiltersCheck(CheckBase):
    """One row per configured entry, naming what it resolves to."""

    id: ClassVar[str] = "groups.filters"
    group: ClassVar[CheckGroup] = CheckGroup.GROUPS
    requires: ClassVar[tuple[str, ...]] = ("workspace.packages",)

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        assert ctx.workspace is not None  # guaranteed by `requires`
        config = ctx.config.config
        if config is None:
            yield self.warn(
                "filters",
                "The configuration could not be read, so its ignore, fixed and linked entries "
                "could not be matched against this workspace.",
                "Fix the configuration failures above, then run `molt doctor` again.",
            )
            return

        names = ctx.workspace.names
        empty = True
        for entry in config.ignore:
            empty = False
            yield self._entry("ignore", entry, names)
        for index, group in enumerate(config.fixed, start=1):
            empty = False
            yield self._group("fixed", index, group, names)
        for index, group in enumerate(config.linked, start=1):
            empty = False
            yield self._group("linked", index, group, names)

        if empty:
            yield self.ok("filters", "no ignore, fixed or linked entries are configured")

    def _entry(self, option: str, entry: str, names: Sequence[str]) -> Row:
        """One ``ignore`` entry and the packages it covers."""
        matched = _matches(entry, names)
        if not matched:
            return self.warn(
                f"{option}: {entry}",
                "matches no package in this workspace, so it has no effect today.",
                "Remove it, or correct the spelling if a package was meant.",
            )
        return self.ok(f"{option}: {entry}", f"never released: {', '.join(matched)}")

    def _group(self, option: str, index: int, group: Sequence[str], names: Sequence[str]) -> Row:
        """One ``fixed`` or ``linked`` group and the packages it ties together."""
        matched = sorted({name for entry in group for name in _matches(entry, names)})
        subject = f"{option} group {index}"
        if not matched:
            return self.warn(
                subject,
                f"{', '.join(group)} matches no package in this workspace, so the group is inert.",
                "Remove the group, or correct the names.",
            )
        shared = "at one shared version" if option == "fixed" else "when released together"
        return self.ok(subject, f"{', '.join(matched)} release {shared}")


def _matches(pattern: str, names: Sequence[str]) -> list[str]:
    """The workspace packages ``pattern`` covers, in discovery order.

    ``key=normalize_name`` is what makes ``Foo_Bar`` match the member declaring ``foo-bar``, and it
    is the same argument :mod:`molt.config.rules` passes -- so this report and the configuration
    layer resolve an entry identically, which is the only way the two can agree.
    """
    from molt.globs import glob_match
    from molt.names import normalize_name

    return list(glob_match(names, [pattern], key=normalize_name))
