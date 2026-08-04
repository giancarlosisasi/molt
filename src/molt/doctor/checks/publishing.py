"""Whether this machine could publish: the two tools molt shells out to, and a credential.

**The credential check never holds the secret.** It asks whether a name is present in the
environment and reports the name and a boolean; the value is never read into a local, never
formatted and never handed to a renderer. That is deliberately stronger than masking it: a mask is
the pattern that leaks, because somebody later prints an unmasked copy while debugging, and a
masked value still discloses its length. ``doctor``'s output is the artifact users paste into
public issue trackers, and molt's internal-error funnel already redacts the working directory for
the same reason.

**No network request is made unless the run opts in.** The index probe below imports an HTTP client
only inside the opted-in branch, so the default path does not even load one -- which also keeps
``doctor`` fast enough to actually run. Under the opt-in, every failure is a warning: a proxy, an
outage or an air-gapped machine is not a defect in the user's molt setup, and reporting it as one
would train users to ignore failures. ``doctor`` never asks whether a version is already published;
that is the publish planner's job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["CredentialsCheck", "IndexCheck", "ToolsCheck"]

#: The executables molt runs as subprocesses, and what each one is needed for.
_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("uv", "builds and uploads distributions", "https://docs.astral.sh/uv/getting-started/"),
    ("git", "reads history and writes release tags", "https://git-scm.com/downloads"),
)

#: How long the reachability probe may take. Short: a diagnostic that hangs is worse than one that
#: reports "could not be reached", and there is nothing here a longer wait would resolve.
_PROBE_TIMEOUT_SECONDS = 10.0


class ToolsCheck(CheckBase):
    """``uv`` and ``git`` on ``PATH``, with the version each one reports."""

    id: ClassVar[str] = "publish.tools"
    group: ClassVar[CheckGroup] = CheckGroup.PUBLISH

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        del ctx
        for name, purpose, url in _TOOLS:
            version = _tool_version(name)
            if version is None:
                yield self.fail(
                    name,
                    f"not found on PATH. molt {purpose}, so it cannot run without it.",
                    f"Install {name} ({url}) and make sure it is on PATH.",
                )
            else:
                yield self.ok(name, version)


class CredentialsCheck(CheckBase):
    """Whether an upload credential is available. Presence only -- never the value."""

    id: ClassVar[str] = "publish.credentials"
    group: ClassVar[CheckGroup] = CheckGroup.PUBLISH

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        import os

        from molt.publish import ACTIONS_ID_TOKEN_TOKEN, ACTIONS_ID_TOKEN_URL, TOKEN_ENVIRONMENT

        del ctx
        # Membership, not `os.environ.get`: the value is never bound to a name here, which is what
        # makes "no secret can reach the report" a property of the code rather than of a reviewer.
        present = [name for name in TOKEN_ENVIRONMENT if name in os.environ]
        if present:
            yield self.ok("token", f"set in {', '.join(present)}")
            return

        if ACTIONS_ID_TOKEN_URL in os.environ and ACTIONS_ID_TOKEN_TOKEN in os.environ:
            yield self.ok("token", "a Trusted Publishing identity is available in this environment")
            return

        yield self.warn(
            "token",
            "no upload credential was found, so `molt publish` would stop before uploading.",
            f"Set one of {', '.join(TOKEN_ENVIRONMENT)}, or run under a workflow with OIDC "
            "Trusted Publishing. Nothing else needs one.",
        )


class IndexCheck(CheckBase):
    """Whether the package index answers. Skipped unless the run opts in."""

    id: ClassVar[str] = "publish.index"
    group: ClassVar[CheckGroup] = CheckGroup.PUBLISH

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        from molt.publish import DEFAULT_REPOSITORY, index_origin

        origin = index_origin(DEFAULT_REPOSITORY)
        if not ctx.online:
            yield self.ok(
                "index",
                f"{origin} was not contacted; `molt doctor --online` checks that it answers",
            )
            return

        reached = _reachable(origin)
        if reached is None:
            yield self.ok("index", f"{origin} answered")
            return
        yield self.warn(
            "index",
            f"{origin} could not be reached: {reached}",
            "A proxy, an outage or an offline machine explains this; it is not a molt setting.",
        )


def _tool_version(name: str) -> str | None:
    """``<name> --version`` as one line, or ``None`` when the tool is absent or unusable."""
    import shutil
    import subprocess

    executable = shutil.which(name)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else executable


def _reachable(origin: str) -> str | None:
    """``None`` when ``origin`` answered, else why it did not.

    ``httpx`` is imported here rather than at module scope so the default, offline path never loads
    an HTTP client at all -- the requirement is "no network request", and not having a client in
    memory is how that stops being something to remember.
    """
    import httpx

    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=True) as client:
            client.head(origin)
    except httpx.HTTPError as error:
        return f"{type(error).__name__}: {error}"
    return None
