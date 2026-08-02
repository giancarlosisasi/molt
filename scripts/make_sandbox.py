"""Build throwaway molt sandboxes under ``molt/tmp`` so the CLI can be driven by hand.

Two repositories are produced, because molt has to be correct in both shapes:

``tmp/workspace``
    A uv workspace with four members and real internal pins. ``demo-app`` caps ``demo-core``
    at ``<0.2.0`` and ``demo-utils`` does not, so a single ``minor`` changeset on ``demo-core``
    releases ``demo-app`` and leaves ``demo-utils`` alone -- the ``update_internal_dependents
    = "out-of-range"`` behaviour walked through in ``guides/01-try-molt-by-hand.md`` step 8.
    ``demo-internal`` carries the ``Private :: Do Not Upload`` classifier, so publish and skip-tree
    handling get exercised too.

``tmp/single``
    One package at the repository root, with no ``[tool.uv.workspace]`` table at all. This is the
    shape ``molt.ecosystem.find_workspace_root`` falls back to (nearest ancestor with a
    ``pyproject.toml``), and it is the shape most first-time users arrive with.

Both are git repositories with one commit on ``main``, because molt reads git history to decide
what changed and refuses to run without it.

Run it from the package root ``molt/``::

    uv run scripts/make_sandbox.py                # rebuild both sandboxes
    uv run scripts/make_sandbox.py --only single  # rebuild one
    uv run scripts/make_sandbox.py --init         # and run `molt init --yes` in each

``tmp/`` is git-ignored, so nothing this writes can reach the molt repository. Every file goes out
through :func:`write_text_file` as LF bytes on every platform, matching ``.gitattributes`` and the
bytes molt itself writes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["main"]

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = PACKAGE_ROOT / "tmp"

# Written into every sandbox root. `--reset` refuses to delete a directory that does not carry it,
# so a mistyped `--dest` can never remove something this script did not create.
MARKER_NAME = ".molt-sandbox"
MARKER_TEXT = "Created by scripts/make_sandbox.py. Safe to delete.\n"

GIT_IDENTITY = (
    ("user.name", "Molt Sandbox"),
    ("user.email", "sandbox@molt.invalid"),
    # A machine with commit signing configured globally would otherwise fail the initial commit.
    ("commit.gpgsign", "false"),
    ("tag.gpgsign", "false"),
    # Keep the working tree LF on Windows, so a diff shows the line molt changed and nothing else.
    ("core.autocrlf", "false"),
)


# ======================================================================================
# The package model
# ======================================================================================


@dataclass(frozen=True)
class Member:
    """One Python distribution inside a sandbox.

    ``dependencies`` are PEP 508 strings written verbatim into ``[project].dependencies``.
    ``workspace_sources`` names the subset of them that are workspace members, which is what
    ``[tool.uv.sources]`` needs so uv resolves them from disk instead of from PyPI.
    """

    directory: str
    name: str
    module: str
    summary: str
    version: str = "0.1.0"
    dependencies: tuple[str, ...] = ()
    workspace_sources: tuple[str, ...] = ()
    private: bool = False


WORKSPACE_MEMBERS: tuple[Member, ...] = (
    Member(
        directory="packages/core",
        name="demo-core",
        module="demo_core",
        summary="The library everything else in the sandbox depends on.",
    ),
    Member(
        directory="packages/app",
        name="demo-app",
        module="demo_app",
        summary="Depends on demo-core with an upper cap, so a minor bump releases it too.",
        dependencies=("demo-core>=0.1.0,<0.2.0",),
        workspace_sources=("demo-core",),
    ),
    Member(
        directory="packages/utils",
        name="demo-utils",
        module="demo_utils",
        summary="Depends on demo-core with no upper cap, so a minor bump leaves it alone.",
        dependencies=("demo-core>=0.1.0",),
        workspace_sources=("demo-core",),
    ),
    Member(
        directory="packages/internal",
        name="demo-internal",
        module="demo_internal",
        summary="Private: versioned like any other package, never published.",
        # Capped on demo-core, exactly like demo-app: a demo-core minor bump has to release this
        # one too, which is the only way `molt publish-plan` gets a chance to show a private
        # package being versioned and then skipped at upload time.
        dependencies=("demo-core>=0.1.0,<0.2.0",),
        workspace_sources=("demo-core",),
        private=True,
    ),
)

SINGLE_MEMBER = Member(
    directory=".",
    name="demo-solo",
    module="demo_solo",
    summary="A one-package repository -- no uv workspace table anywhere.",
)


# ======================================================================================
# Filesystem primitives
# ======================================================================================


def write_text_file(path: Path, text: str) -> None:
    """Write ``text`` at ``path`` as LF-terminated UTF-8, creating parent directories.

    ``write_bytes``, not ``write_text``: text mode translates ``\\n`` to ``\\r\\n`` on Windows, and
    a sandbox whose manifests are CRLF makes every ``git diff`` after ``molt version`` unreadable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    path.write_bytes(text.encode("utf-8"))


def _clear_readonly(func: Callable[[str], object], path: str, _error: BaseException) -> None:
    """Retry a failed removal after dropping the read-only bit.

    Git marks the loose objects under ``.git/objects`` read-only, and on Windows that makes
    ``shutil.rmtree`` fail outright rather than silently succeeding as it does on POSIX.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    """``shutil.rmtree`` that survives a git repository on Windows."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly)
    else:  # pragma: no cover - 3.11 only; `onexc` did not exist yet
        shutil.rmtree(path, onerror=_clear_readonly)


def reset_directory(path: Path, *, force: bool) -> None:
    """Delete ``path`` if it is one of ours, then recreate it empty.

    Refuses a directory with no :data:`MARKER_NAME` file unless ``force``. The whole point of the
    check is that ``--dest`` takes an arbitrary path, and this function deletes recursively.
    """
    if path.exists():
        if not path.is_dir():
            raise SystemExit(f"error: {path} exists and is not a directory")
        if not (path / MARKER_NAME).exists() and not force:
            raise SystemExit(
                f"error: {path} exists but was not created by this script "
                f"(no {MARKER_NAME} file). Delete it yourself, or pass --force."
            )
        remove_tree(path)
    path.mkdir(parents=True)
    write_text_file(path / MARKER_NAME, MARKER_TEXT)


# ======================================================================================
# Manifest rendering
# ======================================================================================


def _render_list(values: Sequence[str]) -> str:
    if not values:
        return "[]"
    body = "".join(f'    "{value}",\n' for value in values)
    return f"[\n{body}]"


def render_manifest(member: Member) -> str:
    """The ``pyproject.toml`` for one member.

    No ``[tool.uv.workspace]`` table is ever emitted here. The workspace sandbox's root manifest is
    written by :func:`build_workspace` and holds nothing else; the single-package sandbox has no
    such table at all, which is the whole point of it.
    """
    lines = [
        "[project]",
        f'name = "{member.name}"',
        f'version = "{member.version}"',
        f'description = "{member.summary}"',
        'readme = "README.md"',
        'requires-python = ">=3.11"',
        f"dependencies = {_render_list(member.dependencies)}",
    ]
    if member.private:
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    if member.workspace_sources:
        lines += ["", "[tool.uv.sources]"]
        lines += [f"{name} = {{ workspace = true }}" for name in member.workspace_sources]
    lines += [
        "",
        "[build-system]",
        'requires = ["hatchling"]',
        'build-backend = "hatchling.build"',
    ]
    return "\n".join(lines)


def write_member(root: Path, member: Member) -> None:
    """Write one member's manifest, source module and README under ``root``.

    A member whose ``directory`` is ``"."`` writes its README at the sandbox root, where the
    sandbox's own README then replaces it -- deliberate, so ``readme = "README.md"`` still
    resolves and the reader gets the useful text rather than a one-line stub.
    """
    directory = root / member.directory
    write_text_file(directory / "pyproject.toml", render_manifest(member))
    write_text_file(
        directory / "src" / member.module / "__init__.py",
        f'"""{member.summary}"""\n\n__version__ = "{member.version}"\n',
    )
    write_text_file(
        directory / "README.md",
        f"# {member.name}\n\n{member.summary}\n",
    )


# ======================================================================================
# The two sandboxes
# ======================================================================================


def build_workspace(root: Path) -> None:
    """A uv workspace with four members and real internal pins."""
    write_text_file(
        root / "pyproject.toml",
        "\n".join(["[tool.uv.workspace]", 'members = ["packages/*"]']),
    )
    write_text_file(
        root / "README.md",
        "\n".join(
            [
                "# molt workspace sandbox",
                "",
                "Four packages. The pins are the interesting part:",
                "",
                "| Package | Depends on | Released by a `demo-core` minor bump? |",
                "|---|---|---|",
                "| `demo-core` | -- | yes, you asked for it |",
                "| `demo-app` | `demo-core>=0.1.0,<0.2.0` | yes -- 0.2.0 falls outside the cap |",
                "| `demo-utils` | `demo-core>=0.1.0` | no -- 0.2.0 still satisfies the pin |",
                "| `demo-internal` | `demo-core>=0.1.0,<0.2.0` | yes, but never published |",
                "",
                "Try:",
                "",
                "```",
                "uv run molt init --cwd tmp/workspace --yes",
                "uv run molt add --cwd tmp/workspace --package demo-core --bump minor "
                '-m "Add a greeting helper" --yes',
                "uv run molt status --cwd tmp/workspace --verbose",
                "uv run molt version --cwd tmp/workspace --dry-run",
                "```",
            ]
        ),
    )
    for member in WORKSPACE_MEMBERS:
        write_member(root, member)


def build_single(root: Path) -> None:
    """One package at the repository root, with no workspace table."""
    write_member(root, SINGLE_MEMBER)
    write_text_file(
        root / "README.md",
        "\n".join(
            [
                "# molt single-package sandbox",
                "",
                "One distribution, at the repository root. No `[tool.uv.workspace]` table -- molt",
                "falls back to the nearest ancestor holding a `pyproject.toml`.",
                "",
                "Try:",
                "",
                "```",
                "uv run molt init --cwd tmp/single --yes",
                "uv run molt add --cwd tmp/single --package demo-solo --bump patch "
                '-m "Fix a typo" --yes',
                "uv run molt status --cwd tmp/single --verbose",
                "uv run molt version --cwd tmp/single --dry-run",
                "```",
            ]
        ),
    )


SANDBOXES: dict[str, tuple[str, Callable[[Path], None]]] = {
    "workspace": ("uv workspace, 4 members", build_workspace),
    "single": ("single package, no workspace", build_single),
}


# ======================================================================================
# git + molt
# ======================================================================================


def _run(command: Sequence[str], *, cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"error: {' '.join(command)} failed in {cwd}\n{detail}")


def git_init(root: Path) -> bool:
    """Make ``root`` an empty git repository on ``main``. Returns False if git is missing.

    ``main`` explicitly: molt's default ``base_branch`` is ``main``, and a sandbox sitting on
    ``master`` fails change detection for a reason that looks like a molt bug and is not.
    """
    git = shutil.which("git")
    if git is None:
        print("    warning: git is not on PATH -- sandbox left without a repository.")
        print("             molt reads git history and will refuse to run here.")
        return False
    _run([git, "init", "-q", "-b", "main"], cwd=root)
    for key, value in GIT_IDENTITY:
        _run([git, "config", key, value], cwd=root)
    return True


def git_commit_all(root: Path, message: str) -> None:
    """Stage everything and commit.

    Called *after* ``molt init``, deliberately: a sandbox whose first commit already contains
    ``[tool.molt]`` and ``.changeset/README.md`` starts with a clean working tree, and a clean
    tree is what makes the ``git diff`` after ``molt version`` show only what molt wrote.
    """
    git = shutil.which("git")
    if git is None:  # pragma: no cover - git_init already reported it
        return
    _run([git, "add", "-A"], cwd=root)
    _run([git, "commit", "-qm", message], cwd=root)


def molt_init(root: Path) -> None:
    """Run ``molt init --yes`` in ``root`` through this interpreter's own molt.

    ``python -m molt`` rather than the ``molt`` console script, so it works whether or not
    ``uv tool install`` has put ``molt`` on PATH.
    """
    result = subprocess.run(
        [sys.executable, "-m", "molt", "init", "--cwd", str(root), "--yes"],
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    for line in output.splitlines():
        print(f"    {line}")
    if result.returncode != 0:
        raise SystemExit(f"error: molt init failed in {root} (exit {result.returncode})")


# ======================================================================================
# Entry point
# ======================================================================================


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="make_sandbox.py",
        description="Build throwaway molt sandboxes under molt/tmp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run scripts/make_sandbox.py\n"
            "  uv run scripts/make_sandbox.py --only workspace --init\n"
        ),
    )
    parser.add_argument(
        "--only",
        choices=sorted(SANDBOXES),
        action="append",
        metavar="NAME",
        help=f"build one sandbox instead of all ({', '.join(sorted(SANDBOXES))}); repeatable",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"where the sandboxes go (default: {DEFAULT_DESTINATION})",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="skip `git init` and the initial commit (molt will refuse to run)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="also run `molt init --yes` in each sandbox",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"delete an existing target even without a {MARKER_NAME} marker file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    names = sorted(set(args.only)) if args.only else sorted(SANDBOXES)
    destination: Path = args.dest.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    built: list[Path] = []
    for name in names:
        description, build = SANDBOXES[name]
        root = destination / name
        print(f"[{name}] {description}")
        reset_directory(root, force=args.force)
        build(root)
        has_git = git_init(root) if not args.no_git else False
        if args.init:
            molt_init(root)
        if has_git:
            git_commit_all(root, "initial")
        print(f"    {root}")
        built.append(root)

    print()
    print("Next, from the package root:")
    for root in built:
        try:
            shown = root.relative_to(Path.cwd()).as_posix()
        except ValueError:
            shown = root.as_posix()
        print(f"    uv run molt init   --cwd {shown} --yes")
        print(f"    uv run molt status --cwd {shown} --verbose")
    print()
    print("Rebuild any sandbox at any time -- this script deletes and recreates it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
