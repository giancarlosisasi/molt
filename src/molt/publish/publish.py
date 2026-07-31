"""The upload stage -- the one irreversible thing molt does.

Ports ``packages/cli/src/commands/publish/index.ts`` and ``publishPackages.ts`` @ v3.0.0-next.9
against the behaviour catalogued in ``roadmap/research/test-suite/07-publish-pack.md``. Website doc:
``website/docs/cli/publish.md``. The conformance suite is ``tests/publish/test_publish.py``.

Two upstream subsystems are deleted rather than ported (research README section 4.4). The
**OTP / AuthState / interactive-retry** machine, with its TTY-gated concurrency and ``EOTP``
re-queuing, has nothing to do on PyPI: there is no 2FA at publish time, so there is nothing to
prompt for mid-run and no reason for a publish to ever read stdin. And **dist-tags**, including the
``only-pre`` heuristic, do not exist on PyPI at all. Neither is deferred; both are gone, and
``tests/publish/test_deliberately_not_ported.py`` keeps the record.

Four rules that look arbitrary and are not
------------------------------------------
**The whole plan is validated before the first upload** (design D1). changesets discovers a bad
package when its loop reaches it, which is survivable on npm because ``npm unpublish`` exists for 72
hours. PyPI has no unpublish and no overwrite, so a partial monorepo publish cannot be walked back:
molt therefore checks every name, every artifact, every digest and the credentials **first**, and
the run either starts clean or does not start.

**A duplicate is a conjunction of the status code AND the message** (design D2,
``npm-utils.ts:339-354``). ``POST upload.pypi.org/legacy/`` answers **400 for every rejected
upload** -- an unrecognised classifier, a disallowed project name, an oversize file. Classifying on
the status alone reports a genuinely rejected release as "skipped", exits 0, and leaves it silently
missing from a release nobody can walk back. See :func:`is_duplicate_upload_error`.

**A failed chunk stops the run and keeps what already succeeded** (design D3). Rolling back is
impossible and pressing on compounds the damage, so earlier chunks stay published and tagged, the
failing chunk's successes are tagged, and every later chunk is abandoned.

**``--repository`` routes all three uses of an index** (design D4): the credential exchange, the
upload, and the *registry read* that decides what to skip. Routing two of the three is how a
snapshot ends up on pypi.org, or how a release already on a private index gets re-uploaded because
the skip decision was taken against a project of the same name on pypi.org.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from molt.errors import MoltError
from molt.events import git_tag_event, write_ndjson
from molt.publish.plan import PLAN_FILENAME, tag_name

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from molt.ecosystem import Workspace
    from molt.publish.plan import Plan, PlanEntry

__all__ = [
    "DUPLICATE_MARKERS",
    "DUPLICATE_STATUS",
    "PublishResult",
    "ReleaseRef",
    "TrustedPublishingOIDC",
    "UvUploader",
    "is_duplicate_upload_error",
    "publish",
]

#: The HTTP status PyPI answers with when an upload is rejected -- for **any** reason, which is
#: exactly why it is only half of :func:`is_duplicate_upload_error`. npm's analogue is 403.
DUPLICATE_STATUS = 400

#: The message fragments that identify a rejection as "this version is already there", matched
#: case-insensitively. The first is PyPI's own wording; the second is upstream's
#: (``npm-utils.ts:344``) and is kept so an uploader that proxies an npm-shaped error is still
#: classified correctly. Anything else answered with a 400 is a release that did **not** go out.
DUPLICATE_MARKERS = (
    "file already exists",
    "cannot publish over the previously published version",
    "already been uploaded",
)

#: The two ``kind`` values a plan entry may carry. ``read_publish_plan`` already refuses anything
#: else, so these are read here rather than re-validated.
_PUBLISH = "publish"
_TAG_ONLY = "tag-only"

#: Environment variables that hold an index API token, in precedence order. ``UV_PUBLISH_TOKEN`` and
#: ``TWINE_PASSWORD`` are the two spellings a Python project is likely to already have set.
_TOKEN_ENVIRONMENT = ("MOLT_PUBLISH_TOKEN", "UV_PUBLISH_TOKEN", "TWINE_PASSWORD")

#: GitHub Actions' OIDC endpoints, supplied to the job as environment variables when the workflow
#: requests ``id-token: write``. Their absence means "not running under Trusted Publishing".
_ACTIONS_ID_TOKEN_URL = "ACTIONS_ID_TOKEN_REQUEST_URL"
_ACTIONS_ID_TOKEN_TOKEN = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"

#: Where an OIDC identity is exchanged for a short-lived upload token, per index, with the audience
#: that index expects. Hardcoded rather than read from ``/_/oidc/audience`` so the exchange is one
#: round trip and cannot half-fail on a discovery request.
_MINT_ENDPOINTS: dict[str, tuple[str, str]] = {
    "pypi": ("https://pypi.org/_/oidc/mint-token", "pypi"),
    "testpypi": ("https://test.pypi.org/_/oidc/mint-token", "testpypi"),
}

#: How long the token exchange may take. Short: a hung credential exchange in CI is a job that never
#: finishes, and there is nothing to retry that a re-run would not do better.
_OIDC_TIMEOUT_SECONDS = 30.0

#: How long one release's upload may take. Generous -- a large wheel over a slow link is normal --
#: but bounded, because an upload that never returns leaves the operator unable to tell whether the
#: version was spent.
_UPLOAD_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class ReleaseRef:
    """One release, named the way the index names it: distribution name plus version."""

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class PublishResult:
    """What a publish run did -- returned so a caller need not re-read the event stream.

    ``skipped`` is the releases the index rejected as duplicates: a stale plan racing another
    publisher, which is a normal outcome and not a failure. ``tags`` is what was actually created,
    so ``git_tag=False`` leaves it empty while ``published`` is not.
    """

    published: tuple[ReleaseRef, ...] = ()
    skipped: tuple[ReleaseRef, ...] = ()
    tags: tuple[str, ...] = ()
    events: tuple[dict[str, Any], ...] = ()


def is_duplicate_upload_error(error: BaseException) -> bool:
    """Whether ``error`` means "that version is already on the index" (design D2).

    A **conjunction**, faithfully: the rejection must carry :data:`DUPLICATE_STATUS` *and* say so
    in its message. Upstream's ``isDuplicatePublishError`` is built the same way and for the same
    reason, except that on PyPI the status half is far weaker than on npm -- ``POST
    upload.pypi.org/legacy/`` answers 400 for an invalid classifier, a project name too close to an
    existing one, and a file over the size limit, none of which is a release that went out.

    The status is read as an attribute rather than an exception type on purpose: the uploader is an
    injected seam, so the exception class belongs to whoever implements it and molt cannot import
    it. An error with no ``status_code`` at all is **not** a duplicate -- the failure direction here
    is deliberately the safe one, because a false "skip" silently drops a release.
    """
    status = getattr(error, "status_code", None)
    if status != DUPLICATE_STATUS:
        return False
    message = str(error).lower()
    return any(marker in message for marker in DUPLICATE_MARKERS)


def publish(
    *,
    cwd: Path,
    from_pack_dir: Path | str | None = None,
    output: Path | str | None = None,
    repository: str | None = None,
    filter: Sequence[str] | None = None,
    git_tag: bool = True,
    dry_run: bool = False,
    console: Any = None,
    git: Any = None,
    builder: Any = None,
    uploader: Any = None,
    oidc: Any = None,
) -> PublishResult:
    """Upload every release the plan lists, in dependency order, and tag what went out.

    Without ``from_pack_dir`` this runs all three pipeline stages in one process -- compute the
    plan, build the artifacts, upload them. With it, the plan and the artifacts are read from a
    directory a previous ``molt build`` wrote, which is what lets CI build on one machine and upload
    from a credentialed other one. **The plan file is always read through**
    :func:`molt.publish.read_publish_plan`: the envelope-version guard lives in the reader (design
    D6 of the plan change), so a foreign document cannot reach an irreversible upload by way of a
    bare ``json.loads`` here.

    ``console``, ``git``, ``builder``, ``uploader`` and ``oidc`` are injectable seams, following the
    convention the CLI and publish suites froze. There is deliberately **no prompts seam**: PyPI has
    no publish-time OTP, so nothing in this flow has anything to ask (design D5), and
    ``tests/publish/test_publish.py::test_publish_never_prompts_for_an_otp_or_a_token`` poisons
    stdin to keep it that way.

    ``filter`` shadows the builtin for the same reason it does in
    :func:`molt.publish.build_publish_plan`: the shell passes one keyword per long flag.

    Raises :class:`molt.errors.MoltError` if pre-flight fails or a chunk fails to upload. Earlier
    chunks stay published and tagged in that case -- there is no rollback on an immutable index, so
    the honest outcome is a partial release the operator can see, not a pretence of atomicity.
    """
    from pathlib import Path as _Path

    from molt.ecosystem import discover_workspace, find_workspace_root
    from molt.publish.plan import _resolve_config

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(_Path(cwd))
    config = _resolve_config(root, console=console)
    workspace = discover_workspace(root, config.ecosystem)

    if git is None:
        from molt.git import Git

        git = Git(root)

    events: list[dict[str, Any]] = []
    try:
        return _publish(
            root=root,
            workspace=workspace,
            from_pack_dir=None if from_pack_dir is None else _Path(from_pack_dir),
            repository=repository,
            filter=filter,
            git_tag=git_tag,
            dry_run=dry_run,
            console=console,
            git=git,
            builder=builder,
            uploader=uploader,
            oidc=oidc,
            events=events,
        )
    finally:
        # Unconditional, and in a `finally` on purpose (design D5 of `git-tag`, widened): the job
        # downstream reads this file without checking whether it exists, and a run that published
        # two chunks and then failed still has to report the two tags it created.
        if output is not None:
            write_ndjson(output, events)


def _publish(
    *,
    root: Path,
    workspace: Workspace,
    from_pack_dir: Path | None,
    repository: str | None,
    filter: Sequence[str] | None,
    git_tag: bool,
    dry_run: bool,
    console: Any,
    git: Any,
    builder: Any,
    uploader: Any,
    oidc: Any,
    events: list[dict[str, Any]],
) -> PublishResult:
    """The body of :func:`publish`, split out so the events can be flushed in a ``finally``."""
    import contextlib
    import tempfile
    from pathlib import Path as _Path

    from molt.publish.plan import build_publish_plan, read_publish_plan, write_publish_plan

    with contextlib.ExitStack() as stack:
        if from_pack_dir is not None:
            plan = read_publish_plan(from_pack_dir / PLAN_FILENAME)
            artifacts_root = from_pack_dir
            if dry_run:
                return _report_dry_run(plan, console)
            token = _resolve_credentials(plan, oidc=oidc, repository=repository)
        else:
            plan = build_publish_plan(
                cwd=root,
                console=console,
                git=git,
                repository=repository,
                filter=None if filter is None else list(filter),
            )
            if dry_run:
                return _report_dry_run(plan, console)
            # Before the build, not after it: a monorepo build is minutes of work, and a run with
            # no credential could never have finished it. Design D1 requires the credential check
            # to precede the first *upload*; putting it first costs nothing and fails faster.
            token = _resolve_credentials(plan, oidc=oidc, repository=repository)
            work = _Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="molt-publish-", ignore_cleanup_errors=True)
                )
            )
            artifacts_root = work / "dist"
            # The plan goes to disk and comes back through the reader, so the compute path gets the
            # same envelope guard the `--from-pack-dir` path does rather than a private shortcut.
            from molt.pack import pack

            source = write_publish_plan(work / "plan" / PLAN_FILENAME, plan)
            plan = pack(
                cwd=root,
                out_dir=artifacts_root,
                from_publish_plan=source,
                console=console,
                builder=builder,
            )

        # Design D1: everything that can be checked is checked before the first irreversible byte.
        # The artifacts can only be checked once they exist, so this is the last gate, not the only
        # one -- the credential was resolved above, and the envelope by the reader.
        _validate_plan(plan, workspace=workspace, artifacts_root=artifacts_root)
        if uploader is None:
            uploader = UvUploader(token=token)

        return _upload_plan(
            plan,
            artifacts_root=artifacts_root,
            repository=repository,
            git_tag=git_tag,
            single_package=workspace.backend == "single",
            console=console,
            git=git,
            uploader=uploader,
            events=events,
        )


# ======================================================================================
# Pre-flight -- design D1
# ======================================================================================


def _validate_plan(plan: Plan, *, workspace: Workspace, artifacts_root: Path) -> None:
    """Check every release in the plan, and report **all** the problems at once.

    Names first (a plan entry naming a distribution this workspace does not contain is upstream's
    ``sortReleases:219-223``), then the artifacts each publish entry claims: present on disk, and
    still the bytes whose digest the plan recorded. A rebuilt artifact with a different digest is
    not the artifact that was reviewed.

    Reporting the whole set matters more here than anywhere else in molt: the operator is deciding
    whether to start something that cannot be stopped, and finding the second problem after the
    first upload is exactly the failure this function exists to prevent.
    """
    from molt.pack import integrity_of

    problems: list[str] = []
    for chunk in plan:
        for entry in chunk:
            name = str(entry["name"])
            version = str(entry["version"])
            if workspace.get(name) is None:
                problems.append(
                    f"{name} {version}: the plan names a distribution this workspace does not "
                    "contain. The plan and the checkout disagree, so molt cannot tell what it "
                    "would be uploading."
                )
                continue
            if entry.get("kind") != _PUBLISH:
                continue
            artifacts = entry.get("artifacts")
            if not artifacts:
                problems.append(
                    f"{name} {version}: the plan lists no built artifacts. Run `molt build` "
                    "against this plan first, or publish without `--from-pack-dir`."
                )
                continue
            for artifact in artifacts:
                path = artifacts_root / str(artifact["path"])
                if not path.is_file():
                    problems.append(f"{name} {version}: {artifact['path']} is not on disk.")
                elif integrity_of(path) != artifact["integrity"]:
                    problems.append(
                        f"{name} {version}: {artifact['path']} does not match the digest the plan "
                        "recorded. These are not the bytes that were built."
                    )
    if problems:
        raise MoltError(
            "Refusing to publish: the plan did not pass pre-flight validation.\n  - "
            + "\n  - ".join(problems)
            + "\nNothing was uploaded. PyPI has no unpublish, so molt validates the whole plan "
            "before the first upload rather than discovering this halfway through one."
        )


def _resolve_credentials(plan: Plan, *, oidc: Any, repository: str | None) -> str | None:
    """Obtain the upload token **before** the first upload, or fail (design D1).

    Skipped when the plan uploads nothing: a run that only creates tags for private releases has no
    index to authenticate against, and demanding a credential for it would make tagging impossible
    on a machine that legitimately holds none.

    The token is fetched exactly once for the whole run, against the index ``--repository`` names
    (design D4). Nothing here prompts; a missing identity is a failure, not a question (design D5).
    """
    if not any(entry.get("kind") == _PUBLISH for chunk in plan for entry in chunk):
        return None
    if oidc is None:
        oidc = TrustedPublishingOIDC()
    try:
        return oidc.fetch_token(repository=repository)
    except MoltError:
        raise
    except Exception as exc:
        raise MoltError(
            f"Cannot authenticate to {repository or 'PyPI'}: {exc}. Nothing was uploaded. molt "
            "never prompts for a token or a one-time password -- configure OIDC Trusted "
            "Publishing, or set UV_PUBLISH_TOKEN."
        ) from exc


# ======================================================================================
# The upload loop -- designs D2 and D3
# ======================================================================================


def _upload_plan(
    plan: Plan,
    *,
    artifacts_root: Path,
    repository: str | None,
    git_tag: bool,
    single_package: bool,
    console: Any,
    git: Any,
    uploader: Any,
    events: list[dict[str, Any]],
) -> PublishResult:
    """Upload chunk by chunk, tagging as each chunk completes, stopping at the first failure.

    The chunk boundaries *are* the dependency order, so a chunk is only reached once everything it
    depends on is on the index. Tagging happens inside the chunk and after its uploads
    (``publish/index.ts:127-187``), which is what stops a tag ever being created for something that
    failed to upload.
    """
    published: list[ReleaseRef] = []
    skipped: list[ReleaseRef] = []
    tags: list[str] = []

    for chunk in plan:
        uploaded, duplicates, failure = _upload_chunk(
            chunk,
            artifacts_root=artifacts_root,
            repository=repository,
            console=console,
            uploader=uploader,
        )
        published.extend(uploaded)
        skipped.extend(duplicates)

        if git_tag:
            taggable = list(uploaded)
            if failure is None:
                # A tag-only release is tagged with its chunk -- but only if the chunk completed.
                # Tagging one inside an abandoned chunk would make the next run skip it while the
                # dependency it travels with never reached the index.
                taggable.extend(
                    ReleaseRef(str(entry["name"]), str(entry["version"]))
                    for entry in chunk
                    if entry.get("kind") == _TAG_ONLY
                )
            for release in taggable:
                tag = _publish_tag(release, single_package=single_package)
                git.tag(tag, tag)
                tags.append(tag)
                events.append(_event(tag, release.name))
                console.success(f"New tag: {tag}")

        if failure is not None:
            raise MoltError(
                f"Publishing stopped: {failure}. The releases already uploaded are on the index "
                "and stay tagged; the remaining chunks were not attempted. PyPI has no unpublish, "
                "so molt does not press on past a failure and it cannot roll one back."
            ) from failure

    return PublishResult(
        published=tuple(published),
        skipped=tuple(skipped),
        tags=tuple(tags),
        events=tuple(events),
    )


def _publish_tag(release: ReleaseRef, *, single_package: bool) -> str:
    """The tag this run creates for ``release``, in the shape ``molt git-tag`` would have used.

    A workspace member is tagged ``<pep503-name>@<version>``; a **single-package** project is tagged
    ``v<version>``, because there is only one candidate and the version alone identifies it
    (``molt.commands.git_tag`` design D2, and ``website/docs/guides/publishing.md``, "Tags and
    machine-readable output"). The two producers must agree: a release tagged by ``publish`` under
    a shape ``git-tag`` does not recognise is a release ``git-tag`` tags a *second* time on the
    next run.

    The gate does not discriminate here -- every fixture in ``tests/publish`` is a uv workspace, so
    only the first branch is exercised. Recorded as ``PY-1``.
    """
    if single_package:
        return f"v{release.version}"
    return tag_name(release.name, release.version)


def _upload_chunk(
    chunk: Sequence[PlanEntry],
    *,
    artifacts_root: Path,
    repository: str | None,
    console: Any,
    uploader: Any,
) -> tuple[list[ReleaseRef], list[ReleaseRef], BaseException | None]:
    """Upload every publish entry in one chunk; return what went out, what was already there, and
    the first genuine failure.

    Every entry of the chunk is attempted even after one of them fails, mirroring upstream's
    ``Promise.all`` over the chunk (``publish/index.ts:127-176``): the members of a chunk have no
    edge between them, so one being rejected says nothing about the next. The *run* stops at the
    chunk boundary, which is where stopping is meaningful.
    """
    uploaded: list[ReleaseRef] = []
    duplicates: list[ReleaseRef] = []
    failure: BaseException | None = None

    for entry in chunk:
        if entry.get("kind") != _PUBLISH:
            continue
        release = ReleaseRef(str(entry["name"]), str(entry["version"]))
        artifacts = [artifacts_root / str(artifact["path"]) for artifact in entry["artifacts"]]
        try:
            uploader.upload(
                artifacts,
                package=release.name,
                version=release.version,
                repository=repository,
            )
        except Exception as exc:  # the uploader is a seam; its exception type is not molt's
            if is_duplicate_upload_error(exc):
                duplicates.append(release)
                console.warn(
                    f"{release} is already on the index; skipping it and continuing. Another "
                    "publisher won the race, so the version that is there is theirs."
                )
                continue
            console.error(f"Uploading {release} failed: {exc}")
            if failure is None:
                failure = exc
            continue
        uploaded.append(release)
        console.success(f"Published {release}.")

    return uploaded, duplicates, failure


# ======================================================================================
# Reporting -- the dry run and the NDJSON event stream
# ======================================================================================


def _report_dry_run(plan: Plan, console: Any) -> PublishResult:
    """Print what the run would do and return an empty result -- nothing built, nothing uploaded."""
    if not plan:
        console.info("Nothing to publish or tag.")
        return PublishResult()
    lines = ["Would publish:"]
    for index, chunk in enumerate(plan, start=1):
        lines.append(f"- chunk {index}")
        for entry in chunk:
            suffix = "" if entry.get("kind") == _PUBLISH else "  (tag only, never uploaded)"
            lines.append(f"  - {entry['name']} {entry['version']}{suffix}")
    console.info("\n".join(lines))
    return PublishResult()


def _event(tag: str, package_name: str) -> dict[str, Any]:
    """One NDJSON ``git-tag`` event, snake_case keys.

    Byte-identical to what ``molt git-tag`` emits (``molt.commands.git_tag._TagItem.event``), and
    that is a contract rather than a coincidence: a consumer reading the stream cannot tell which
    command produced it, so upstream's camelCase ``packageName`` (``utils/output.ts:6-10``) must
    stay renamed in both. ``tests/publish/test_publish.py`` asserts the exact bytes against the
    same reader ``tests/cli/test_git_tag.py`` uses. Both call one builder in :mod:`molt.events`,
    so the agreement is structural rather than maintained by hand (gap ``PY-8``).
    """
    return git_tag_event(tag, package_name)


# ======================================================================================
# The default seams -- never constructed by the conformance suite
# ======================================================================================


class TrustedPublishingOIDC:
    """Resolve an upload credential without ever asking a human (design D5).

    Two sources, in order. An **API token** in the environment
    (:data:`_TOKEN_ENVIRONMENT`) wins, because a project that has deliberately set one is telling
    molt which credential to use. Otherwise, if the process is running under a workflow that
    requested ``id-token: write``, the OIDC identity is exchanged for a short-lived upload token at
    the index's mint endpoint -- **PyPI Trusted Publishing**, which is the arrangement this project
    targets (tech-stack section 11) precisely so no long-lived token has to live in CI.

    Neither path prompts, and there is no third path that does. Upstream's whole ``AuthState``
    machine -- environment OTP, web-auth OTP, interactive retry, TTY-gated concurrency -- exists
    because npm asks for a one-time password mid-publish. PyPI does not, so a molt publish that
    cannot authenticate fails immediately instead of hanging a CI job on a question nobody will
    answer.

    **Not exercised by the conformance suite**, which injects its own OIDC double; see ``PY-2``.
    """

    def fetch_token(self, *, repository: str | None = None) -> str:
        """Return the token to upload with, or raise if there is no identity and no token."""
        import os

        for variable in _TOKEN_ENVIRONMENT:
            token = os.environ.get(variable)
            if token:
                return token

        request_url = os.environ.get(_ACTIONS_ID_TOKEN_URL)
        request_token = os.environ.get(_ACTIONS_ID_TOKEN_TOKEN)
        endpoint = _MINT_ENDPOINTS.get((repository or "pypi").strip().lower())
        if not request_url or not request_token or endpoint is None:
            raise MoltError(
                "No upload credential is available: no Trusted Publishing identity in this "
                "environment and no API token in "
                f"{', '.join(_TOKEN_ENVIRONMENT)}. molt does not prompt for one -- there is "
                "nothing to type at a CI job."
            )
        mint_url, audience = endpoint
        return self._mint(
            request_url=request_url,
            request_token=request_token,
            mint_url=mint_url,
            audience=audience,
        )

    def _mint(self, *, request_url: str, request_token: str, mint_url: str, audience: str) -> str:
        """Exchange the workflow's OIDC identity for a short-lived index token."""
        import httpx

        separator = "&" if "?" in request_url else "?"
        try:
            with httpx.Client(timeout=_OIDC_TIMEOUT_SECONDS) as client:
                identity = client.get(
                    f"{request_url}{separator}audience={audience}",
                    headers={"Authorization": f"bearer {request_token}"},
                )
                identity.raise_for_status()
                minted = client.post(mint_url, json={"token": identity.json()["value"]})
                minted.raise_for_status()
                return str(minted.json()["token"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise MoltError(
                f"The Trusted Publishing token exchange with {mint_url} failed: {exc}. Nothing "
                "was uploaded."
            ) from exc


class UvUploader:
    """The default upload seam: ``uv publish``, one invocation per release.

    A subprocess rather than an HTTP client, for the same reason :class:`molt.pack.UvBuilder` is
    one: uv already knows how to talk to an index, how to read ``--index`` from a project's
    configuration, and how to attach PEP 740 attestations. Reimplementing the multipart upload here
    would be a second, worse client to keep current.

    **The duplicate classification depends on this class recovering a status code** (design D2).
    ``uv publish`` prints the index's HTTP status in its failure output, so the status is parsed out
    of it and attached to the raised error; when no status can be found the failure stays
    unclassified, which makes it a hard failure rather than a skip. That is the safe direction: a
    release wrongly reported as "already published" disappears silently from a release nobody can
    walk back.

    **Not exercised by the conformance suite**, which injects its own uploader -- group 7's brief
    is explicit that no test may reach an index; see ``PY-2``.
    """

    def __init__(self, *, token: str | None = None) -> None:
        self.token = token

    def upload(
        self,
        artifacts: Sequence[Path],
        *,
        package: str,
        version: str,
        repository: str | None = None,
    ) -> None:
        """Upload one release's artifacts, raising :class:`UploadRejected` if the index refuses."""
        import subprocess

        command = ["uv", "publish"]
        if repository and repository.strip():
            target = repository.strip()
            command += ["--publish-url", target] if "://" in target else ["--index", target]
        if self.token:
            command += ["--token", self.token]
        command += [str(artifact) for artifact in artifacts]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_UPLOAD_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UploadRejected(f"Could not upload {package} {version}: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise UploadRejected(
                f"Uploading {package} {version} failed: {detail}",
                status_code=_status_in(detail),
            )


class UploadRejected(MoltError):
    """An index refused an upload. ``status_code`` is what :func:`is_duplicate_upload_error` reads.

    ``None`` means the status could not be recovered from the uploader's output, which is treated
    as "not a duplicate" -- see :class:`UvUploader`.
    """

    status_code: int | None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _status_in(output: str) -> int | None:
    """The first HTTP status code mentioned in ``output``, if any."""
    import re

    found = re.search(r"\b([45]\d\d)\b", output)
    return int(found.group(1)) if found else None
