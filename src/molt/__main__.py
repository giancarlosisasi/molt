"""``python -m molt`` -- the same program as the ``molt`` console script.

A bootstrap and nothing else. The banner gating, the error funnel and the exit-code contract all
live in :func:`molt.cli.main`, because that is the function ``[project.scripts]`` points at
(``molt = "molt.cli:main"``): putting any of them here would mean the installed command and
``python -m molt`` behaved differently, and only one of the two is what users actually run.
"""

from __future__ import annotations

from molt.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
