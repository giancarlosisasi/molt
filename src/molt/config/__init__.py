"""molt's configuration surface -- resolution, validation, normalization and its JSON Schema.

Start at :func:`load_config` (read the workspace's configuration off disk) or :func:`parse_config`
(validate a document you already have). Neither raises: both return a
:class:`~molt.config.result.ConfigResult` of ``(config, warnings, errors)``, because a configuration
can be partly usable and a successful parse can still produce warnings (design D3).

**This module is a lazy facade, and that is load-bearing.** pydantic costs 60-100 ms to import;
``molt --help``, ``molt --version`` and shell completion must not pay it (design D2, research README
section 6). Nothing is imported until an attribute is actually touched, so::

    import sys, molt.config
    assert "pydantic" not in sys.modules

holds. Adding a module-level ``from .models import Config`` here would silently undo it, which is
why the deferral is pinned by a spec scenario rather than left as a convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from molt.config.json_schema import config_json_schema
    from molt.config.load import JSON_CONFIG_PATH, load_config
    from molt.config.models import (
        ChangelogOptions,
        Config,
        PrivatePackages,
        SnapshotOptions,
        VersionSourceConfig,
        default_config,
    )
    from molt.config.parse import parse_config
    from molt.config.result import ConfigIssue, ConfigResult

__all__ = [
    "JSON_CONFIG_PATH",
    "ChangelogOptions",
    "Config",
    "ConfigIssue",
    "ConfigResult",
    "PrivatePackages",
    "SnapshotOptions",
    "VersionSourceConfig",
    "config_json_schema",
    "default_config",
    "load_config",
    "parse_config",
]

#: Public name -> the submodule that defines it. The map is explicit rather than derived so a
#: typo in ``__all__`` fails loudly at import of the name, not silently at first use.
_EXPORTS: dict[str, str] = {
    "JSON_CONFIG_PATH": "molt.config.load",
    "ChangelogOptions": "molt.config.models",
    "Config": "molt.config.models",
    "ConfigIssue": "molt.config.result",
    "ConfigResult": "molt.config.result",
    "PrivatePackages": "molt.config.models",
    "SnapshotOptions": "molt.config.models",
    "VersionSourceConfig": "molt.config.models",
    "config_json_schema": "molt.config.json_schema",
    "default_config": "molt.config.models",
    "load_config": "molt.config.load",
    "parse_config": "molt.config.parse",
}


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access -- the mechanism behind the deferred pydantic import."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
