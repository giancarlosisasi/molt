"""`molt add` -- Create a changeset -- interactively, or from --package/--bump/--message.

Placeholder. The CLI shell declares this command's flags, help text and exit codes
(``openspec/changes/adopt-typer-cli-shell/``); the implementation is a later step of the build
order in ``roadmap/research/README.md`` §7. Its conformance suite is already written and waits in
``tests/cli/test_add.py``.
"""

from __future__ import annotations

from typing import Any

from molt.commands import not_implemented

__all__ = ["run"]

#: Removing this flag turns on the ``tests/cli/test_add.py`` conformance suite -- the module's
#: existence alone no longer means "implemented" now that the shell needs it to dispatch through.
#: See ``tests/cli/conftest.py`` and ``molt.commands``.
__molt_placeholder__ = True


def run(**options: Any) -> Any:
    """Report that `molt add` is not implemented yet, and exit non-zero.

    Deliberately signature-free: the real entry point is ``run(*, cwd: Path, **options)``,
    but pinning that here would type-check the (already written) conformance suite against a
    placeholder rather than against the implementation it is waiting for.
    """
    not_implemented("add", options)
