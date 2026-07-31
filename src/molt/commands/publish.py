"""`molt publish` -- Upload every unpublished distribution to the configured index.

Ports ``packages/cli/src/commands/publish/index.ts`` @ v3.0.0-next.9. Everything that decides or
uploads anything lives in :func:`molt.publish.publish`; this module is the shell over it -- resolve
the cwd, reconcile the two spellings of "which index", supply the git seam, report the outcome.
Website doc: ``website/docs/cli/publish.md``. The conformance suite is
``tests/publish/test_publish.py``.

**This is the one irreversible verb.** PyPI has no unpublish and a partial monorepo publish cannot
be rolled back, so the library validates the whole plan before the first upload and stops at the
first failed chunk. Nothing here weakens that: the command adds no retry, no "continue anyway", and
no prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.publish import PublishResult


def run(
    *,
    cwd: Path | None = None,
    filter: list[str] | None = None,
    repository: str | None = None,
    index_url: str | None = None,
    from_pack_dir: Path | str | None = None,
    git_tag: bool = True,
    output: str | None = None,
    dry_run: bool = False,
    console: Any = None,
    git: Any = None,
    builder: Any = None,
    uploader: Any = None,
    oidc: Any = None,
    **options: Any,
) -> PublishResult:
    """Build and upload the release, tagging what went out, and report what happened.

    ``--repository`` and ``--index-url`` are two spellings of one question -- which index is this
    release aimed at -- so an explicit ``--index-url`` wins and a bare ``--repository`` is used
    otherwise, exactly as ``molt publish-plan`` resolves them. Whichever is chosen routes **all
    three** uses of an index: the credential exchange, the upload, and the registry read that
    decides what to skip.

    ``filter`` shadows the builtin because the shell passes one keyword per long flag and the flag
    is ``--filter``; nothing in this module needs the builtin.

    Every heavy import happens inside this function: ``tests/cli/test_cli.py``'s import-light
    assertion lists ``molt.publish`` among the modules ``import molt.cli`` must not pull in.
    """
    del options  # `--non-interactive` is the shell's global; `publish` never prompts.

    from pathlib import Path as _Path

    from molt.ecosystem import find_workspace_root
    from molt.publish import publish

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(_Path(cwd) if cwd is not None else _Path.cwd())
    if git is None:
        from molt.git import Git

        git = Git(root)

    result = publish(
        cwd=root,
        from_pack_dir=from_pack_dir,
        output=output,
        repository=index_url or repository,
        filter=filter,
        git_tag=git_tag,
        dry_run=dry_run,
        console=console,
        git=git,
        builder=builder,
        uploader=uploader,
        oidc=oidc,
    )
    if not dry_run:
        console.info(_summary(result))
    return result


def _summary(result: PublishResult) -> str:
    """The closing line: what went out, and what the index already had.

    A skipped release is reported rather than swallowed. It means another publisher won a race, and
    an operator reading a green CI log needs to know that the version on the index is not
    necessarily the one this run built.
    """
    if not result.published and not result.skipped:
        return "Nothing to publish."
    lines = [f"Published {len(result.published)} release(s)."]
    lines.extend(f"  - {release}" for release in result.published)
    if result.skipped:
        lines.append(f"Skipped {len(result.skipped)} already on the index:")
        lines.extend(f"  - {release}" for release in result.skipped)
    return "\n".join(lines)
