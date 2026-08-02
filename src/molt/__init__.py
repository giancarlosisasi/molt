"""molt — changeset-driven versioning and changelogs for Python monorepos."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

__all__ = ["__version__"]

try:
    #: Read from the INSTALLED distribution metadata rather than written here, so that
    #: ``[project].version`` in ``pyproject.toml`` is the single source of truth. ``molt version``
    #: rewrites that key and nothing else, so a literal here drifts one release behind on every
    #: run -- which is exactly what it did between 0.1.0 and 0.1.1, making ``molt --version`` lie.
    #: ``importlib.metadata`` is stdlib and is not on
    #: ``tests/cli/test_cli.py::HEAVY_MODULE_PREFIXES``, so ``molt --help`` pays nothing for it.
    __version__ = _distribution_version("molt-release")
except PackageNotFoundError:  # pragma: no cover - a source tree that was never installed
    #: A checkout with ``src/`` on the path and no install. A PEP 440 local version, so anything
    #: that parses it still gets a valid one.
    __version__ = "0+unknown"
