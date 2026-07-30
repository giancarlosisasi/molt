"""`molt init` -- Scaffold `.changeset/` and molt's config in the workspace root.

Ports the shape of ``packages/cli/src/commands/init/index.ts`` -- create the changeset folder,
write a starter configuration, never clobber user content -- but not its config location
(``[tool.molt]`` in ``pyproject.toml`` rather than a standalone JSON file) or its prompt order
(``cli/init.md``: base branch, changelog integration, whether to auto-commit -- upstream asks
GitHub first and the base branch last). See
``openspec/changes/implement-init-command/design.md`` for D1-D7 and
``tests/cli/test_init.py`` for the 18-row conformance suite this satisfies.

Two deliberate simplifications, both explained in the module's ``## Open gaps`` section of
``design.md`` rather than repeated here:

- The written configuration is upstream's *generator template* (``base_branch``, ``format``,
  ``changelog``, ``commit``, ``ignore``, ``fixed``, ``linked``,
  ``update_internal_dependencies`` -- upstream's eight, minus ``$schema`` and ``access``), not
  every key ``Config`` defines. ``MOLT_KEY_ORDER`` covers the full 15-key model (cross-checked
  against ``tests/config/test_parse.py``'s pinned set) so a future change is free to write more
  of it without touching the ordering contract.
- Ecosystem/workspace detection (``molt.ecosystem``) is always reported to the console, but its
  result is not itself written into the config -- ``ecosystem = "auto"`` already re-detects it
  on every later run, so persisting the detected name would be a redundant write, not a new
  capability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["MOLT_KEY_ORDER", "run"]

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from molt.config.models import Config

#: ``console=``/``prompts=`` are typed ``Any`` rather than ``molt.ui.console.Console`` /
#: ``molt.ui.prompts.Prompts`` deliberately: both protocols declare ``*messages: object`` /
#: ``**kwargs: Any`` methods, and pyrefly's structural check treats a variadic method and a
#: fixed-arity test double's method (``tests/cli/fake_cli.py::RecordingConsole.info(self,
#: message: str)``) as mutually non-assignable in *either* direction -- there is no annotation
#: that admits both the real adapter and the test double. Both seams are duck-typed by design
#: (the protocols' own docstrings: "Structural, not nominal"), so ``Any`` here gives up nothing
#: a stricter annotation would actually have enforced.

#: Filesystem constants.
_MANIFEST_NAME = "pyproject.toml"
_CHANGESET_DIR = ".changeset"
_README_NAME = "README.md"

#: molt's built-in GitHub changelog generator ref (``config/options.md``:
#: ``changelog = ["molt.changelog.github", { repo = "acme/acme" }]``). No public constant exists
#: yet for it -- ``molt.changelog.github`` is change 13's module, not yet built -- so the dotted
#: path is spelled here the same way ``molt.config.models.BUILTIN_CHANGELOG`` spells the default.
_GITHUB_CHANGELOG_REF = "molt.changelog.github"

#: The prompt sequence, in the documented order (``cli/init.md``: "base branch, changelog
#: integration, whether to auto-commit changesets"). The base branch is asked first and
#: unconditionally; the GitHub repo question only fires when the changelog confirm is accepted
#: (task 5.2); the commit confirm is always last.
_BASE_BRANCH_PROMPT = "Which branch should molt treat as the base branch?"
_GITHUB_CONFIRM_PROMPT = "Use the GitHub changelog generator?"
_GITHUB_REPO_PROMPT = "GitHub repository (owner/repo) for changelog links?"
_COMMIT_CONFIRM_PROMPT = "Commit changesets automatically after `molt version`?"

#: A short explanation of the folder, written once when ``.changeset/README.md`` is missing.
#: Never overwritten -- see the "never clobbers" rows of the conformance suite.
_README_BODY = (
    "# Changesets\n"
    "\n"
    "This directory holds changesets: short Markdown files describing an unreleased change\n"
    "and the packages it affects.\n"
    "\n"
    "Run `molt add` to create one. Run `molt version` to consume every changeset here, bump\n"
    "versions, and write changelogs. See the changeset file format documentation for the exact\n"
    "grammar these files follow.\n"
)

#: The full set of ``Config`` fields (``molt/config/models.py``), in the order they are written
#: when present (design D2). The first eight positions mirror upstream's fixed generator order
#: (``init/index.ts:59-70``) with ``$schema`` and ``access`` dropped -- and are, today, also the
#: only keys ``run`` actually writes (see the module docstring). The remaining seven are appended
#: in the order ``Config`` declares them, so a caller that later decides to write more of the
#: model has a deterministic position to put each key in without renegotiating the ordering
#: contract. Cross-checked programmatically against ``Config.model_fields`` and against
#: ``tests/config/test_parse.py``'s pinned key set: both are this exact 15-name set.
MOLT_KEY_ORDER: tuple[str, ...] = (
    # Upstream's generator order (`init/index.ts:59-70`), minus `$schema` and `access`.
    "base_branch",
    "format",
    "changelog",
    "commit",
    "ignore",
    "fixed",
    "linked",
    "update_internal_dependencies",
    # In `Config` but not written by `run` today (see the module docstring).
    "changed_file_patterns",
    "private_packages",
    "snapshot",
    "update_internal_dependents",
    "bump_workspace_sources_only",
    "ecosystem",
    "forge",
)


def run(
    *,
    cwd: Path | None = None,
    console: Any = None,
    prompts: Any = None,
    non_interactive: bool = False,
    **options: Any,
) -> None:
    """Scaffold ``.changeset/`` and write molt's configuration.

    ``cwd`` defaults to the current working directory (``cli/init.md`` step 1: "walks up from the
    working directory to find the workspace root"); the walk itself is
    :func:`molt.ecosystem.find_workspace_root`, so configuration lands at the workspace root even
    when ``init`` is run from a nested package. ``console``/``prompts`` are injectable seams for
    the conformance suite (:mod:`tests.cli.fake_cli`); a real invocation gets the module-level
    :data:`molt.ui.console.console` and the (not yet built) ``QuestionaryPrompts``.

    Heavy imports (``molt.config``, ``molt.ecosystem``) happen here, not at module scope --
    ``tests/cli/test_cli.py``'s import-light assertion pins that for every ``molt.commands.*``
    module.
    """
    del options  # `init` declares only `--cwd` (the shell's global) and `--non-interactive`.

    from pathlib import Path

    from molt.config.load import JSON_CONFIG_PATH
    from molt.config.models import default_config
    from molt.ecosystem import discover_workspace, find_workspace_root, read_toml
    from molt.errors import ExitError

    if console is None:
        from molt.ui.console import console as _default_console

        console = _default_console
    if prompts is None:
        from molt.ui.prompts import QuestionaryPrompts

        prompts = QuestionaryPrompts()

    root = find_workspace_root(cwd if cwd is not None else Path.cwd())
    manifest_path = root / _MANIFEST_NAME
    json_path = root / JSON_CONFIG_PATH

    # Design D4: the two-locations check runs before any write, and before any other console
    # output -- detecting it after scaffolding would leave a half-initialized project in the
    # state the error is complaining about.
    pyproject_table = _pyproject_molt_table(manifest_path, read_toml)
    has_json_config = json_path.is_file()
    if pyproject_table is not None and has_json_config:
        console.error(
            f"Both {manifest_path} [tool.molt] and {json_path} define configuration; pick one."
        )
        raise ExitError(1)

    # Design D5: detection is delegated to `molt.ecosystem`, and its result is reported -- the
    # cheapest place to confirm discovery works before any release depends on it.
    _report_ecosystem(root, console, discover_workspace)

    # Design D3: four independent existence checks, never collapsed into one "already
    # initialized" boolean -- that collapse is what would suppress a missing README behind an
    # existing config.
    changeset_dir = root / _CHANGESET_DIR
    readme_path = changeset_dir / _README_NAME
    readme_missing = not readme_path.is_file()
    config_exists = pyproject_table is not None or has_json_config

    if config_exists:
        location = manifest_path if pyproject_table is not None else json_path
        console.info(
            f"molt is already initialized: configuration found at {location}. Leaving it untouched."
        )
        if readme_missing:
            _write_readme(changeset_dir, readme_path)
        return

    defaults = default_config()
    base_branch, changelog, commit = _collect_answers(
        prompts, non_interactive=non_interactive, defaults=defaults
    )
    values: dict[str, Any] = {
        "base_branch": base_branch,
        "format": defaults.format,
        "changelog": changelog,
        "commit": commit,
        "ignore": list(defaults.ignore),
        "fixed": [list(group) for group in defaults.fixed],
        "linked": [list(group) for group in defaults.linked],
        "update_internal_dependencies": defaults.update_internal_dependencies,
    }
    _write_config(manifest_path, values)
    if readme_missing:
        _write_readme(changeset_dir, readme_path)
    console.success(f"Initialized molt in {manifest_path}.")


# ======================================================================================
# Config-location detection (design D4)
# ======================================================================================


def _pyproject_molt_table(manifest_path: Path, read_toml: Any) -> dict[str, Any] | None:
    """``[tool.molt]`` of ``manifest_path``, or ``None`` when the table is absent.

    An **empty** ``[tool.molt]`` is a present source, not an absent one -- the same rule
    :func:`molt.config.load.load_config` enforces, so a user who wrote the header and no options
    still trips the two-locations check.
    """
    if not manifest_path.is_file():
        return None
    tool = read_toml(manifest_path).get("tool")
    if not isinstance(tool, dict):
        return None
    molt_table = tool.get("molt")
    return molt_table if isinstance(molt_table, dict) else None


# ======================================================================================
# Ecosystem/workspace detection and reporting (design D5)
# ======================================================================================


def _report_ecosystem(root: Path, console: Any, discover_workspace: Any) -> None:
    """Report the detected backend, and for a uv workspace, its members (task 4.2/4.3)."""
    workspace = discover_workspace(root, "auto")
    if workspace.backend == "uv":
        root_dir = workspace.root_package.directory if workspace.root_package is not None else None
        members = [package.name for package in workspace.packages if package.directory != root_dir]
        listed = ", ".join(members) if members else "none"
        console.info(f"Detected a uv workspace at {root} with {len(members)} member(s): {listed}.")
    else:
        name = workspace.root_package.name if workspace.root_package is not None else "(no package)"
        console.info(f"Detected a single-package project: {name}.")


# ======================================================================================
# The prompt sequence (design D6, D7)
# ======================================================================================


def _collect_answers(
    prompts: Any, *, non_interactive: bool, defaults: Config
) -> tuple[str, Any, bool]:
    """Ask the documented questions, or (design D6) accept every default without asking at all.

    ``defaults.changelog`` is typed ``GeneratorRef | Literal[False]`` on ``Config``, so its first
    element is not statically indexable; :data:`~molt.config.models.BUILTIN_CHANGELOG` is the same
    string ``default_config().changelog[0]`` would give at runtime, without the union.
    """
    from molt.config.models import BUILTIN_CHANGELOG

    if non_interactive:
        return defaults.base_branch, BUILTIN_CHANGELOG, False

    base_branch = _normalize_base_branch(prompts.text(_BASE_BRANCH_PROMPT), defaults.base_branch)

    use_github = bool(prompts.confirm(_GITHUB_CONFIRM_PROMPT))
    changelog: Any
    if use_github:
        repo = prompts.text(_GITHUB_REPO_PROMPT)
        changelog = [_GITHUB_CHANGELOG_REF, {"repo": repo}]
    else:
        changelog = BUILTIN_CHANGELOG

    commit = bool(prompts.confirm(_COMMIT_CONFIRM_PROMPT))
    return base_branch, changelog, commit


def _normalize_base_branch(answer: Any, fallback: str) -> str:
    """Design D7: an empty answer falls back to ``fallback`` (``"main"``), applied *after* the
    prompt rather than passed to it as a default -- the fallback must fire for an explicitly
    emptied answer, not only for one the user never touched.
    """
    if isinstance(answer, str):
        candidate = answer.strip()
        if candidate:
            return candidate
    return fallback


# ======================================================================================
# Writing the configuration (design D1, D2)
# ======================================================================================


def _write_config(manifest_path: Path, values: Mapping[str, Any]) -> None:
    """Append a ``[tool.molt]`` section to ``manifest_path``, byte-exactly.

    Design D1: the document is assembled as an explicit ``\\n``-terminated string and written with
    ``write_bytes`` -- never through ``Path.write_text``, which translates newlines and would land
    CRLF on Windows. Existing content (a ``[project]`` table, ``[tool.uv.workspace]``, anything
    else) is preserved verbatim and never reformatted: only the new section is rendered through
    ``tomlkit``, and it is *appended*, not merged into a parsed-and-redumped document.
    """
    import tomlkit

    existing = manifest_path.read_bytes().decode("utf-8") if manifest_path.is_file() else ""

    section_lines = ["[tool.molt]"]
    for key in MOLT_KEY_ORDER:
        if key not in values:
            continue
        section_lines.append(f"{key} = {tomlkit.item(values[key]).as_string()}")
    section = "\n".join(section_lines) + "\n"

    if existing.strip():
        prefix = existing.rstrip("\n") + "\n"
        document = f"{prefix}\n{section}"
    else:
        document = section
    document = document.rstrip("\n") + "\n"  # exactly one trailing LF, never CRLF
    manifest_path.write_bytes(document.encode("utf-8"))


def _write_readme(changeset_dir: Path, readme_path: Path) -> None:
    """Create ``.changeset/`` (if needed) and its ``README.md``. Never called when one exists."""
    changeset_dir.mkdir(parents=True, exist_ok=True)
    readme_path.write_bytes(_README_BODY.encode("utf-8"))
