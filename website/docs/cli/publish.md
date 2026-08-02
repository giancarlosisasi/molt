---
title: molt publish
---

# molt publish

Build and upload every package whose version changed, to PyPI, in dependency order, using OIDC Trusted Publishing.

## Synopsis

```bash
molt publish [OPTIONS]
molt publish-plan [OPTIONS]
```

## Description

`molt publish` ships a release. It queries the index for each package, decides what is unpublished, builds the artifacts (the [build](/cli/pack) stage), and uploads them -- creating a git tag per package as it goes. It is the third verb in the loop, after [`molt version`](/cli/version).

### Authentication: OIDC Trusted Publishing

Molt authenticates to PyPI via **OIDC Trusted Publishing** -- no long-lived API token in CI, and **no publish-time OTP or 2FA** (PyPI has none). In CI the token is exchanged automatically; PEP 740 attestations are produced by the publishing backend. There is nothing to prompt for mid-run, so `molt publish` never blocks on authentication. Outside a configured OIDC environment, molt uses whatever credentials your uploader is configured with (for example `UV_PUBLISH_TOKEN`).

### PyPI is immutable, so molt validates first

A published version cannot be overwritten or unpublished, and a partial monorepo publish cannot be rolled back. Molt therefore runs **exhaustive pre-flight validation across the whole plan before the first upload** -- names resolve, versions are not already present, artifacts build, and auth is reachable -- rather than discovering problems package by package mid-upload.

If a version turns out to already exist at upload time (a stale read racing another publisher), PyPI returns **400 "File already exists"**. Molt treats that as **skip, not fail**, and continues.

### Ordering and partial failure

Molt publishes in **topological order**: a dependency uploads before its dependents, in chunks where each chunk can go concurrently and chunks run in sequence. Cyclic dependencies are kept together in one chunk and warned about. If a chunk fails, molt tags the successes in that chunk and then **stops -- later chunks are not attempted**.

### `molt publish-plan`

`molt publish-plan` resolves and prints the same plan **without uploading anything**. It emits the versioned publish-plan envelope (`{ "version": 1, "plan": [...] }`), which [`molt build`](/cli/pack) can consume and `molt publish --from-pack-dir` can then upload. Use `--output` (or `MOLT_OUTPUT`) to write it to a file.

The envelope is written **even when there is nothing to publish** -- `{ "version": 1, "plan": [] }`. The next CI job reads that file unconditionally, so "no file" and "an empty plan" must not look alike to it.

Every key in a plan entry is `snake_case`, and entries carry no npm vocabulary: there is no `access` and no `tag`, because PyPI has neither per-package access nor dist-tags.

### Snapshots are refused against PyPI

A release whose version has molt's **snapshot shape** -- `0.0.0.dev` followed by a `.dev` counter that is either the 14-digit datetime timestamp or the 13-digit millisecond-epoch counter, which is what [`molt version --snapshot`](/cli/version) writes depending on your `snapshot_prerelease_template` -- is **refused** when the plan targets the public index:

```
Refusing to publish a snapshot release to PyPI: acme-core 0.0.0.dev20211213000730.
```

The trigger is the **shape** of the version, not who wrote it. A hand-typed `0.0.0.dev` with a 13- or 14-digit counter is refused too: molt cannot tell it from one of its own, and a version that looks like a snapshot on the public index is a mistake either way.

`publish-plan` is a separate invocation from `molt version`, so it has no memory of the `--snapshot` flag; the trigger is the version itself. The refusal fires **before molt reads the index and before it writes any output file**, so a refused run leaves nothing behind for a later stage to pick up.

Molt refuses rather than quietly sending the snapshot somewhere else, because it cannot invent the URL of your private index. Name one and the guardrail clears:

```bash
molt publish-plan --repository https://packages.internal.example/simple/ --output publish-plan.json
```

An ordinary developmental release is unaffected -- `1.2.3.dev5` and `0.0.0.dev1` are planned normally, and so is anything `molt version --pre dev` produces.

**Limitation: a calculated-version snapshot is not caught.** If your config sets `snapshot.use_calculated_version`, the snapshot's release segment is the real computed version (for example `1.3.0.dev...`) instead of `0.0.0`. That makes it indistinguishable in shape from an ordinary release, so this guardrail does **not** refuse it -- it is planned and published like any other version. Routing that kind of snapshot away from the public index is your own responsibility; point `--repository` at a private index yourself when using `use_calculated_version`.

### Naming an index turns the pypi.org query off

With `--repository` or `--index-url` pointing anywhere other than pypi.org, molt does **not** query pypi.org for already-published versions, and every local version is planned as unpublished. An explicitly named index is the index; molt does not consult a second one behind your back. Querying a private index for its published version set is not implemented yet.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--filter <glob>` | string | all changed | Publish only packages matching the glob. Usable outside GitHub Actions. |
| `--repository <name>` | string | `pypi` | Named repository/index to publish to (for example a snapshot index or `testpypi`). |
| `--index-url <url>` | string | -- | Explicit index URL, as an alternative to `--repository`. |
| `--from-pack-dir <dir>` | string | -- | Upload prebuilt artifacts and the `publish-plan.json` from a [`molt build`](/cli/pack) output directory instead of building now. |
| `--git-tag` / `--no-git-tag` | flag | `--git-tag` | Create git tags for published packages. `--no-git-tag` skips tagging of published packages -- **and of tag-only (private) releases**, whose whole contribution to a run is their tag, so `--no-git-tag` makes those entries no-ops. See [molt git-tag](/cli/git-tag). |
| `--output <file>` | string | -- | Write an NDJSON `git-tag` event stream to a file. Back-filled by `MOLT_OUTPUT`. |
| `--dry-run` | flag | off | Print the publish plan; build and upload nothing. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

`molt publish-plan` takes `--filter`, `--repository`/`--index-url`, `--output`, and `--cwd`.

## Exit codes

| Situation | Exit code |
|---|---|
| Everything published (or `--dry-run` printed the plan) | 0 |
| Nothing to publish (all versions already on the index) | 0 |
| Pre-flight validation failed | 1 |
| An upload in a chunk failed (later chunks not attempted) | 1 |

A version that already exists on the index is skipped, not an error, and does not by itself change the exit code.

## Examples

Publish everything `molt version` bumped:

```bash
molt publish
```

Preview without building or uploading:

```bash
molt publish --dry-run
```

Publish a subset:

```bash
molt publish --filter "acme-*"
```

Split build and upload (CI: build on one job, upload on another):

```bash
molt publish-plan --output publish-plan.json
molt build --from-publish-plan publish-plan.json --out-dir dist
molt publish --from-pack-dir dist
```

Publish a snapshot build to a separate index:

```bash
molt version --snapshot=pr-123
molt publish --repository snapshots
```

## See also

- [Publishing](/guides/publishing) -- the release-time guide.
- [molt build](/cli/pack) -- the mandatory build step.
- [molt git-tag](/cli/git-tag) -- tags created after a successful publish.
- [Snapshot releases](/concepts/snapshots) and [molt yank](/cli/yank).
