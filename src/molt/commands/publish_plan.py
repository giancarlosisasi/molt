"""`molt publish-plan` -- Emit the machine-readable plan of what `publish` would upload.

Placeholder. The CLI shell declares this command's flags, help text and exit codes
(``openspec/changes/adopt-typer-cli-shell/``); the implementation is a later step of the build
order in ``roadmap/research/README.md`` §7. Its conformance suite is already written and waits in
``tests/publish/test_plan.py``.
"""

from __future__ import annotations

from typing import Any

from molt.commands import not_implemented

__all__ = ["run"]

#: Marks this module as a shell placeholder rather than an implementation: the shell needs it to
#: exist so it can dispatch through it, so the module's existence alone no longer means
#: "implemented". See ``molt.commands`` and ``tests/cli/conftest.py``.
__molt_placeholder__ = True


def run(**options: Any) -> Any:
    """Report that `molt publish-plan` is not implemented yet, and exit non-zero.

    Deliberately signature-free: the real entry point is ``run(*, cwd: Path, **options)``,
    but pinning that here would type-check the (already written) conformance suite against a
    placeholder rather than against the implementation it is waiting for.
    """
    not_implemented("publish-plan", options)
