"""molt's command-line application -- the Typer app, its group, and the program bootstrap.

The framework is **Typer** (owner's decision 2026-07-23, ``roadmap/tech-stack.md`` §4). Everything
Typer gives for free is used directly; the four behaviors it does not give are hand-written here and
nowhere else, so the accepted cost of that decision stays bounded and testable:

1. **Default command** -- bare ``molt`` (or ``molt --open``) runs ``add`` (research doc 03 §1.1).
2. **Deprecated alias** -- ``molt tag`` runs ``git-tag`` and says so (§1.6).
3. **Option normalization** -- last-wins scalars, always-list repeatables, numeric-as-string, the
   dropped ``--`` passthrough, the ``MOLT_OUTPUT`` back-fill (§1.3, :func:`normalize_options`).
4. **Optional-value ``--snapshot``** -- neither Typer nor Click binds a space-separated optional
   value, so the three accepted spellings are resolved before parsing (§11.1 R5, design D4).

(1), (2) and (4) live in :class:`MoltGroup.parse_args` -- deliberately **not** ``resolve_command``,
which the original design named: ``TyperGroup.invoke`` fails with ``Missing command.`` before
``resolve_command`` is reached for a bare ``molt``, and ``molt --open`` dies even earlier inside
``super().parse_args`` because ``TyperGroup`` sets ``allow_extra_args`` but not
``ignore_unknown_options``. See the amended design D1.

Commands are registered as thin wrappers that **lazily import** their implementation module inside
the body (design D7), mirroring the JS CLI's per-command dynamic ``import()``. ``import molt.cli``
therefore costs Typer and the console adapter, not the release-plan engine -- pinned by an
import-light assertion in ``tests/cli/test_cli.py``.
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
import traceback
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final
from urllib.parse import urlencode

import typer
import typer.core

from molt import __version__
from molt.errors import ExitError, InternalError, MoltError
from molt.ui import prompts
from molt.ui.console import console

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import ModuleType

    from typer._click.core import Context as ClickContext

__all__ = ["app", "main", "normalize_options"]


# ======================================================================================
# Constants
# ======================================================================================

#: U+1F40D SNAKE -- molt's mark. Written as an escape so this source file stays ASCII-only; the
#: console adapter degrades it on a legacy code page.
_BANNER_GLYPH: Final = "\U0001f40d"

#: Where a molt bug is reported. The internal-error funnel pre-fills an issue here.
_ISSUES_URL: Final = "https://github.com/giancarlosisasi/molt/issues/new"

#: CLI verb -> the ``molt.commands`` module that implements it. Dashes become underscores; the
#: mapping is explicit rather than derived so a rename shows up as a diff here.
_COMMAND_MODULES: Final[dict[str, str]] = {
    "init": "init",
    "add": "add",
    "version": "version",
    "status": "status",
    "doctor": "doctor",
    "publish": "publish",
    "publish-plan": "publish_plan",
    "build": "build",
    "yank": "yank",
    "git-tag": "git_tag",
}

#: Group-level options that must NOT be rewritten into the default command. Everything else that
#: starts with ``-`` and appears where a command name belongs is an ``add`` flag.
_GROUP_OPTIONS: Final = frozenset({"--help", "-h", "--version"})

#: Deprecated command spellings and their replacements (research doc 03 §1.6).
_ALIASES: Final[dict[str, str]] = {"tag": "git-tag"}

#: ``ctx.meta`` key holding the argument list as the group rewrote it, so the callback can tell a
#: matched command from a ``--help`` that is about to short-circuit.
_ARGS_META_KEY: Final = "molt.cli.args"

#: Typer maps ``KeyboardInterrupt`` to this POSIX code; molt's contract is 0 (research doc 03 §11.6
#: -- a deliberate, documented divergence, matching changesets).
_POSIX_SIGINT_EXIT: Final = 130


def _version() -> str:
    """The distribution version, for the banner and ``--version``."""
    return __version__


# ======================================================================================
# Option normalization (research doc 03 §1.3; design D3)
# ======================================================================================


def normalize_options(raw: Mapping[str, Any], *, array: Sequence[str] = ()) -> dict[str, Any]:
    """Apply changesets' ``normalizeOptions`` rules to a parsed option mapping.

    Ports ``packages/cli/src/index.ts:19-63`` as a pure function over a dict, so the rules stay
    unit-testable in isolation from Typer -- an end-to-end assertion only sees the *combination* of
    Click's parser and this pass, and a rule deleted here could hide behind a Click default.

    The six rules, in application order:

    1. Single-character keys are dropped -- ``-m`` and ``--message`` are one option, and only the
       long name reaches the command (``index.ts:28-31``).
    2. A repeated scalar takes the **last** value, silently (``index.ts:45-47``).
    3. A name listed in ``array`` always yields a list, even for one occurrence, and its members are
       stringified (``index.ts:34-43``).
    4. Numeric-looking values stay ``str``: version and tag names are text, and ``123`` must not
       become ``int`` (``index.ts:51-53``). Booleans are values, not numbers, and survive as-is.
    5. Everything after a bare ``--`` is dropped; molt has no passthrough args anywhere (§10.3).
    6. ``MOLT_OUTPUT`` back-fills ``--output`` when the flag is absent (``withEnvOptions``,
       ``index.ts:59-63``, with ``CHANGESETS_OUTPUT`` renamed).

    An option whose value is ``None`` is *removed* rather than passed through, so a command's own
    default wins over "the flag was not given". An empty string survives: ``-m ''`` is an explicit
    empty summary, not an absent one (§10.15).
    """
    array_names = set(array)
    normalized: dict[str, Any] = {}

    for key, value in raw.items():
        if key == "--" or len(key) == 1:  # rules 5 and 1
            continue
        if value is None:
            continue
        if key in array_names:  # rule 3
            members = list(value) if isinstance(value, (list, tuple)) else [value]
            if not members:
                continue
            normalized[key] = [str(member) for member in members]
            continue
        if isinstance(value, (list, tuple)):  # rule 2
            if not value:
                continue
            value = value[-1]
        normalized[key] = _as_scalar(value)

    if normalized.get("output") is None:  # rule 6
        from_env = os.environ.get("MOLT_OUTPUT")
        if from_env is not None:
            normalized["output"] = from_env

    return normalized


def _as_scalar(value: Any) -> Any:
    """Rule 4: numbers become strings, booleans stay booleans, everything else is untouched."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return value


# ======================================================================================
# The group -- default command, deprecated alias, `--` drop, optional-value `--snapshot`
# ======================================================================================


def _drop_passthrough(args: Sequence[str]) -> list[str]:
    """Rule 5: truncate at the first bare ``--``.

    Click would otherwise hand the remainder to the command as positional arguments (or fail with
    "got unexpected extra arguments"), and molt has no passthrough feature to hand them to.
    """
    argv = list(args)
    return argv[: argv.index("--")] if "--" in argv else argv


def _resolve_snapshot_spellings(args: list[str]) -> list[str]:
    """Rewrite ``--snapshot=<name>`` and reject the space form (design D4).

    Click can express "flag with an optional value", but its parser still swallows the *next*
    token when that token does not look like an option -- so ``--snapshot pr-123`` would bind
    silently, which is exactly the ambiguity the accepted spec says to reject. Resolving the three
    spellings here keeps the ``version`` command's signature to two plain options.
    """
    resolved: list[str] = []
    for index, token in enumerate(args):
        if token == "--snapshot":
            following = args[index + 1] if index + 1 < len(args) else None
            if following is not None and not following.startswith("-"):
                console.error(
                    f"`--snapshot` does not take a space-separated value. "
                    f"Did you mean `--snapshot={following}` or `--snapshot-name {following}`?"
                )
                raise typer.Exit(2)
            resolved.append(token)
        elif token.startswith("--snapshot="):
            resolved.extend(["--snapshot-name", token.split("=", 1)[1]])
        else:
            resolved.append(token)
    return resolved


class MoltGroup(typer.core.TyperGroup):
    """The Typer group that carries molt's command-resolution rules.

    Subclasses ``TyperGroup`` rather than a bare Click group so Typer's rich help rendering
    survives, and overrides ``parse_args`` rather than ``resolve_command`` because the latter is
    unreachable for the two cases it was supposed to handle (design D1, amended).
    """

    # `ctx` is the *vendored* Click context, not the public `typer.Context`: Typer 0.20+ ships its
    # own copy of Click, and `Command.context_class` resolves to `typer._click.core.Context`.
    # Annotating the public class would be both an override violation and untrue at runtime. The
    # import is TYPE_CHECKING-only, so a Typer bump that moves it fails type-checking rather than
    # the CLI.
    def parse_args(self, ctx: ClickContext, args: list[str]) -> list[str]:
        if ctx.resilient_parsing:
            # Shell completion parses speculatively; rewriting argv (or warning) here would
            # corrupt the candidate list and print to the user's prompt.
            return super().parse_args(ctx, args)

        args = _drop_passthrough(args)

        if args and args[0] in _ALIASES:
            # The alias branch runs FIRST, and the `elif` guarantees an aliased first token is
            # never also treated as a leading option: `molt tag --output x` must reach `git-tag`
            # carrying its option, not become `add --output x`.
            replacement = _ALIASES[args[0]]
            console.warn(
                f"The `{args[0]}` command is deprecated. Please use `{replacement}` instead."
            )
            args = [replacement, *args[1:]]
        elif not args or (args[0].startswith("-") and args[0] not in _GROUP_OPTIONS):
            args = [_DEFAULT_COMMAND, *args]

        if args and args[0] == "version":
            args = _resolve_snapshot_spellings(args)

        ctx.meta[_ARGS_META_KEY] = list(args)
        return super().parse_args(ctx, args)


#: Bare ``molt`` is ``molt add`` (``cli.ts:78``; research doc 03 §10.26).
_DEFAULT_COMMAND: Final = "add"


# ======================================================================================
# The application
# ======================================================================================

app = typer.Typer(
    cls=MoltGroup,
    name="molt",
    help="Changeset-driven versioning and changelogs for Python monorepos.",
    no_args_is_help=False,  # bare `molt` is `molt add`, not a help dump
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> None:
    """Eager ``--version``: print the bare version and exit before the banner can run (design D2).

    Written with ``typer.echo`` rather than the console adapter on purpose. This is a
    machine-readable payload -- the string a script pipes into a comparison -- so it belongs on
    stdout with no level label, exactly like the ``--output json`` document. Every *human* byte
    still goes through the ``Console`` protocol.
    """
    if value:
        typer.echo(_version())
        raise typer.Exit(0)


@app.callback()
def molt(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the molt version and exit.",
        ),
    ] = False,
) -> None:
    """Changeset-driven versioning and changelogs for Python monorepos."""
    del version  # consumed by the eager callback above
    if _wants_help(ctx.meta.get(_ARGS_META_KEY, ())):
        # Click resolves `--help` inside the *subcommand's* context, which Typer builds after this
        # callback has already run -- so `molt status --help` would otherwise print the banner
        # above its own help. No command body runs, so no banner (cli-shell spec).
        return
    console.info(f"{_BANNER_GLYPH} molt v{_version()}")


def _wants_help(args: Sequence[str]) -> bool:
    """True when the invocation will short-circuit into help instead of running a command body."""
    return any(arg in ("-h", "--help") for arg in args)


# ======================================================================================
# Shared option types (overview.md:41-48 -- the globals every command carries)
# ======================================================================================

CwdOption = Annotated[
    Path | None,
    typer.Option("--cwd", help="Directory to run in. Defaults to the current directory."),
]
NonInteractiveOption = Annotated[
    bool,
    typer.Option(
        "--non-interactive",
        "--yes",
        help="Never prompt: use each prompt's documented default, or fail naming the input.",
    ),
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print what would happen and write nothing.")
]
OutputOption = Annotated[
    str | None,
    typer.Option("--output", help="Write the machine-readable output here (or `json` for stdout)."),
]


class Bump(StrEnum):
    """The three release types a changeset can request (``website/docs/cli/add.md``)."""

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


class Pre(StrEnum):
    """PEP 440 pre-release phases. molt has no ``pre`` mode -- ``--pre`` is a stateless flag.

    changesets' ``pre enter next`` produced ``1.0.1-next.0``, which is not a legal PEP 440 version;
    accepting a free-form tag here would resurrect the shape molt exists to avoid (research README
    §4.2).
    """

    A = "a"
    B = "b"
    RC = "rc"
    DEV = "dev"


# ======================================================================================
# Dispatch -- one place where options are normalized and the implementation is imported
# ======================================================================================


def _load_command(command: str) -> ModuleType:
    """Import a command's implementation module. Lazy by contract (design D7)."""
    name = f"molt.commands.{_COMMAND_MODULES[command]}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            # The module exists but one of *its* imports is missing -- a real failure, not a
            # not-implemented-yet command. Let the error funnel report it.
            raise
        console.error(f"`molt {command}` is not implemented yet.")
        raise typer.Exit(1) from exc


def _dispatch(
    command: str,
    raw: Mapping[str, Any],
    *,
    array: Sequence[str] = (),
    cwd: Path | None,
) -> None:
    """Normalize ``raw``, then hand it to the command's ``run`` as keyword arguments.

    ``raw`` names every option the command declares, including the ones left unset: that is what
    makes the ``MOLT_OUTPUT`` back-fill land on the four commands that have ``--output`` and on no
    others -- :func:`normalize_options` adds the key unconditionally, and the projection below
    drops it again for a command that never asked for one.

    The implementation module is looked up by attribute at call time (``module.run``), never bound
    at import: that is what lets a test replace it, and what keeps the import lazy.
    """
    prompts.set_non_interactive(bool(raw.get("non_interactive")))
    options = normalize_options(raw, array=array)
    options = {key: value for key, value in options.items() if key in raw}
    module = _load_command(command)
    module.run(cwd=cwd if cwd is not None else Path.cwd(), **options)


# ======================================================================================
# The command table (cli-shell spec, "Command surface registration"; research doc 03 §1.4)
# ======================================================================================


@app.command()
def init(cwd: CwdOption = None, non_interactive: NonInteractiveOption = False) -> None:
    """Scaffold `.changeset/` and molt's configuration in the workspace root."""
    _dispatch("init", {"non_interactive": non_interactive}, cwd=cwd)


@app.command()
def add(
    empty: Annotated[
        bool, typer.Option("--empty", help="Write a changeset that releases nothing.")
    ] = False,
    # `open` shadows the builtin inside this signature only. The keyword the command receives is
    # part of the CLI contract (`--open` -> `open=`), so it is not ours to rename.
    open: Annotated[
        bool, typer.Option("--open", help="Open the new changeset in $EDITOR.")
    ] = False,
    since: Annotated[
        list[str] | None, typer.Option("--since", help="Detect changed packages since this ref.")
    ] = None,
    message: Annotated[
        str | None, typer.Option("--message", "-m", help="The changeset summary.")
    ] = None,
    major: Annotated[
        list[str] | None,
        typer.Option("--major", help="Release this package as a major. Repeatable."),
    ] = None,
    minor: Annotated[
        list[str] | None,
        typer.Option("--minor", help="Release this package as a minor. Repeatable."),
    ] = None,
    patch: Annotated[
        list[str] | None,
        typer.Option("--patch", help="Release this package as a patch. Repeatable."),
    ] = None,
    package: Annotated[
        list[str] | None,
        typer.Option("--package", "-p", help="Select a package without prompting. Repeatable."),
    ] = None,
    bump: Annotated[
        Bump | None, typer.Option("--bump", help="Release type for every --package selection.")
    ] = None,
    stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the changeset summary from stdin.")
    ] = False,
    dry_run: DryRunOption = False,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Create a changeset describing the releases your change needs."""
    _dispatch(
        "add",
        {
            "empty": empty,
            "open": open,
            "since": since,
            "message": message,
            "major": major,
            "minor": minor,
            "patch": patch,
            "package": package,
            "bump": None if bump is None else bump.value,
            "stdin": stdin,
            "dry_run": dry_run,
            "non_interactive": non_interactive,
        },
        array=("major", "minor", "patch", "package"),
        cwd=cwd,
    )


@app.command()
def version(
    ignore: Annotated[
        list[str] | None, typer.Option("--ignore", help="Do not release this package. Repeatable.")
    ] = None,
    snapshot: Annotated[
        bool, typer.Option("--snapshot", help="Release an unnamed snapshot version.")
    ] = False,
    snapshot_name: Annotated[
        str | None, typer.Option("--snapshot-name", help="Release a snapshot named NAME.")
    ] = None,
    pre: Annotated[
        Pre | None, typer.Option("--pre", help="Release a PEP 440 pre-release of this phase.")
    ] = None,
    dry_run: DryRunOption = False,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Consume changesets: bump versions, propagate to dependents, write changelogs."""
    _dispatch(
        "version",
        {
            "ignore": ignore,
            # The three accepted spellings collapse into one keyword (`True` unnamed, `str` named,
            # `None` absent), mirroring upstream's `snapshot?: string | boolean`. Handing the
            # command both `snapshot` and `snapshot_name` would make it rebuild this itself.
            "snapshot": snapshot_name if snapshot_name is not None else (snapshot or None),
            "pre": None if pre is None else pre.value,
            "dry_run": dry_run,
            "non_interactive": non_interactive,
        },
        array=("ignore",),
        cwd=cwd,
    )


@app.command()
def status(
    since: Annotated[
        list[str] | None, typer.Option("--since", help="Compare against this git ref.")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Print the full release plan.")
    ] = False,
    # `-o` is upstream's alias (cli.ts:117) and the one the ported routing row types; the docs
    # page lists only the long form, which is a docs gap rather than a behavior difference.
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Print the plan to stdout as `json`, the only value."),
    ] = None,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Report the pending release plan. Exits 1 as a CI gate when a changeset is missing."""
    _dispatch(
        "status",
        {
            "since": since,
            "verbose": verbose,
            "output": output,
            "non_interactive": non_interactive,
        },
        cwd=cwd,
    )


@app.command()
def doctor(
    online: Annotated[
        bool,
        typer.Option("--online", help="Also check that the package index answers. Off by default."),
    ] = False,
    output: OutputOption = None,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Check whether this workspace is set up correctly, and what a release would skip.

    Read-only, like `yank`: it reports and exits, so every run is already a preview and there is no
    write to guard. It is also the one command that does not refuse an uninitialized project --
    being unsure whether the project is set up is why somebody runs it.
    """
    _dispatch(
        "doctor",
        {"online": online, "output": output, "non_interactive": non_interactive},
        cwd=cwd,
    )


@app.command()
def publish(
    # `filter` shadows the builtin inside this signature only -- see the note on `add`'s `open`.
    filter: Annotated[
        list[str] | None, typer.Option("--filter", help="Only publish matching packages.")
    ] = None,
    repository: Annotated[
        str | None, typer.Option("--repository", help="Named index from your uv/pip config.")
    ] = None,
    index_url: Annotated[
        str | None, typer.Option("--index-url", help="Upload URL of the index to publish to.")
    ] = None,
    from_pack_dir: Annotated[
        Path | None,
        typer.Option("--from-pack-dir", help="Publish the distributions already built here."),
    ] = None,
    git_tag: Annotated[
        bool, typer.Option("--git-tag/--no-git-tag", help="Create git tags for what was published.")
    ] = True,
    output: OutputOption = None,
    dry_run: DryRunOption = False,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Upload every unpublished distribution to the configured index."""
    _dispatch(
        "publish",
        {
            "filter": filter,
            "repository": repository,
            "index_url": index_url,
            "from_pack_dir": from_pack_dir,
            "git_tag": git_tag,
            "output": output,
            "dry_run": dry_run,
            "non_interactive": non_interactive,
        },
        array=("filter",),
        cwd=cwd,
    )


@app.command("publish-plan")
def publish_plan(
    filter: Annotated[
        list[str] | None, typer.Option("--filter", help="Only consider matching packages.")
    ] = None,
    repository: Annotated[
        str | None, typer.Option("--repository", help="Named index from your uv/pip config.")
    ] = None,
    index_url: Annotated[
        str | None, typer.Option("--index-url", help="URL of the index to check against.")
    ] = None,
    output: OutputOption = None,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Emit the machine-readable plan of what `publish` would upload, without uploading."""
    _dispatch(
        "publish-plan",
        {
            "filter": filter,
            "repository": repository,
            "index_url": index_url,
            "output": output,
            "non_interactive": non_interactive,
        },
        array=("filter",),
        cwd=cwd,
    )


@app.command()
def build(
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="Write the built distributions here.")
    ] = None,
    from_publish_plan: Annotated[
        Path | None,
        typer.Option("--from-publish-plan", help="Build only what this publish plan lists."),
    ] = None,
    dry_run: DryRunOption = False,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Build sdists and wheels for the distributions a release covers.

    Named `build` rather than changesets' `pack`, with no `pack` alias: `build` is what the Python
    packaging ecosystem calls this stage (research doc 03 §11.5).
    """
    _dispatch(
        "build",
        {
            "out_dir": out_dir,
            "from_publish_plan": from_publish_plan,
            "dry_run": dry_run,
            "non_interactive": non_interactive,
        },
        cwd=cwd,
    )


@app.command("git-tag")
def git_tag(
    output: OutputOption = None,
    dry_run: DryRunOption = False,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Create the git tags for the versions the last `version` run produced."""
    _dispatch(
        "git-tag",
        {"output": output, "dry_run": dry_run, "non_interactive": non_interactive},
        cwd=cwd,
    )


@app.command()
def yank(
    package: Annotated[str, typer.Argument(help="The package whose release to yank.")],
    version: Annotated[str, typer.Argument(help="The released version to yank.")],
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why the release is being yanked.")
    ] = None,
    undo: Annotated[bool, typer.Option("--undo", help="Report how to un-yank instead.")] = False,
    repository: Annotated[
        str | None, typer.Option("--repository", help="The index the version lives on.")
    ] = None,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Report how to yank a released version (PEP 592).

    Read-only and advisory: PyPI exposes no yank API, so molt reads the index and prints the
    browser steps. Every run is therefore already a dry run, which is why this is the one mutating-
    sounding command with no `--dry-run` (`website/docs/cli/yank.md`).
    """
    _dispatch(
        "yank",
        {
            "package": package,
            "version": version,
            "reason": reason,
            "undo": undo,
            "repository": repository,
            "non_interactive": non_interactive,
        },
        cwd=cwd,
    )


@app.command()
def pre(
    args: Annotated[
        list[str] | None,
        typer.Argument(metavar="[enter|exit] [TAG]", help="Accepted only to report the migration."),
    ] = None,
    cwd: CwdOption = None,
    non_interactive: NonInteractiveOption = False,
) -> None:
    """Not supported: use `molt version --pre {a,b,rc,dev}` instead.

    changesets keeps pre-release state in a `pre.json` and produces `-next.N` versions, which are
    not legal PEP 440. molt drops the mode entirely and makes `--pre` a stateless flag on `version`
    (research README §4.2). The command stays registered so the migration message has somewhere to
    live -- it is the one command that will never grow an implementation.
    """
    del args, cwd, non_interactive
    console.error(
        "`molt pre` is not supported: molt has no pre mode to enter or exit. "
        "Use `molt version --pre {a,b,rc,dev}` to cut a PEP 440 pre-release instead."
    )
    raise typer.Exit(1)


# ======================================================================================
# Bootstrap -- the error funnel and the exit-code contract (design D5; research doc 03 §1.2, §11.6)
# ======================================================================================


def _redact_cwd(text: str) -> str:
    """Replace the working directory with ``<cwd>``.

    The internal-error report invites the user to paste its URL into a public issue tracker, so an
    absolute path -- which routinely carries a username or a client name -- must not survive into it
    (``index.ts:27-49``).
    """
    return text.replace(str(Path.cwd()), "<cwd>")


def _issue_url(message: str) -> str:
    """A pre-filled "new issue" URL carrying the CLI and runtime versions."""
    body = (
        f"molt version: {_version()}\n"
        f"Python: {platform.python_version()}\n"
        f"Platform: {platform.platform()}\n\n"
        f"Error: {message}\n"
    )
    return f"{_ISSUES_URL}?{urlencode({'title': f'Internal error: {message}', 'body': body})}"


def _report_internal_error(error: InternalError) -> None:
    """An internal error is a molt bug: no traceback, a redacted report, and an invitation."""
    message = _redact_cwd(str(error))
    console.error(message)
    console.note(
        "This is a bug in molt, not a problem with your project.",
        f"Please report it: {_issue_url(message)}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code. The ``molt`` console script entry point.

    Typer is driven in **standalone mode** so usage errors keep their rich rendering, and the
    resulting ``SystemExit`` is caught rather than allowed to escape: ``main`` returns an integer
    and never lets an exception reach the interpreter (``cli-shell`` spec).

    The exit-code contract (research doc 03 §11.6, ``website/docs/cli/overview.md``): success 0;
    Ctrl-C 0; a user-facing exit error its own code; a validation failure 1; an internal error 1;
    anything unexpected 1 with a traceback.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    command = typer.main.get_command(app)

    try:
        command.main(args=args, prog_name="molt", standalone_mode=True)
    except SystemExit as exit_request:
        code = exit_request.code
        code = 0 if code is None else code if isinstance(code, int) else 1
        if code == _POSIX_SIGINT_EXIT:
            # Typer turns a KeyboardInterrupt into Exit(130) before the funnel can see it. molt's
            # contract is 0 -- and "Canceled" is part of it, because an exit 0 with no message is
            # indistinguishable from success (§10.5: CI reads a cancelled `add` as a pass).
            console.info("Canceled")
            return 0
        return code
    except KeyboardInterrupt:
        console.info("Canceled")
        return 0
    except ExitError as error:
        # A child process exited non-zero and molt is propagating its status verbatim.
        console.error(str(error))
        return error.code
    except InternalError as error:
        _report_internal_error(error)
        return 1
    except MoltError as error:
        # An expected, molt-raised failure: a sentence, not a stack.
        console.error(str(error))
        return 1
    # A blanket `except Exception` is the funnel's entire job: catching what nothing else did.
    except Exception:
        console.error(traceback.format_exc())
        console.error("Exited with code 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
