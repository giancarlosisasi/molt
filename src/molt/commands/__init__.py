"""Command implementations, one module per CLI verb.

The CLI shell (``molt.cli``) imports these modules **inside** the command body, never at module
level (``adopt-typer-cli-shell`` design D7): ``molt --help`` and shell completion must not pay for
the release-plan engine, the config parser, or a PyPI client. ``tests/cli/test_cli.py`` pins that
with an import-light assertion, so a convenience re-export here would be a test failure, not a
style nit.

**Every module in this package is currently a placeholder.** The shell change landed the framework,
the flag matrix and the cross-cutting contracts; the command logic is later build-order steps. A
placeholder module declares ``__molt_placeholder__ = True`` and its ``run`` reports "not implemented
yet" and exits non-zero, which is the ``cli-shell`` spec's "Declared but unimplemented command"
scenario. Deleting that flag is the switch that turns the matching ``tests/cli/test_<command>.py``
conformance suite on -- see ``tests/cli/conftest.py``.

The entry point of every module is ``run(*, cwd: Path, **options)`` -- keyword-only, one keyword per
long flag with dashes turned into underscores. Nothing is passed positionally, so a command's
signature order never becomes part of the CLI contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["not_implemented"]


def not_implemented(command: str, options: Mapping[str, Any] | None = None) -> NoReturn:
    """Report that ``command`` is declared but unimplemented, and exit non-zero.

    Shared by every placeholder module so the message, the stream and the exit code cannot drift
    apart across ten of them. ``typer`` and the console adapter are imported here rather than at
    module level to keep a placeholder as cheap to import as the real module will be.
    """
    del options
    import typer

    from molt.ui.console import console

    console.error(
        f"`molt {command}` is not implemented yet. "
        "The CLI shell (flags, help, exit codes) is in place; this command's implementation "
        "is a later step of the build order."
    )
    raise typer.Exit(1)
