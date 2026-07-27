"""``load_config`` -- resolving the configuration document off disk.

Ports ``readConfig`` (``packages/config/src/parse.ts:59``), which walks up to the workspace root via
``@manypkg/get-packages`` and reads ``.changeset/config.json``. molt reads ``[tool.molt]`` in the
workspace-root ``pyproject.toml`` **or** ``.molt/config.json``, and walks up through
:func:`molt.ecosystem.find_workspace_root`.

**Two sources is an error, never a merge** (design D7, research README open decision #7). Merging
needs a precedence rule, and a release tool is the last place for a surprising one: the failure mode
is a user editing the file that loses and watching nothing happen. The error names both paths.

Like :func:`molt.config.parse.parse_config`, nothing here raises. A malformed manifest, an
unreadable JSON document and an unimplemented ecosystem all come back as errors on the result, so
one command run can report every problem at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molt.config.parse import parse_config
from molt.config.result import ConfigIssue, ConfigResult

__all__ = ["JSON_CONFIG_PATH", "load_config"]

#: The alternative to ``[tool.molt]``. Named after ``.changeset/config.json`` so a migrating user
#: recognizes it (research doc 02 section 12.1).
JSON_CONFIG_PATH = Path(".molt") / "config.json"

_MANIFEST_NAME = "pyproject.toml"


def load_config(start: Path | str) -> ConfigResult:
    """Read, validate and normalize the configuration governing the workspace containing ``start``.

    Configuration is a property of the workspace **root**, not of the current directory, so this
    behaves identically run from a member package and from the top -- the observable behavior that
    ports from upstream, even though root discovery is uv-shaped rather than pnpm-shaped.
    """
    from molt.ecosystem import discover_workspace, find_workspace_root, read_toml
    from molt.errors import MoltError

    root = find_workspace_root(Path(start))
    warnings: list[ConfigIssue] = []
    errors: list[ConfigIssue] = []

    manifest = root / _MANIFEST_NAME
    json_path = root / JSON_CONFIG_PATH
    try:
        table = _tool_table(manifest, read_toml)
    except MoltError as exc:
        return ConfigResult(None, warnings, [ConfigIssue((), str(exc))])

    has_json = json_path.is_file()
    if table is not None and has_json:
        return ConfigResult(
            None,
            warnings,
            [
                ConfigIssue(
                    (),
                    f"Configuration is defined in two places: [tool.molt] in {manifest} and "
                    f"{json_path}. molt never merges them -- keep exactly one.",
                )
            ],
        )

    if has_json:
        written, read_error = _read_json(json_path)
        if read_error is not None:
            return ConfigResult(None, warnings, [read_error])
    else:
        written = table if table is not None else {}

    ecosystem = written.get("ecosystem") if isinstance(written, dict) else None
    try:
        workspace = discover_workspace(root, ecosystem if isinstance(ecosystem, str) else "auto")
    except MoltError as exc:
        return ConfigResult(None, warnings, [ConfigIssue(("ecosystem",), str(exc))])

    result = parse_config(written, package_names=workspace.names, workspace=workspace)
    return ConfigResult(result.config, [*warnings, *result.warnings], [*errors, *result.errors])


def _tool_table(manifest: Path, read_toml: Any) -> dict[str, Any] | None:
    """``[tool.molt]`` of ``manifest``, or ``None`` when the table is absent.

    An **empty** ``[tool.molt]`` is a present source, not an absent one: writing the header and no
    options is how a user says "defaults, explicitly", and treating it as absent would make the
    both-sources check miss a real conflict.
    """
    if not manifest.is_file():
        return None
    tool = read_toml(manifest).get("tool")
    if not isinstance(tool, dict):
        return None
    molt = tool.get("molt")
    return molt if isinstance(molt, dict) else None


def _read_json(path: Path) -> tuple[Any, ConfigIssue | None]:
    """Read ``.molt/config.json``, reporting a decode failure rather than raising it."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, ConfigIssue((), f"Could not read {path}: {exc}")
