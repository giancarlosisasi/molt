"""molt's git seam -- a ``subprocess`` wrapper over the real ``git`` binary.

Ports ``packages/git/src/index.ts`` @ v3.0.0-next.9 (research doc 08, the
``packages/git/src/index.test.ts`` section: 20 Port / 10 Adapt). Upstream is a module of free
functions that each take a ``cwd``; molt binds one :class:`Git` object to a working directory
instead, because every call site already knows where it is and threading ``cwd`` through eight
signatures is how a wrong-directory bug gets written.

No ``GitPython`` (design D9). A subprocess wrapper keeps the failure modes explicit -- an exit code
plus stderr, which is exactly what :class:`~molt.errors.GitError` carries -- keeps the install small
for a CLI, and matches what ``tests/git/test_git.py`` asserts against: **every row in that module
runs a real git binary against a real repository**, so there is no mock path to be faithful to.

Deliberate divergences from upstream, each pinned by a test
-----------------------------------------------------------
* **A failed ``add`` or ``commit`` raises** (design D3). ``index.ts:9-25`` logs the stderr, returns
  a boolean nobody reads, and leaves the process exiting 0 -- research README section 3.4, "Failed
  ``git commit`` only logged; exit code stays 0 -> Silent CI failure". molt raises
  :class:`~molt.errors.GitError`, whose ``.code`` is the git exit status.
* **``commit`` does not hardcode ``--allow-empty``.** ``index.ts:21`` always passes it, so a
  ``version`` run that staged nothing still writes a commit. molt makes it an opt-in keyword.
* **:meth:`Git.get_commits_that_add_files` returns ``dict[path, sha]``** (design D2), not
  ``index.ts:141``'s positional list with ``undefined`` holes. Missing paths are absent; the
  requested order is preserved.
* **:meth:`Git.get_all_tags` is empty on a tagless repository.** ``index.ts:34-36`` splits ``""``
  on a newline and returns a set holding one phantom empty tag name.
* **:meth:`Git.get_changed_packages_since_ref` returns package names**, resolved through
  :mod:`molt.ecosystem` (design D8) rather than by walking manifests here.

Path handling -- the one rule (design D5)
------------------------------------------
Callers may pass native, absolute or repo-relative paths; **results are always repo-relative POSIX
paths**, except :meth:`Git.get_changed_files_since` with ``full_path=True``, which returns absolute
paths in native form. :func:`Git._repo_relative` is the only place that conversion happens, and the
commands that take a path argument run at the repository root so a repo-relative pathspec means
what it says. Diff invocations pass ``--no-relative`` so a repository with ``diff.relative = true``
cannot make the answer depend on the caller's working directory.

This is the module where "Windows-correct from day one" (research README section 5 item 15) is
either true or not: upstream added Windows CI only in 2026-07 (#2169) and has a path-bug history.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from molt.ecosystem import AUTO, discover_workspace, find_workspace_root
from molt.errors import GitError
from molt.globs import glob_match_some

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "DEEPEN_BY",
    "DEFAULT_CHANGED_FILE_PATTERNS",
    "SHORT_COMMIT_ID_LENGTH",
    "Git",
]

#: How far one round of the shallow-clone deepening loop extends the clone
#: (``index.ts:129`` -- ``deepenCloneBy({ by: 50 })``). The step size is behavior, not a tuning
#: knob: ``tests/git/test_git.py`` asserts the clone lands at exactly ``depth + DEEPEN_BY``, which
#: is what separates "deepened enough" from "downloaded the whole history" (community pain #517).
DEEPEN_BY = 50

#: Length of the short commit id (design D7). Fixed, **not** git's ``--short`` auto-abbreviation,
#: which grows with repository size: a changelog link label has to render the same on every machine.
#: ``molt add`` writes the full 40-character sha, so the truncation happens here.
SHORT_COMMIT_ID_LENGTH = 7

#: ``index.ts:271`` -- with no configured patterns, every changed file counts for its package.
DEFAULT_CHANGED_FILE_PATTERNS: tuple[str, ...] = ("**",)

#: ``index.ts:254``, ported verbatim -- the leading dot is an unescaped wildcard upstream too, and
#: tightening it would be a behavior change rather than a typo fix. Applied to repo-relative POSIX
#: paths, which is why :meth:`Git.get_changed_changeset_files_since_ref` must not hand it native
#: separators.
_CHANGESET_FILE = re.compile(r".changeset/[^/]+\.md$")

#: Applied to every invocation. ``core.quotePath=false`` stops git octal-escaping non-ASCII path
#: bytes, so a package directory with an accent survives the round trip instead of arriving as
#: ``"caf\303\251"``.
_GIT_GLOBAL_ARGS: tuple[str, ...] = ("-c", "core.quotePath=false")


class Git:
    """A git working tree, bound to one directory.

    ``cwd`` is where the subprocesses run and may be any directory inside the repository -- the
    repository root itself is discovered lazily from it (``rev-parse --show-cdup``,
    ``index.ts:188-200``) and cached, because it cannot change for the lifetime of the object.

    The **command** surface conforms to ``tests/cli/fake_cli.py::FakeGit``, method for method. That
    double is committed and four CLI suites inject it, so it outranks this class: if a signature has
    to change, the double changes first and ``tests/cli/test_fake_cli.py`` proves it (design D1).

    The CI-loop surface below is the deliberate exception, and it is now eight methods rather than
    six: the branch/remote group plus :meth:`diff_name_status` and :meth:`list_untracked`. None of
    them is on ``FakeGit``, because no ``molt`` **command** calls any of them and giving the double
    a method would let a command acquire the ability with no row noticing
    (``openspec/GAPS.md`` ``CA-6``, widened by ``ACM-7``).
    """

    __slots__ = ("_root", "cwd")

    def __init__(self, cwd: Path | str) -> None:
        self.cwd = Path(cwd)
        self._root: Path | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self.cwd)!r})"

    # ----------------------------------------------------------------------------------
    # The subprocess boundary
    # ----------------------------------------------------------------------------------

    def _run(self, *args: str, at_root: bool = False) -> str:
        """Invoke git and return stdout, raising :class:`~molt.errors.GitError` on a non-zero exit.

        ``at_root`` runs the command from the repository root. Every command that takes a **path**
        argument sets it, because :meth:`_repo_relative` produced that path relative to the root
        (design D5); everything else runs from :attr:`cwd`, so a caller's working directory still
        reaches git for the queries where it legitimately matters.

        ``errors="replace"`` rather than ``errors="strict"``: git's stderr is whatever the operating
        system handed it, and a decode failure while *reporting* a git failure would replace a
        useful message with a ``UnicodeDecodeError`` traceback.
        """
        directory = self.root if at_root else self.cwd
        completed = subprocess.run(
            ["git", *_GIT_GLOBAL_ARGS, *args],
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise GitError(completed.returncode, f"git {' '.join(args)} failed: {detail}")
        return completed.stdout

    @property
    def root(self) -> Path:
        """The repository root, resolved once from :attr:`cwd` (``index.ts:188-200``).

        ``rev-parse --show-cdup`` rather than ``--show-toplevel`` on purpose: it answers "how far up
        is the root **from here**", so the result is built from the caller's own spelling of the
        path and compares equal to it. ``--show-toplevel`` returns git's spelling, which on Windows
        differs from ``pathlib``'s in separator and sometimes in drive-letter case.
        """
        if self._root is None:
            cdup = self._run("rev-parse", "--show-cdup").strip()
            self._root = (self.cwd / cdup).resolve() if cdup else self.cwd.resolve()
        return self._root

    def _repo_relative(self, path: str | Path) -> str:
        """Normalize any caller-supplied path to a repo-relative POSIX string (design D5).

        The single input-side normalization rule. A relative path is read against :attr:`cwd`, which
        is what a caller holding ``Path("packages/pkg-a")`` means; an absolute one is used as given.
        Both come back with ``/`` separators, so ``str(Path("foo/bar"))`` on Windows -- which is
        ``"foo\\bar"`` -- never reaches git as a pathspec, where the backslash is an escape
        character rather than a separator.

        A path outside the repository falls back to :func:`os.path.relpath`, which spells the answer
        with ``..``. That is a valid pathspec, so the caller gets git's own error rather than ours.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        resolved = candidate.resolve()
        root = self.root
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            return Path(os.path.relpath(resolved, root)).as_posix()

    # ----------------------------------------------------------------------------------
    # Low-level primitives (design D1 -- short spellings, unlike the `get_*` queries)
    # ----------------------------------------------------------------------------------

    def rev_parse(self, *args: str) -> str:
        """``git rev-parse``, trimmed."""
        return self._run("rev-parse", *args).strip()

    def merge_base(self, *refs: str) -> str:
        """``git merge-base``, trimmed -- the newest commit reachable from all of ``refs``."""
        return self._run("merge-base", *refs).strip()

    def log(self, *args: str) -> str:
        """``git log``, raw stdout. Callers choose their own ``--pretty``."""
        return self._run("log", *args)

    def status(self, *args: str) -> str:
        """``git status``, defaulting to ``--porcelain`` so the output is parseable, not prose."""
        return self._run("status", *(args or ("--porcelain",)))

    def diff_name_only(self, ref: str, *, diff_filter: str | None = None) -> list[str]:
        """``git diff --name-only --no-relative <ref>`` as repo-relative POSIX paths.

        Compares the **working tree** to ``ref``, not ``HEAD`` to ``ref``: uncommitted edits are
        part of "what changed", and every caller depends on that (``index.ts:213-217``).

        ``--no-relative`` is the flag form of design D5. Without it a repository that sets
        ``diff.relative = true`` makes the output relative to the caller's directory, so the same
        query answers differently depending on where the process happens to be.
        """
        args = ["diff", "--name-only", "--no-relative"]
        if diff_filter is not None:
            args.append(f"--diff-filter={diff_filter}")
        args.append(ref)
        return [line for line in self._run(*args).splitlines() if line.strip()]

    def diff_name_status(self, ref: str) -> list[tuple[str, str]]:
        """``(status, path)`` for every file the **working tree** changes against ``ref``.

        Ports the first of the two reads ``@changesets/ghcommit``'s ``src/git.ts::getFileChanges``
        runs to turn a working tree into a host commit's additions and deletions.

        Three flags, each load-bearing:

        * ``--no-relative`` is the flag form of design D5, exactly as on :meth:`diff_name_only`:
          without it a repository that sets ``diff.relative = true`` makes the answer depend on the
          caller's working directory.
        * ``--no-renames`` so a rename arrives as a ``D`` plus an ``A`` rather than an ``R`` a
          caller would have to split back apart. Same result, one branch fewer, and no dependence
          on git's rename-similarity threshold.
        * ``-z`` so the paths are NUL-separated. Without it git applies ``core.quotePath`` and a
          path holding a space, a quote or a byte outside ASCII arrives as its own **escaped
          rendering** -- ``"src/caf\\303\\251.py"`` -- which would then be committed as a filename
          spelled exactly that way.

        With ``-z`` and ``--name-status`` git emits ``<status>NUL<path>NUL`` per entry, so the
        fields are read in pairs rather than split on a tab.
        """
        raw = self._run("diff", "--name-status", "--no-renames", "--no-relative", "-z", ref)
        fields = [field for field in raw.split("\0") if field]
        return [(fields[index], fields[index + 1]) for index in range(0, len(fields) - 1, 2)]

    def list_untracked(self) -> list[str]:
        """Every untracked, non-ignored file, as repo-relative POSIX paths.

        The second read ``@changesets/ghcommit``'s ``src/git.ts::getFileChanges`` runs
        (``git ls-files --others --exclude-standard``). Not optional for a release: ``molt version``
        writes a brand-new ``CHANGELOG.md`` for a package that never had one, and the git command
        line's own ``git add .`` picks that up -- so an API commit that skipped untracked files
        would quietly drop the first changelog of every new package.

        ``--exclude-standard`` is what honours ``.gitignore``; without it a release would try to
        commit a virtual environment. ``-z`` for the same reason :meth:`diff_name_status` needs it,
        and the command runs at the repository root because ``ls-files`` reports paths relative to
        the working directory, which would otherwise make the answer depend on where molt was
        invoked. A file inside an untracked **directory** is listed individually, which is
        ``ls-files``' own behaviour and what the commit API needs -- it takes files, not trees.
        """
        raw = self._run("ls-files", "--others", "--exclude-standard", "-z", at_root=True)
        return [path for path in raw.split("\0") if path]

    # ----------------------------------------------------------------------------------
    # Mutating operations
    # ----------------------------------------------------------------------------------

    def add(self, *paths: str | Path) -> None:
        """Stage one or more files or directories (``index.ts:9-18``).

        Accepts native and absolute paths, so a Windows caller holding a ``Path`` is not forced to
        hand-build a POSIX-separated string. ``--`` separates the pathspecs from anything git might
        otherwise read as a revision.

        Raises :class:`~molt.errors.GitError` on a non-zero exit -- upstream ``console.log``s the
        stderr and returns ``false``, which nobody checks (research README section 3.4).
        """
        if not paths:
            return
        self._run("add", "--", *(self._repo_relative(path) for path in paths), at_root=True)

    def commit(self, message: str, *, allow_empty: bool = False) -> str:
        """Commit the staged changes and return the **new** HEAD sha (``index.ts:20-25``).

        Two divergences from upstream in one method. It raises on a non-zero exit instead of
        returning a boolean every call site discards -- research README section 3.4 records that as
        "Silent CI failure", and ``tests/cli/test_version.py::test_a_failed_commit_is_fatal`` pins
        the same rule one layer up. And ``--allow-empty`` is opt-in rather than hardcoded
        (``index.ts:21``), so a run that staged nothing does not quietly write an empty commit.

        Returning the sha rather than a boolean is what ``tests/cli/fake_cli.py::FakeGit.commit``
        already promises its callers.
        """
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self._run(*args)
        return self.get_current_commit_id()

    def tag(self, name: str, message: str | None = None) -> None:
        """Create an **annotated** tag at the current HEAD (``index.ts:39-47``).

        The ``-m`` is load-bearing and upstream says why: "it's important we use the -m flag to
        create annotated tag otherwise 'git push --follow-tags' won't actually push the tags". A
        lightweight tag still resolves to the right commit, so the failure would only appear as a
        release that never reached the remote. ``message`` defaults to the tag name, which is what
        ``molt git-tag`` passes.
        """
        self._run("tag", name, "-m", message if message is not None else name)

    def tag_exists(self, name: str) -> bool:
        """Whether ``name`` is a tag in this repository (``index.ts:301-308``)."""
        return bool(self._run("tag", "-l", name).strip())

    def get_all_tags(self) -> set[str]:
        """Every tag in this repository (``index.ts:27-37``).

        Empty on a repository with no tags. Upstream splits an empty string on a newline and
        returns a set holding one empty name, which makes any ``len(tags)`` reasoning wrong.
        """
        return {line.strip() for line in self._run("tag").splitlines() if line.strip()}

    # ----------------------------------------------------------------------------------
    # Branches and the remote -- the CI-loop surface (``release-utils/src/gitUtils.ts``)
    #
    # These six are used by :mod:`molt.action` and by nothing else, as are
    # :meth:`diff_name_status` and :meth:`list_untracked` above. They are **not** part of the
    # command surface ``tests/cli/fake_cli.py::FakeGit`` doubles: no command pushes, and adding a
    # push to that double would let a command acquire one without a test noticing.
    # ----------------------------------------------------------------------------------

    def current_branch(self) -> str:
        """The checked-out branch name (``gitUtils.ts``'s ``github.context.ref`` stand-in).

        ``HEAD`` on a detached checkout, which is what ``rev-parse --abbrev-ref`` answers. Callers
        that need a real branch name pass one in rather than guessing from this.
        """
        return self.rev_parse("--abbrev-ref", "HEAD")

    def is_clean(self) -> bool:
        """Whether the working tree has nothing to commit (``gitUtils.ts``'s ``checkIfClean``)."""
        return not self.status().strip()

    def switch_to_maybe_existing_branch(self, branch: str) -> None:
        """Check ``branch`` out, creating it at HEAD when it does not exist yet.

        Upstream decides which of the two happened by *string-matching git's stderr*
        (``gitUtils.ts``), which breaks under any locale but English. Trying the checkout and
        falling back on its exit status asks git the same question in a way that has one answer
        everywhere.
        """
        try:
            self._run("checkout", branch)
        except GitError:
            self._run("checkout", "-b", branch)

    def reset(self, ref: str, *, mode: str = "hard") -> None:
        """``git reset --<mode> <ref>``.

        The version loop resets onto the commit being released, so a re-run **replaces** the
        previous run's branch instead of stacking a second release on top of it.
        """
        self._run("reset", f"--{mode}", ref)

    def push(self, branch: str, *, force: bool = False, remote: str = "origin") -> None:
        """Push HEAD to ``branch`` on ``remote`` (``release-utils/src/run.ts:146``).

        ``HEAD:<branch>`` rather than a bare branch name: the local branch may be checked out under
        a different name in a detached CI checkout, and the destination is the part that matters.
        """
        args = ["push", remote, f"HEAD:{branch}"]
        if force:
            args.append("--force")
        self._run(*args)

    def push_tags(self, *, remote: str = "origin") -> None:
        """Push every local tag to ``remote`` (``release-utils/src/run.ts:41``).

        ``--tags`` rather than ``--follow-tags``: the publish loop's tags point at commits the
        remote already has, and ``--follow-tags`` would refuse to push them without also pushing a
        branch this loop deliberately does not touch.
        """
        self._run("push", remote, "--tags")

    # ----------------------------------------------------------------------------------
    # Queries
    # ----------------------------------------------------------------------------------

    def get_current_commit_id(self, *, short: bool = False) -> str:
        """The current HEAD sha; ``short`` truncates it to :data:`SHORT_COMMIT_ID_LENGTH`.

        Truncation rather than ``rev-parse --short`` (``index.ts:310-326``) is design D7: git's
        auto-abbreviation grows with the repository's object count, so the same commit renders as
        7 characters locally and 9 in CI, and a changelog link label must not drift.
        """
        full = self.rev_parse("HEAD")
        return full[:SHORT_COMMIT_ID_LENGTH] if short else full

    def get_diverged_commit(self, ref: str) -> str:
        """Where the current branch last agreed with ``ref`` (``index.ts:49-60``).

        HEAD when the branches have not diverged. Raises :class:`~molt.errors.GitError` on a ref
        that does not resolve, carrying upstream's sentence verbatim because the CLI error funnel
        renders it to the user. Upstream throws a bare ``Error`` here, which is why its own
        ``catch (err instanceof GitError)`` in
        :meth:`get_changed_changeset_files_since_ref` never actually fires.
        """
        try:
            return self.merge_base(ref, "HEAD")
        except GitError as exc:
            raise GitError(
                exc.code,
                f'Failed to find where HEAD diverged from "{ref}". '
                f'Does "{ref}" exist and it\'s synced with remote?',
            ) from exc

    def get_changed_files_since(self, ref: str, *, full_path: bool = False) -> list[str]:
        """Files this branch changed since it diverged from ``ref`` (``index.ts:202-233``).

        The baseline is the **merge base**, never ``ref``'s tip (design D4). Diffing the tip reports
        work done on the other branch as the caller's own, which inflates the changed-package set
        and therefore the release.

        ``full_path=True`` returns absolute paths in **native** form, resolved against the
        repository root rather than :attr:`cwd` -- resolving against the cwd yields
        ``<root>/packages/packages/pkg-b/b.js`` when the call is made from ``packages/``.
        """
        diverged_at = self.get_diverged_commit(ref)
        files = self.diff_name_only(diverged_at)
        if not full_path:
            return files
        root = self.root
        return [str((root / file).resolve()) for file in files]

    def get_commits_that_add_files(
        self, paths: Sequence[str | Path], *, short: bool = False
    ) -> dict[str, str]:
        """Map each path to the commit that added it (``index.ts:62-142``).

        Returns ``dict[repo-relative POSIX path, sha]`` (design D2). A path with no adding commit is
        **absent** rather than a hole, and the requested order is preserved, so a caller still knows
        which sha belongs to which path without index-aligning two lists.

        A shallow clone makes its boundary commit look like a root commit that added every file in
        its tree, so "the commit has no parent" is the signal that the answer may be a lie. When it
        fires, the clone is deepened by :data:`DEEPEN_BY` and the remaining paths are asked again --
        never more than that, because ``fetch --unshallow`` on a large repository is the failure
        community pain #517 reports.
        """
        requested = [self._repo_relative(path) for path in paths]
        found: dict[str, str] = {}
        remaining = list(dict.fromkeys(requested))

        while remaining:
            unresolved: list[tuple[str, str]] = []
            for path in remaining:
                commit_sha, parent_sha = self._adding_commit(path)
                if not commit_sha:
                    continue  # the path has no adding commit -- it never existed here
                if parent_sha:
                    found[path] = commit_sha
                else:
                    unresolved.append((path, commit_sha))
            if not unresolved:
                break
            if not self.is_repo_shallow():
                # Nothing left to deepen, so a parentless commit is a genuine root commit
                # (design D6). Without this branch the loop never terminates.
                found.update(unresolved)
                break
            self.deepen_clone_by(DEEPEN_BY)
            remaining = [path for path, _ in unresolved]

        return {
            path: (found[path][:SHORT_COMMIT_ID_LENGTH] if short else found[path])
            for path in dict.fromkeys(requested)
            if path in found
        }

    def _adding_commit(self, path: str) -> tuple[str, str]:
        """The sha of the commit that added ``path`` and its first parent, or ``("", "")``.

        ``--`` is molt's addition to ``index.ts:83-96``. Upstream passes the path as a bare
        argument, so git reports a path that never existed as an ambiguous-argument *failure* --
        harmless there only because upstream never checks the exit code. molt's ``_run`` does, so
        the pathspec has to be unambiguous.
        """
        output = self._run(
            "log",
            "--diff-filter=A",
            "--max-count=1",
            "--pretty=format:%H:%p",
            "--",
            path,
            at_root=True,
        ).strip()
        if not output:
            return "", ""
        commit_sha, _, parents = output.partition(":")
        parent_shas = parents.split()
        return commit_sha.strip(), parent_shas[0] if parent_shas else ""

    def get_changed_changeset_files_since_ref(self, ref: str) -> list[str]:
        """Changeset files added or modified since ``ref``, repo-relative (``index.ts:236-266``).

        The one query where a git error is **swallowed into an empty result** (design D3): a fresh
        repository, or a CI checkout that never fetched the base branch, has no such ref, and that
        is a normal state rather than a failure worth exploding on.

        ``--diff-filter=d`` is lowercase -- it *excludes* deletions. ``molt version`` consumes
        changesets by deleting them, so on a release branch every applied changeset appears in the
        diff as a deletion; without the filter the GitHub Action would re-announce releases that
        already happened.
        """
        try:
            diverged_at = self.get_diverged_commit(ref)
            files = self.diff_name_only(diverged_at, diff_filter="d")
        except GitError:
            return []
        return [file for file in files if _CHANGESET_FILE.search(file)]

    def get_changed_packages_since_ref(
        self,
        ref: str,
        *,
        changed_file_patterns: Sequence[str] | None = None,
        ecosystem: str = AUTO,
    ) -> list[str]:
        """Names of the packages this branch changed since ``ref`` (``index.ts:268-299``).

        Discovery, the manifest-name rule and the closest-package rule all belong to
        :mod:`molt.ecosystem` (design D8); repeating any of them here is how two copies drift. So
        the name is whatever ``[project].name`` declares -- never the directory name -- and a file
        inside nested package directories belongs to the **closest** one, which then owns it
        exclusively.

        ``changed_file_patterns`` are matched against each file's path **relative to its own
        package**, so the pattern is ``src/**`` rather than ``packages/pkg-a/src/**``. That relative
        path is derived from resolved absolute paths, hence the POSIX conversion: on Windows it
        would otherwise read ``src\\index.py`` and match nothing (``index.ts:349-351``).

        The result is in workspace discovery order, which is sorted and therefore stable across
        machines. Upstream's order is an artefact of its longest-directory-first probe.
        """
        changed_files = self.get_changed_files_since(ref, full_path=True)
        if not changed_files:
            return []
        patterns = (
            DEFAULT_CHANGED_FILE_PATTERNS
            if changed_file_patterns is None
            else tuple(changed_file_patterns)
        )
        workspace = discover_workspace(find_workspace_root(self.cwd), ecosystem)

        owned: dict[str, list[str]] = {}
        for file in changed_files:
            path = Path(file)
            package = workspace.package_for_path(path)
            if package is None:
                continue
            relative = path.resolve().relative_to(package.directory.resolve()).as_posix()
            owned.setdefault(package.name, []).append(relative)

        return [
            package.name
            for package in workspace.packages
            if _matches_some(owned.get(package.name, ()), patterns)
        ]

    # ----------------------------------------------------------------------------------
    # Shallow-clone support
    # ----------------------------------------------------------------------------------

    def is_repo_shallow(self) -> bool:
        """Whether this is a shallow clone (``index.ts:144-178``).

        Upstream carries a fallback that probes ``.git/shallow`` for git older than 2.15, which
        predates ``rev-parse --is-shallow-repository``. Deliberately not ported: 2.15 shipped in
        2017, molt requires Python 3.11 (2022), and a second code path that no supported
        environment reaches is a second code path nothing tests.
        """
        return self.rev_parse("--is-shallow-repository") == "true"

    def deepen_clone_by(self, by: int = DEEPEN_BY) -> None:
        """Extend a shallow clone's history by ``by`` commits (``index.ts:180-187``)."""
        self._run("fetch", f"--deepen={by}")


def _matches_some(paths: Iterable[str], patterns: Sequence[str]) -> bool:
    """Whether any of ``paths`` passes ``patterns`` (``index.ts:341-369``).

    A package with no changed files at all is not a match, which is the ``changedPackageFiles.length
    > 0`` half of ``index.ts:294-297``. ``key=None`` because these are file paths: PEP 503 folding
    belongs to package **names** only (research doc 02 section 4.3).
    """
    return any(glob_match_some(path, patterns) for path in paths)
