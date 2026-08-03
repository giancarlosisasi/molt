"""``molt yank`` -- verify a release, report its yank state, and hand back the browser steps.

molt-native: there is nothing to port. ``roadmap/research/test-suite/07-publish-pack.md`` lists
``molt yank`` under "New in molt", research README section 4.3 explains why it exists ("PyPI has no
unpublish... our worst constraint becomes a feature") and README section 5 item 11 lists it as
differentiator #11, marked "Impossible on npm". Website docs: ``website/docs/cli/yank.md`` (flags,
exit codes) and ``website/docs/guide/yank.md`` (semantics). The conformance suite is
``tests/publish/test_yank.py``.

This command does **not** perform the yank -- owner ruling (design D6)
----------------------------------------------------------------------
PEP 592 specifies only the *read* side of a yank (the ``data-yanked`` attribute in the Simple API)
and leaves setting the flag to each index. PyPI implements setting it as a **web form** on
``https://pypi.org/manage/project/<name>/releases/``: there is no documented API endpoint
(`warehouse#12708 <https://github.com/pypi/warehouse/issues/12708>`_ is the open request), ``twine``
has no yank command, PyPI API tokens are scoped to *uploads* so the credential CI already holds
could not authorize one anyway, and only a project **Owner** may yank.

So this module reads, verifies, and instructs. It never mutates, never deletes and resolves no
credential -- and that is enforced rather than merely intended: ``tests/publish/test_yank.py``'s
``FakeIndex`` exposes ``set_yanked`` / ``yank`` / ``unyank`` / ``delete`` /
``resolve_credentials`` **only so that calling any of them raises** (design D8). An implementation
that tries to perform the action fails the suite instead of shipping.

Three consequences of the ruling, each visible in the surface
------------------------------------------------------------
There is **no ``--dry-run``** -- every run is already one. There is **no ``--yes``** -- there is
nothing to confirm. And there is **no authentication failure case** in the exit-code table: this
reads public data, so "not found" and "unreachable" are the only failures (design D7). A version
that is already in the state the caller asked for is a **success**: a re-run of a completed
recovery must not turn a CI job red.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from molt.errors import MoltError
from molt.names import normalize_name

__all__ = [
    "DEFAULT_REPOSITORY",
    "MANAGE_URL_PATH",
    "IndexRelease",
    "PyPIIndex",
    "YankReport",
    "yank",
]

#: The path PyPI serves the release-management page under, relative to the index origin. It carries
#: the **PEP 503 normalized** project name, which is why the printed URL cannot simply echo whatever
#: spelling the caller typed (``docs.pypi.org/project-management/yanking/``).
MANAGE_URL_PATH = "/manage/project/{name}/releases/"

#: The indexes molt knows how to both query and link to, keyed by their twine/uv repository name.
#: Each value is ``(JSON API base, index origin)``. A name that is not here cannot be resolved: a
#: repository name is a local alias defined in the user's own ``.pypirc``/uv configuration, and
#: guessing an origin for it would print a URL to an index molt never consulted.
_KNOWN_INDEXES: dict[str, tuple[str, str]] = {
    "pypi": ("https://pypi.org/pypi", "https://pypi.org"),
    "testpypi": ("https://test.pypi.org/pypi", "https://test.pypi.org"),
}

#: The default index, and the value reported when ``--repository`` is not given. Spelled out rather
#: than left as ``None`` so the index seam is always told which index it is being asked about.
DEFAULT_REPOSITORY = "pypi"

#: How long the read may take. Short: this command exists to answer a question quickly before the
#: user goes clicking, and an unbounded wait is worse than "the index could not be reached".
_READ_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class IndexRelease:
    """One release as an index reports it. ``yanked`` is a *flag*: the files never go away."""

    version: str
    yanked: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class YankReport:
    """What ``molt yank`` found and what the user must now do in a browser.

    ``already`` is the whole exit-code question (design D7): it is ``True`` when the version is
    already in the state the caller asked for -- yanked when yanking, not yanked when undoing --
    and that is a success, not an error. ``current_reason`` is the reason recorded **on the index**,
    which is deliberately not the ``--reason`` the caller passed: seeing that a release was already
    yanked for a different reason is the thing that stops a second, contradictory yank.
    """

    package: str
    version: str
    repository: str
    url: str
    undo: bool
    yanked: bool
    already: bool
    current_reason: str | None
    reason: str | None
    steps: tuple[str, ...]
    summary: str

    def text(self) -> str:
        """The whole report as one block of text -- the summary, then the steps."""
        return "\n".join((self.summary, "", *self.steps))


def yank(
    package: str,
    version: str,
    *,
    reason: str | None = None,
    undo: bool = False,
    repository: str | None = None,
    console: Any = None,
    index: Any = None,
) -> YankReport:
    """Check ``version`` of ``package`` on the index and return the steps to yank it by hand.

    Reads and reports; mutates nothing. ``index`` is the injectable read seam -- any object with
    ``get_release(package, version, *, repository) -> release | None``, where the release carries
    ``yanked`` and ``reason``. It defaults to :class:`PyPIIndex`.

    ``repository`` selects **both** the index queried and the management URL printed. Routing only
    one of the two would send the user to yank a release on an index molt never looked at.

    The package name is PEP 503 normalized before the lookup and in the printed URL, because that is
    the form PyPI serves (research README section 4.5).

    Raises :class:`molt.errors.MoltError` when the package or version is not on the index, and when
    the index cannot be reached. There is no authentication failure: this reads public data and
    resolves no credential.
    """
    normalized = normalize_name(package)
    target = (repository or DEFAULT_REPOSITORY).strip() or DEFAULT_REPOSITORY
    origin = _index_origin(target)
    host = _host_of(origin)

    if index is None:
        index = PyPIIndex()

    release = _read(index, normalized, version, repository=target)
    if release is None:
        raise MoltError(
            f"{normalized} {version} is not on {host}. Nothing to yank -- check the version "
            "number before opening the release-management page, where one wrong click yanks a "
            "release that is fine."
        )

    yanked = bool(getattr(release, "yanked", False))
    current_reason = getattr(release, "reason", None)
    already = yanked is not undo
    url = origin + MANAGE_URL_PATH.format(name=normalized)

    report = YankReport(
        package=normalized,
        version=version,
        repository=target,
        url=url,
        undo=undo,
        yanked=yanked,
        already=already,
        current_reason=current_reason if current_reason is None else str(current_reason),
        reason=reason,
        steps=_steps(
            package=normalized,
            version=version,
            url=url,
            undo=undo,
            already=already,
            reason=reason,
        ),
        summary=_summary(
            package=normalized,
            version=version,
            host=host,
            yanked=yanked,
            current_reason=current_reason,
        ),
    )
    if console is not None:
        console.info(report.text())
    return report


def _read(index: Any, package: str, version: str, *, repository: str) -> Any:
    """Ask the index seam for one release, turning any transport failure into a molt error.

    The seam's exception type belongs to whoever implements it, so the failure is caught by breadth
    rather than by name. "Unreachable" must stay distinct from "not found" (design D7): reporting a
    network failure as "that version does not exist" sends the user off to check a release that is
    in fact fine.
    """
    try:
        return index.get_release(package, version, repository=repository)
    except MoltError:
        raise
    except Exception as exc:
        raise MoltError(
            f"Could not reach {repository} to check {package} {version}: {exc}."
        ) from exc


def _summary(*, package: str, version: str, host: str, yanked: bool, current_reason: Any) -> str:
    """The one-line state of the release, including the reason already recorded on the index.

    Names the index by **host** rather than by the repository alias the caller typed, because the
    alias means nothing outside that machine's configuration -- and ``website/docs/cli/yank.md``'s
    worked example is the same sentence.
    """
    if not yanked:
        return f"{package} {version} is on {host} and is not yanked."
    if current_reason:
        return f"{package} {version} is on {host} and is already yanked: {current_reason}"
    return f"{package} {version} is on {host} and is already yanked."


def _host_of(origin: str) -> str:
    """The host part of an index origin -- ``https://pypi.org`` -> ``pypi.org``."""
    from urllib.parse import urlsplit

    return urlsplit(origin).netloc or origin


def _steps(
    *, package: str, version: str, url: str, undo: bool, already: bool, reason: str | None
) -> tuple[str, ...]:
    """The browser steps, or an explanation that there is nothing to do.

    The steps name the control the user must click, not just the page: ``docs.pypi.org``'s own
    instructions are "open the release-management page, click Options next to the release, then
    Yank", and a report that printed a bare URL would leave the user hunting for it.
    """
    action = "Un-yank" if undo else "Yank"
    if already:
        state = "not yanked" if undo else "yanked"
        return (
            f"{package} {version} is already {state}, so there is nothing to do.",
            f"To review it anyway, open {url}",
        )

    steps: list[str] = [
        f"To {action.lower()} it, PyPI requires you to do this in a browser -- there is no API "
        "for it:",
        "",
        f"  1. Open {url}",
        f"  2. Click Options next to {version}, then {action}",
    ]
    number = 3
    if reason and not undo:
        steps.append(f"  {number}. Paste this reason: {reason}")
        number += 1
    steps.append(f"  {number}. Confirm")
    steps.append("")
    steps.append("You must be an Owner of the project, signed in with 2FA.")
    return tuple(steps)


def _index_origin(repository: str) -> str:
    """The origin whose management page a maintainer must open for ``repository``.

    A repository *name* is resolved through :data:`_KNOWN_INDEXES`; a URL is reduced to its origin,
    which is where a Warehouse-based index serves both the JSON API and the management pages. A
    name molt does not know is refused rather than guessed at: the name is a local alias in the
    user's own configuration, and molt would otherwise link to an index it never consulted.
    """
    known = _KNOWN_INDEXES.get(repository.lower())
    if known is not None:
        return known[1]
    if "://" in repository:
        from urllib.parse import urlsplit

        parts = urlsplit(repository)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    raise MoltError(
        f"molt does not know the index {repository!r}. A repository name is an alias in your own "
        f"configuration, so molt cannot tell which host to query or link to. Known names: "
        f"{', '.join(sorted(_KNOWN_INDEXES))}. Pass the index URL instead."
    )


class PyPIIndex:
    """The default read seam: a Warehouse JSON API, queried read-only.

    ``GET <base>/<name>/<version>/json`` carries ``info.yanked`` and ``info.yanked_reason`` (PEP
    592). A 404 means the package or that version is not there, which the caller reports as a
    failure; any other transport problem is "unreachable", which is a different failure.

    **There is no write method on this class, and that is the point.** PyPI exposes no yank API, so
    a mutating method here would be a method that cannot work -- and its mere existence is what a
    later change would reach for. If ``warehouse#12708`` ever lands, completing the action is an
    additive flag and this class gains its first writer deliberately.

    **Not exercised by the conformance suite**, which injects its own index double; see ``PY-2``.
    """

    def get_release(
        self, package: str, version: str, *, repository: str | None = None
    ) -> IndexRelease | None:
        """The release, or ``None`` when the index has no such package or no such version."""
        import httpx

        base = _KNOWN_INDEXES.get((repository or DEFAULT_REPOSITORY).lower())
        root = base[0] if base is not None else _json_base_of(repository or DEFAULT_REPOSITORY)
        url = f"{root}/{package}/{version}/json"
        try:
            with httpx.Client(timeout=_READ_TIMEOUT_SECONDS) as client:
                response = client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise MoltError(f"Could not reach the package index at {url}: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise MoltError(f"The package index answered {response.status_code} for {url}.")
        try:
            document = response.json()
        except ValueError as exc:
            raise MoltError(f"The package index returned a non-JSON document for {url}.") from exc
        info = document.get("info") if isinstance(document, dict) else None
        if not isinstance(info, dict):
            return None
        reason = info.get("yanked_reason")
        return IndexRelease(
            version=version,
            yanked=bool(info.get("yanked", False)),
            reason=None if reason is None else str(reason),
        )


def _json_base_of(repository: str) -> str:
    """The JSON API base for a URL-shaped repository -- its origin plus Warehouse's ``/pypi``."""
    return _index_origin(repository) + "/pypi"
