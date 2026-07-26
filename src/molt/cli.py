"""CLI entry point.

Placeholder. The real command surface is step 6 of the build order in
``roadmap/research/README.md`` §7 and will be built on cyclopts (see
``roadmap/tech-stack.md`` §4). This stub exists so the ``molt`` console script declared in
``pyproject.toml`` resolves instead of raising ``ModuleNotFoundError``.
"""

from __future__ import annotations

import sys

from molt import __version__


def main() -> int:
    # Windows consoles default to cp1252, which cannot encode "—" or "§" and renders them
    # as "?". Writing plain ASCII here keeps the stub correct everywhere; once the real CLI
    # lands, rich handles console encoding for us. See roadmap/research/README.md item 15 —
    # being Windows-correct from day one is a deliberate differentiator, since upstream only
    # added Windows CI in 2026-07.
    sys.stdout.write(
        f"molt {__version__} - not implemented yet.\n\n"
        "The versioning engine spike (molt.versioning) is in place; the CLI is step 6 of\n"
        "the build order in roadmap/research/README.md section 7.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
