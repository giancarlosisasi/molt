"""Terminal I/O adapters -- the one seam between molt and its terminal libraries.

Everything molt prints or asks goes through a protocol defined here, never through a direct
``print`` or a ``rich``/``questionary`` call in command code (``tech-stack.md`` §5, and the
``terminal-ui`` spec of the ``adopt-typer-cli-shell`` change). That keeps the rendering backend and
the prompt backend swappable in one file each instead of across every command.

Nothing is re-exported at package level on purpose: importing ``molt.ui`` must stay free of
``rich``, so ``molt --help`` and shell completion do not pay for a console they never build. Import
the concrete module you need (``molt.ui.console`` / ``molt.ui.prompts``).
"""

from __future__ import annotations

__all__: list[str] = []
