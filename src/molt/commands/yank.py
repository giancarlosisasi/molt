"""`molt yank` -- Read-only advisory: report how to yank a release (PEP 592) on the index.

molt-native; there is nothing to port. Everything the command knows lives in
:func:`molt.publish.yank`; this module is the shell over it. Website docs:
``website/docs/cli/yank.md`` (flags, exit codes) and ``website/docs/guide/yank.md`` (semantics).
The conformance suite is ``tests/publish/test_yank.py``.

**It never mutates and needs no credential.** PyPI exposes no supported way for a tool to perform a
yank -- it is a web-UI action, there is no documented API endpoint, and an upload token would not
authorize one -- so molt verifies the release, reports whether it is already yanked and why, and
prints the management URL plus the steps to complete in a browser. That is why this is the one
mutating-sounding command with no ``--dry-run`` (every run is already one) and no ``--yes`` (there
is nothing to confirm).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.publish import YankReport


def run(
    *,
    package: str,
    version: str,
    cwd: Path | None = None,
    reason: str | None = None,
    undo: bool = False,
    repository: str | None = None,
    console: Any = None,
    index: Any = None,
    **options: Any,
) -> YankReport:
    """Check the release on the index and print the steps to yank (or un-yank) it by hand.

    Exits 0 when the steps are printed **and** when the version is already in the requested state:
    a re-run of a completed recovery must not turn a CI job red. The only failures are "not found on
    the index" and "the index could not be reached", both of which the library raises as a
    :class:`molt.errors.MoltError` for the shell's funnel to render and exit 1 on. There is no
    authentication failure case -- this reads public data.

    ``cwd`` is accepted because ``--cwd`` is a global flag of the shell, but nothing here reads the
    workspace: a yank is about a distribution on an index, which need not be one this checkout
    contains.

    Every heavy import happens inside this function: ``tests/cli/test_cli.py``'s import-light
    assertion lists ``molt.publish`` among the modules ``import molt.cli`` must not pull in.
    """
    del options, cwd  # `--non-interactive` is the shell's global; `yank` never prompts.

    from molt.publish import yank

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    return yank(
        package,
        version,
        reason=reason,
        undo=undo,
        repository=repository,
        console=console,
        index=index,
    )
