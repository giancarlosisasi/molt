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

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--filter <glob>` | string | all changed | Publish only packages matching the glob. Usable outside GitHub Actions. |
| `--repository <name>` | string | `pypi` | Named repository/index to publish to (for example a snapshot index or `testpypi`). |
| `--index-url <url>` | string | -- | Explicit index URL, as an alternative to `--repository`. |
| `--from-pack-dir <dir>` | string | -- | Upload prebuilt artifacts and the `publish-plan.json` from a [`molt build`](/cli/pack) output directory instead of building now. |
| `--git-tag` / `--no-git-tag` | flag | `--git-tag` | Create git tags for published packages. `--no-git-tag` skips tagging of published packages. See [molt git-tag](/cli/git-tag). |
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
