"""The publish plan -- what an upload *would* do, decided before anything irreversible happens.

Ports ``packages/cli/src/commands/publish-plan/getPublishPlan.ts`` and its thin command wrapper
``publish-plan/index.ts`` @ v3.0.0-next.9, against the behaviour catalogued in
``roadmap/research/test-suite/07-publish-pack.md``. Website docs: ``website/docs/cli/publish.md``
(the ``molt publish-plan`` section). The conformance suite is ``tests/publish/test_plan.py``.

Why the plan is a separate, serializable document
-------------------------------------------------
PyPI is immutable: there is no unpublish, no dist-tags, and a partial monorepo publish cannot be
rolled back (research README section 4.3). Every decision that can be made before the first upload
is therefore made *here* -- classification, chunking, the already-published comparison and the
snapshot guardrail -- and the answer is written to a versioned envelope that a later, credentialed
job can consume unchanged. Nothing in this module uploads anything.

Six rules that look arbitrary and are not
-----------------------------------------
**Ignored packages short-circuit before the registry query** (design D3, ``getPublishPlan.ts``
:106-109). The saving is not the round trip; it is that a network failure while checking a package
that would never be published must not turn a healthy run into a failed one.

**Private packages are tag-only and are never looked up.** The Python marker is the
``Private :: Do Not Upload`` classifier (:func:`molt.ecosystem.is_private`), not a ``private`` key
-- and a private package whose tag already exists is not listed again
(``utils/getUntaggedPackages.ts:11-18``), which is what stops a second run from re-tagging a whole
workspace.

**The registry rule is membership, not recency** (``getPublishPlan.ts:152-160``). A version is
published iff it appears in the index's ``releases`` map. Comparing against the *highest* published
version instead silently refuses to publish a backport -- a 1.1.x maintenance line while 1.2 is out
is an ordinary Python release pattern.

**The registry is queried under the PEP 503 normalized name** (research README section 4.5).
``GET pypi.org/pypi/Foo_Bar/json`` answers 404 on some mirrors and 301 on pypi.org, either of which
reads as "never published" and re-uploads a version that already exists. The plan entry keeps the
declared spelling; only the comparison folds.

**A dependency cycle collapses into one chunk with a warning, never an error** (design D2,
``getPublishPlan.ts:241-251``). Cyclic workspaces are legal in Python, and refusing to plan would
leave one unable to release at all -- strictly worse than an unordered upload of its members.

**The snapshot guardrail keys on the version SHAPE, not on a remembered flag** (design D1).
``publish-plan`` is a separate invocation with no memory of ``molt version --snapshot``, so there is
no flag to read. It refuses rather than silently rerouting -- molt cannot invent the URL of a
private index -- and it fires before any registry read and writes no output file, so a refused run
leaves nothing behind for a later stage to pick up.

The workspace root is not a publish candidate when the workspace has members
---------------------------------------------------------------------------
Upstream's ``getPackages()`` returns ``rootPackage`` *beside* ``packages`` for every real monorepo
tool, so ``getPublishPlan`` never sees it. uv's rule is the opposite -- a root declaring
``[project]`` **is** a member -- and :mod:`molt.ecosystem` follows uv, so the root arrives inside
``packages``. :func:`publishable_members` undoes that fold, with one exception: when the root is the
*only* package, it is the distribution (the single-package repository, which is the common Python
shape), and excluding it would make such a project unpublishable.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from molt.errors import MoltError, MoltParseError
from molt.names import normalize_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from molt.ecosystem import Package, Workspace

__all__ = [
    "ARTIFACT_KINDS",
    "INTEGRITY_PREFIX",
    "PLAN_FILENAME",
    "PUBLISH_PLAN_VERSION",
    "SNAPSHOT_VERSION_PATTERN",
    "EntryKind",
    "Plan",
    "PlanEntry",
    "build_publish_plan",
    "chunk_entries",
    "is_snapshot_version",
    "plan_envelope",
    "publishable_members",
    "read_publish_plan",
    "tag_name",
    "targets_public_pypi",
    "write_publish_plan",
]

#: ``CURRENT_PUBLISH_PLAN_VERSION`` (``getPublishPlan.ts:58-76``). The guard on this integer is the
#: only thing standing between a plan file written by one molt and an upload performed by another,
#: and on an immutable index a misread plan cannot be taken back. Bump it here, in one place, if the
#: document's shape ever changes -- and remember that the guard is **equality**, not a floor.
PUBLISH_PLAN_VERSION = 1

#: The filename every stage of the pipeline agrees on: ``publish-plan --output`` suggests it,
#: ``molt build`` writes it into its output directory, and ``molt publish --from-pack-dir`` reads it
#: from there.
PLAN_FILENAME = "publish-plan.json"

#: The two artifacts a Python release consists of, in the order the plan records them. ``npm pack``
#: writes a single ``.tgz``, so upstream's entry carries one ``tarball`` object; ``python -m build``
#: writes an sdist **and** a wheel. Both halves matter: a release with no wheel forces every
#: consumer to build from source, and one with no sdist is undistributable to any platform without a
#: matching wheel tag. The order is fixed because the plan is a document that gets diffed.
ARTIFACT_KINDS = ("sdist", "wheel")

#: How an artifact's digest is spelled (design D5). **Not** npm's SRI ``sha256-<base64>``: twine
#: sends a hex ``sha256_digest`` and the PEP 503 simple index publishes ``#sha256=<hex>``. One
#: constant so the two spellings cannot drift apart.
INTEGRITY_PREFIX = "sha256="

#: molt's snapshot version shape -- release ``0.0.0`` plus a 14-digit ``YYYYMMDDHHMMSS`` ``.dev``
#: counter, optionally carrying PEP 440's free-form local segment
#: (``molt.engine.assemble._snapshot_version``).
#:
#: Written as the *whole* shape rather than a prefix on purpose. ``.devN`` is a first-class PEP 440
#: release segment and ``molt version --pre dev`` produces one deliberately, so a trigger keying on
#: ``.dev`` alone -- or on a bare ``0.0.0`` prefix -- would refuse to publish any developmental
#: release at all. See ``tests/publish/test_plan.py::NON_SNAPSHOT_VERSIONS``.
SNAPSHOT_VERSION_PATTERN = re.compile(r"^0\.0\.0\.dev\d{14}(?:\+[A-Za-z0-9.]+)?$")

#: The suffixes that identify each member of :data:`ARTIFACT_KINDS` on disk (PEP 625, PEP 427).
_ARTIFACT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "sdist": (".tar.gz", ".zip"),
    "wheel": (".whl",),
}

#: Hosts that are the public PyPI. Naming any other index clears the snapshot guardrail, because the
#: guardrail exists to protect *public, permanent* version numbers -- a private or staging index is
#: exactly where a snapshot belongs. ``test.pypi.org`` is deliberately absent: it is a sandbox.
_PYPI_HOSTS = frozenset({"pypi.org", "www.pypi.org", "upload.pypi.org"})

#: The twine/uv repository *names* that resolve to the public PyPI when no URL is given.
_PYPI_REPOSITORY_NAMES = frozenset({"pypi", "upload.pypi.org"})

#: ``GET`` this for the published version set. The name is PEP 503 normalized by the caller.
_PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"

#: How long to wait on the index before giving up. Publishing is interactive often enough that an
#: unbounded wait reads as a hang; the plan is cheap to recompute.
_REGISTRY_TIMEOUT_SECONDS = 15.0

EntryKind = str
PlanEntry = dict[str, Any]
Plan = list[list[PlanEntry]]

#: The two ``kind`` values a plan entry may carry.
_PUBLISH = "publish"
_TAG_ONLY = "tag-only"


# ======================================================================================
# Building the plan
# ======================================================================================


def build_publish_plan(
    *,
    cwd: Path,
    console: Any = None,
    git: Any = None,
    repository: str | None = None,
    output: Path | str | None = None,
    filter: Sequence[str] | None = None,
) -> Plan:
    """Decide what ``molt publish`` would upload, without uploading anything.

    Returns the plan as a list of **chunks**: each chunk may be published concurrently and the
    chunks run in sequence, so a dependency is on the index before the release that pins it. The
    nesting is the whole point -- flattening it erases the publish order the rest of the pipeline
    depends on.

    ``filter`` is ``--filter``: the globs a package name must match to be considered at all. It
    shadows the builtin deliberately -- the shell passes one keyword per long flag, and renaming it
    here would break that contract for the sake of a name nothing in this module uses.

    ``console`` and ``git`` are injectable seams, following the convention the CLI suite froze.
    ``git`` is used for exactly one question -- which private packages are already tagged -- and
    ``git=None`` means "do not ask", which is how :func:`molt.pack.pack` calls this (it has no
    ``git=`` parameter of its own). ``repository`` is ``--repository``/``--index-url``: naming a
    non-PyPI index skips the pypi.org query entirely and clears the snapshot guardrail.

    ``output`` writes the versioned envelope, and writes an **empty** envelope when there is nothing
    to publish -- the consumer of that file reads it unconditionally, so "no file" and "an empty
    plan" must not look alike to it (``publish-plan/index.ts:34-49``, which returns before the
    nothing-to-do branch at ``:51-54``).
    """
    from pathlib import Path as _Path

    from molt.ecosystem import discover_workspace, find_workspace_root

    root = find_workspace_root(_Path(cwd))
    config = _resolve_config(root, console=console)
    workspace = discover_workspace(root, config.ecosystem)

    candidates = _selected_packages(workspace, config, filter)
    public_index = targets_public_pypi(repository)
    # Design D1: before any registry read, and before the output file is opened.
    _refuse_snapshots(candidates, public_index=public_index)

    entries = _classify(candidates, workspace, git=git, public_index=public_index, console=console)
    plan = chunk_entries(entries, workspace, config, console=console)

    if output is not None:
        write_publish_plan(_Path(output), plan)
    return plan


def publishable_members(workspace: Workspace) -> list[Package]:
    """The workspace members a publish plan may consider, in discovery order.

    The workspace root is dropped, because uv counts it as a member and upstream's ``getPackages()``
    does not -- unless it is the only package there is, which is the single-package repository and
    is the shape most Python projects have. See this module's docstring.
    """
    root = workspace.root_package
    if root is None:
        return list(workspace.packages)
    members = [
        package for package in workspace.packages if package.normalized_name != root.normalized_name
    ]
    return members if members else list(workspace.packages)


def is_snapshot_version(version: str | None) -> bool:
    """Whether ``version`` is molt's snapshot shape (:data:`SNAPSHOT_VERSION_PATTERN`)."""
    return version is not None and SNAPSHOT_VERSION_PATTERN.match(version) is not None


def targets_public_pypi(repository: str | None) -> bool:
    """Whether ``repository`` names the public PyPI -- which is also the default when it is unset.

    A bare word is read as a twine/uv repository *name*; anything with a scheme is read as a URL and
    compared on its host. Anything else is somebody's own index, and molt neither queries it nor
    applies the snapshot guardrail to it.
    """
    from urllib.parse import urlsplit

    if repository is None or not repository.strip():
        return True
    value = repository.strip()
    if "://" not in value:
        return value.lower() in _PYPI_REPOSITORY_NAMES
    return (urlsplit(value).hostname or "").lower() in _PYPI_HOSTS


# ======================================================================================
# Configuration, selection and the snapshot guardrail
# ======================================================================================


def _resolve_config(root: Path, *, console: Any) -> Any:
    """Load the configuration, rendering **both** channels (``molt.config`` design D3)."""
    from molt.config import load_config
    from molt.errors import ExitError

    result = load_config(root)
    if console is not None:
        for warning in result.warnings:
            console.warn(str(warning))
    if result.config is None:
        if console is not None:
            for error in result.errors:
                console.error(str(error))
        raise ExitError(1)
    return result.config


def _selected_packages(
    workspace: Workspace, config: Any, filter: Sequence[str] | None
) -> list[Package]:
    """Members that survive ``ignore`` and ``--filter`` -- computed **before** any registry query.

    Both filters fold PEP 503 on each side: a package declared ``Foo_Bar`` is the one
    ``ignore = ["foo-bar"]`` names, and publishing a package the user asked to ignore cannot be
    undone on PyPI, only yanked. ``molt.config`` already expands these entries against the
    workspace, so the fold here keeps the two layers agreeing rather than expanding a second time.
    """
    from molt.globs import glob_match_some

    ignored = {normalize_name(entry) for entry in config.ignore}
    selected = [
        package
        for package in publishable_members(workspace)
        if package.normalized_name not in ignored
    ]
    if not filter:
        return selected
    patterns = list(filter)
    return [
        package
        for package in selected
        if glob_match_some(package.name, patterns, key=normalize_name)
    ]


def _refuse_snapshots(candidates: Iterable[Package], *, public_index: bool) -> None:
    """Refuse a snapshot-shaped release aimed at the public index (design D1).

    Raised before the first registry read and before the output file is written, so a refused run
    touches neither the network nor the disk. The refusal names every offending release: in a
    monorepo the user needs to know the whole set, not the first one molt happened to see.
    """
    if not public_index:
        return
    offenders = [
        f"{package.name} {package.version}"
        for package in candidates
        if is_snapshot_version(package.version)
    ]
    if not offenders:
        return
    raise MoltError(
        "Refusing to publish a snapshot release to PyPI: "
        + ", ".join(offenders)
        + ". A snapshot version is throwaway, and every upload to PyPI permanently burns a public "
        "version number -- there is no unpublish. Point `--repository` at a private index to "
        "publish snapshots."
    )


# ======================================================================================
# Classification (public -> publish, private -> tag-only, already there -> absent)
# ======================================================================================


def _classify(
    candidates: Sequence[Package],
    workspace: Workspace,
    *,
    git: Any,
    public_index: bool,
    console: Any,
) -> list[PlanEntry]:
    """Turn selected packages into plan entries, in workspace order.

    A private package becomes a tag-only entry unless its tag already exists; a public package
    becomes a publish entry unless the index already has its version. The registry is asked only
    about public packages, and only when the target is the public PyPI -- how molt would query
    somebody's own index for a published version set is still an open design question, and guessing
    at it would mean issuing an unannounced request to a host the user named for uploads.
    """
    private = [package for package in candidates if package.private]
    public = [package for package in candidates if not package.private]

    tagged = _existing_tags(private, git=git, console=console)
    published = _published_versions(public, public_index=public_index, console=console)

    entries: list[PlanEntry] = []
    for package in candidates:
        version = package.version
        if version is None:
            # A dynamically-versioned member has no version to compare or to tag; the version-source
            # abstraction that would resolve it is still owed (research doc 02 section 12.5).
            continue
        directory = _relative_directory(package, workspace)
        if package.private:
            if tag_name(package.name, version) not in tagged:
                entries.append(_entry(_TAG_ONLY, package.name, version, directory))
            continue
        if version not in published.get(package.normalized_name, frozenset()):
            entries.append(_entry(_PUBLISH, package.name, version, directory))
    return entries


def _entry(kind: EntryKind, name: str, version: str, directory: str) -> PlanEntry:
    """One plan entry. Keys are snake_case and carry no npm vocabulary (design D9).

    ``access`` and ``tag`` are absent rather than unused: PyPI has neither per-package access nor
    dist-tags (research README section 4.4), so there is nothing for them to mean.
    """
    return {"kind": kind, "name": name, "version": version, "directory": directory}


def _relative_directory(package: Package, workspace: Workspace) -> str:
    """The package's directory relative to the workspace root, POSIX-spelled.

    POSIX on every platform because a plan written on Windows is read by the Linux job that uploads
    it, and a backslash there is a filename, not a separator.
    """
    try:
        return package.directory.resolve().relative_to(workspace.root.resolve()).as_posix()
    except ValueError:  # pragma: no cover - a member outside its own workspace root
        return package.directory.as_posix()


def tag_name(name: str, version: str) -> str:
    """The tag a publish run writes: the PEP 503 normalized name, ``@``, the version.

    Public because **two** stages compose tags and they must agree: this module asks "is this
    private release already tagged?" and :func:`molt.publish.publish` creates the tag after an
    upload. A second spelling of the rule in either place is how a release tagged by ``publish``
    becomes invisible to the idempotency check in ``molt git-tag`` and gets re-tagged on every run
    (``tests/publish/test_publish.py::test_publish_tags_use_pep_503_normalized_names`` says so in
    as many words).

    Note the one deliberate difference from :mod:`molt.commands.git_tag`: that command spells a
    **single-package** repository's tag ``v<version>``, because there is only one candidate and the
    version alone identifies it. The publish path has no such branch -- a plan entry names a
    distribution, and the distribution name is what PyPI published it under. See ``PY-4``.
    """
    return f"{normalize_name(name)}@{version}"


def _existing_tags(private: Sequence[Package], *, git: Any, console: Any) -> frozenset[str]:
    """Every tag in the repository, read in **one** batch, or nothing when there is no git seam.

    Batched for the same reason ``molt git-tag`` batches it: one ``git tag --list`` beats one
    ``rev-parse`` per package, and the answer is a set either way. Skipped entirely when no private
    package is a candidate, because then no entry depends on it.

    A repository molt cannot read is not an error here -- a workspace that is not a git checkout can
    still be planned, it simply has no tags -- so the failure is warned about and treated as "no
    tags", which lists the package rather than silently dropping it.
    """
    if git is None or not private:
        return frozenset()
    from molt.errors import GitError

    try:
        return frozenset(git.get_all_tags())
    except GitError as exc:  # pragma: no cover - defensive: a workspace outside a git checkout
        if console is not None:
            console.warn(
                f"Could not read git tags ({exc}); every private release is listed as untagged."
            )
        return frozenset()


def _published_versions(
    public: Sequence[Package], *, public_index: bool, console: Any
) -> dict[str, frozenset[str]]:
    """The version set the index already holds, per package, keyed by normalized name.

    Every lookup goes out under the PEP 503 normalized name (research README section 4.5). A 404 is
    "never published", which is upstream's ``infoAllow404``; any other failure is fatal, because
    guessing "not published" from a broken index is how a version gets uploaded twice.
    """
    if not public:
        return {}
    if not public_index:
        if console is not None:
            console.info(
                "An explicit index was named, so pypi.org is not queried; every local version is "
                "planned as unpublished."
            )
        return {}

    import httpx

    published: dict[str, frozenset[str]] = {}
    with httpx.Client(timeout=_REGISTRY_TIMEOUT_SECONDS) as client:
        for package in public:
            published[package.normalized_name] = _query_index(client, package.normalized_name)
    return published


def _query_index(client: Any, normalized: str) -> frozenset[str]:
    """``GET pypi.org/pypi/<normalized>/json`` -> the keys of its ``releases`` map."""
    import httpx

    url = _PYPI_JSON_URL.format(name=normalized)
    try:
        response = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise MoltError(f"Could not reach the package index at {url}: {exc}") from exc
    if response.status_code == 404:
        return frozenset()
    if response.status_code >= 400:
        raise MoltError(
            f"The package index answered {response.status_code} for {normalized}. molt cannot tell "
            "whether that version is already published, and publishing it twice is not something "
            "PyPI lets you take back."
        )
    try:
        document = response.json()
    except ValueError as exc:
        raise MoltError(
            f"The package index returned a non-JSON document for {normalized}."
        ) from exc
    releases = document.get("releases") if isinstance(document, dict) else None
    if not isinstance(releases, dict):
        return frozenset()
    return frozenset(str(version) for version in releases)


# ======================================================================================
# Chunking -- the topological order that IS the publish order
# ======================================================================================


def chunk_entries(
    entries: Sequence[PlanEntry], workspace: Workspace, config: Any, *, console: Any = None
) -> Plan:
    """Group ``entries`` into dependency-ordered chunks (``getPublishPlan.ts:204-252``).

    Entries with no edge between them share one chunk -- one chunk per release would serialize an
    entire monorepo for no reason -- and a chunk is emitted only once every release it depends on
    sits in an earlier one.

    Cycles collapse (design D2): the members of a strongly-connected component always travel
    together, and a component with more than one member is warned about by name. Silently collapsing
    it would hide the one fact the user needs in order to fix it.
    """
    if not entries:
        return []
    by_name = {normalize_name(entry["name"]): entry for entry in entries}
    order = list(by_name)
    dependencies = _dependency_edges(order, workspace, config)

    components = _strongly_connected(order, dependencies)
    for component in components:
        if len(component) > 1 and console is not None:
            named = ", ".join(by_name[key]["name"] for key in _in_order(component, order))
            console.warn(
                f"Publish plan contains cyclic dependencies: {named}. They are published together "
                "in one chunk, because no order between them is correct."
            )

    component_of = {key: index for index, group in enumerate(components) for key in group}
    blockers: dict[int, set[int]] = {index: set() for index in range(len(components))}
    for key, needed in dependencies.items():
        for other in needed:
            if component_of[other] != component_of[key]:
                blockers[component_of[key]].add(component_of[other])

    plan: Plan = []
    remaining = set(range(len(components)))
    while remaining:
        ready = {index for index in remaining if not (blockers[index] & remaining)}
        if not ready:  # pragma: no cover - components are acyclic by construction
            ready = set(remaining)
        keys = [key for index in sorted(ready) for key in components[index]]
        plan.append([by_name[key] for key in _in_order(keys, order)])
        remaining -= ready
    return plan


def _in_order(keys: Iterable[str], order: Sequence[str]) -> list[str]:
    """``keys``, sorted into the entry order the plan was built in."""
    position = {key: index for index, key in enumerate(order)}
    return sorted(keys, key=lambda key: position[key])


def _dependency_edges(
    order: Sequence[str], workspace: Workspace, config: Any
) -> dict[str, set[str]]:
    """``normalized name -> the planned releases it depends on``.

    Derived from :func:`molt.engine.get_dependents_graph`, inverted: the engine answers "who depends
    on me", and the publish order needs "what has to be on the index before me". Reusing the
    engine's edge rules is deliberate -- a second edge classifier here would drift from the one
    that decides which packages get released in the first place.
    """
    from molt.engine import get_dependents_graph, to_engine_config, to_engine_packages

    planned = set(order)
    dependencies: dict[str, set[str]] = {key: set() for key in order}
    graph, _valid, _errors = get_dependents_graph(
        to_engine_packages(workspace, placeholder_version="0.0.0"),
        to_engine_config(config, workspace),
    )
    for dependency, dependents in graph.items():
        source = normalize_name(dependency)
        if source not in planned:
            continue
        for dependent in dependents:
            target = normalize_name(dependent)
            if target in planned and target != source:
                dependencies[target].add(source)
    return dependencies


def _strongly_connected(order: Sequence[str], dependencies: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's strongly-connected components, iteratively, in dependency-first order.

    Iterative rather than recursive because the recursion depth would otherwise be the length of the
    longest dependency chain in the workspace, and a monorepo is exactly where that gets long. The
    returned order is already "a component after everything it depends on", which the caller then
    regroups into levels.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in order:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Any]] = [(root, iter(sorted(dependencies[root])))]
        while work:
            node, children = work[-1]
            descended = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(dependencies[child]))))
                    descended = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if descended:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


# ======================================================================================
# The envelope -- writing it, and the guard that every reader goes through
# ======================================================================================


def plan_envelope(plan: Plan) -> dict[str, Any]:
    """``{"version": N, "plan": [[entry, ...], ...]}`` -- the document every stage exchanges."""
    return {"version": PUBLISH_PLAN_VERSION, "plan": [list(chunk) for chunk in plan]}


def write_publish_plan(path: Path, plan: Plan) -> Path:
    """Serialize ``plan`` to ``path`` as the versioned envelope, LF-only on every platform.

    Written through ``write_bytes``: ``Path.write_text`` applies newline translation, and this file
    crosses machines -- the job that reads it is usually not the one that wrote it (research doc 02
    section 12.6).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(plan_envelope(plan), indent=2) + "\n"
    path.write_bytes(document.encode("utf-8"))
    return path


def read_publish_plan(path: Path | str) -> Plan:
    """Read and validate a plan file, returning its chunks (``getPublishPlan.ts:58-76``).

    **This is where the envelope guard lives, and it is deliberate** (design D6). Every consumer
    goes through this function -- ``molt build --from-publish-plan`` and ``molt publish
    --from-pack-dir`` both -- so a guard inlined in :func:`molt.pack.pack` would pass every test and
    still let a foreign plan reach an irreversible upload.

    Two classes of defect are refused. The **envelope**: not an object, a ``version`` that is not
    exactly :data:`PUBLISH_PLAN_VERSION` (equality, not a floor -- an older document is a different
    document), a missing or non-list ``plan``. And the **artifact list**, whenever an entry carries
    one: exactly two entries, sdist then wheel, each with a path and a ``sha256=`` integrity. An
    entry with **no** ``artifacts`` key is not a defect -- that is simply a plan that has not been
    built yet, which is exactly what ``publish-plan --output`` writes.
    """
    from pathlib import Path as _Path

    location = _Path(path)
    try:
        raw = location.read_bytes()
    except OSError as exc:
        raise MoltError(f"Could not read the publish plan at {location}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MoltParseError(
            f"The publish plan at {location} is not valid JSON.", path=str(location)
        ) from exc

    if not isinstance(document, dict):
        raise MoltError(
            f"Invalid publish plan file at {location}: the envelope must be a JSON object "
            f'of the form {{"version": {PUBLISH_PLAN_VERSION}, "plan": [...]}}.'
        )
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != PUBLISH_PLAN_VERSION:
        raise MoltError(
            f"Invalid publish plan file version at {location}: found {version!r}, but this molt "
            f"writes and reads version {PUBLISH_PLAN_VERSION}."
        )
    chunks = document.get("plan")
    if not isinstance(chunks, list):
        raise MoltError(
            f"Invalid publish plan file at {location}: `plan` must be a list of chunks."
        )

    plan: Plan = []
    for chunk in chunks:
        if not isinstance(chunk, list):
            raise MoltError(
                f"Invalid publish plan file at {location}: every chunk must be a list of entries."
            )
        plan.append([_validated_entry(entry, location) for entry in chunk])
    return plan


def _validated_entry(entry: Any, location: Path) -> PlanEntry:
    """One entry, checked for the fields every stage of the pipeline reads off it."""
    if not isinstance(entry, dict):
        raise MoltError(
            f"Invalid publish plan file at {location}: every plan entry must be an object."
        )
    kind = entry.get("kind")
    name = entry.get("name")
    if kind not in (_PUBLISH, _TAG_ONLY):
        raise MoltError(
            f"Invalid publish plan file at {location}: entry {name!r} has kind {kind!r}; the only "
            f"kinds are {_PUBLISH!r} and {_TAG_ONLY!r}."
        )
    for field in ("name", "version", "directory"):
        if not isinstance(entry.get(field), str):
            raise MoltError(
                f"Invalid publish plan file at {location}: entry {name!r} is missing a `{field}`."
            )
    if "artifacts" in entry:
        _validate_artifacts(entry["artifacts"], str(name), location)
    return entry


def _validate_artifacts(artifacts: Any, name: str, location: Path) -> None:
    """Exactly two artifacts, sdist then wheel, each digested (design D4).

    Validated on the way *in* rather than at upload time, because PyPI is immutable: half a release
    discovered halfway through a monorepo publish cannot be rolled back, only yanked, and only after
    the version number is already spent. The message names the release it refuses -- an operator who
    cannot tell which package is malformed cannot act on the refusal.
    """
    expected_order = " then ".join(ARTIFACT_KINDS)
    if not isinstance(artifacts, list) or len(artifacts) != len(ARTIFACT_KINDS):
        found = len(artifacts) if isinstance(artifacts, list) else "a non-list of"
        raise MoltError(
            f"Invalid publish plan file at {location}: {name} lists {found} artifacts, but a "
            f"Python release is exactly {len(ARTIFACT_KINDS)} -- {expected_order}."
        )
    for expected, artifact in zip(ARTIFACT_KINDS, artifacts, strict=True):
        if not isinstance(artifact, dict):
            raise MoltError(
                f"Invalid publish plan file at {location}: {name} has an artifact that is not an "
                "object."
            )
        path = artifact.get("path")
        integrity = artifact.get("integrity")
        if not isinstance(path, str) or not isinstance(integrity, str):
            raise MoltError(
                f"Invalid publish plan file at {location}: {name} has an artifact without a `path` "
                "and an `integrity`."
            )
        if _artifact_kind(path) != expected:
            raise MoltError(
                f"Invalid publish plan file at {location}: {name}'s artifact {path} is a "
                f"{_artifact_kind(path)} where the plan requires the {expected}. The order is "
                f"fixed at {expected_order}, because the plan is a document that gets diffed, so "
                "the same release must serialize the same way on every filesystem."
            )
        if not integrity.startswith(INTEGRITY_PREFIX):
            raise MoltError(
                f"Invalid publish plan file at {location}: {name}'s artifact {path} has integrity "
                f"{integrity!r}, but molt records a hex digest as {INTEGRITY_PREFIX}<hex>."
            )


def _artifact_kind(path: str) -> str:
    """Which member of :data:`ARTIFACT_KINDS` a filename is, or ``"unknown"``."""
    lowered = path.lower()
    for kind, suffixes in _ARTIFACT_SUFFIXES.items():
        if lowered.endswith(suffixes):
            return kind
    return "unknown"
