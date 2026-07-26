---
title: Troubleshooting
---

# Troubleshooting

Common errors and confusing behaviors, in symptom -> cause -> fix form -- most of them are molt refusing to do something unsafe on purpose.

Many of the entries below are deliberate stops, not failures. molt would rather halt with a clear message than produce a release that PyPI's immutability makes unfixable. If you hit something not covered here, the [Design decisions](/reference/design-decisions) page explains the reasoning behind most of molt's refusals.

## `molt version` refuses: `dynamic = ["version"]` detected

**Symptom.** `molt version` stops with an error naming a package whose `pyproject.toml` declares `dynamic = ["version"]` (typically a setuptools-scm or hatch-vcs project).

**Cause.** With a dynamic version, the version is not written in the manifest -- it is derived from a git tag at build time. There is nothing in the file for molt to bump, and silently doing nothing would leave you with a "released" package whose version never moved. That silent no-op is worse than an error.

**Fix.** Pick one:

- Give the package a **static** `[project].version` that molt manages. This is the supported path -- molt owns the number, writes it to the manifest, and creates the git tag.
- **Exclude** the package from molt's release plan if it is versioned entirely by its own tag-driven tooling and you do not want molt touching it.

molt refuses rather than guesses here on purpose; see [Design decisions](/reference/design-decisions).

## Error: both `[tool.molt]` and `.molt/config.json` are present

**Symptom.** Any molt command fails immediately with a message that configuration was found in two places.

**Cause.** molt reads config from `[tool.molt]` in `pyproject.toml`, with `.molt/config.json` as a fallback -- but never both. Merging two sources would require precedence rules, and every precedence rule is a subtle footgun.

**Fix.** Delete one. Keep `[tool.molt]` in `pyproject.toml` unless you have a specific reason to prefer standalone JSON. See [The config file](/config/config-file).

## `molt version` exits 1 with "no changesets"

**Symptom.** `molt version` prints that there are no pending changesets and exits with code 1, writing nothing.

**Cause.** This is intended behavior, not a crash. A `version` run with no changesets is a no-op, and molt makes that detectable by exiting non-zero -- so a CI pipeline can tell "nothing to release" apart from "released successfully." (changesets adopted the same exit-1 behavior in v3, after the old silent exit-0 caused snapshot-mode footguns.)

**Fix.** Usually nothing is wrong -- there is simply nothing to release. If you expected a release:

- Confirm you actually [added a changeset](/guides/adding-a-changeset) (`molt status` lists what is pending).
- Check that a previous `molt version` did not already consume and delete the changesets (it deletes them once applied).
- In CI, treat exit 1 from `version` as "no release this run," not as a build failure.

## Snapshot upload rejected by PyPI

**Symptom.** A snapshot publish returns a 400 from PyPI -- often about a local version segment, or simply a rejected filename.

**Cause.** Snapshots are not meant for PyPI. PyPI rejects local-version suffixes (`+abc1234`), and even a legal `.devN` snapshot would permanently burn a public version number, since PyPI versions are immutable and cannot be unpublished. High-frequency snapshot publishing to PyPI is unworkable by design.

**Fix.** Target a **non-PyPI index** for snapshots -- a local wheelhouse, TestPyPI, or a private index (devpi, Artifactory, CodeArtifact). This is molt's default; you only see this error if snapshots were explicitly pointed at PyPI. If you need to share a build, prefer building the artifact and handing out the exact install command over publishing to a public index. See [Snapshot releases](/concepts/snapshots).

## Error: prerelease kind is not PEP 440-legal

**Symptom.** `molt version --pre next` (or any custom tag) fails, saying the prerelease kind is invalid.

**Cause.** PEP 440 allows prereleases only from a fixed vocabulary: `a` (alpha), `b` (beta), `rc` (release candidate), and `dev`. Arbitrary tags like `next` or `canary` -- legal in npm/SemVer -- cannot be represented, and PyPI would reject or renormalize them.

**Fix.** Use one of the legal kinds:

```bash
molt version --pre rc      # 2.0.0rc0, 2.0.0rc1, ...
molt version --pre a       # 2.0.0a0
molt version --pre dev     # 2.0.0.dev0
```

See [Prerelease mode](/concepts/prerelease) and [`molt pre`](/cli/pre).

## Will `molt version` delete the comments in my `pyproject.toml`?

**Symptom.** Concern (or a diff review) that molt rewriting `[project].version` and internal dependency ranges will strip comments or reflow the whole file.

**Cause.** A naive read-modify-write through a plain TOML parser discards comments and reformats -- which would be an instant-uninstall bug for a Python tool, since `pyproject.toml` comments are load-bearing to their authors.

**Fix.** Nothing to do -- molt uses `tomlkit` on the write path specifically to round-trip your file, so comments, key order, and formatting are preserved and only the values molt changes are touched. (Round-tripping is not always byte-perfect in pathological cases; if you ever see an unexpected reflow, it is worth reporting.) The read-only paths use the faster stdlib `tomllib`; only the handful of files molt actually rewrites go through `tomlkit`.

## A changeset targets a package but nothing happens to it

**Symptom.** A changeset names a package, but `molt version` does not release it -- and molt prints a warning about it.

**Cause.** The package is being skipped -- it is in the `ignore` list, marked non-publishable, or has no version to bump. changesets drops such a reference silently; molt warns instead, so the mismatch is visible rather than a mystery.

**Fix.** Either remove the package from the changeset, or stop skipping it (adjust `ignore` / publish settings) if you did intend to release it. The warning tells you which package and which reason.

## A changeset name does not match a workspace member

**Symptom.** A changeset front-matter key like `Acme_Core` seems not to match the project named `acme-core`.

**Cause.** This is not actually a problem -- molt applies [PEP 503 name normalization](/config/changeset-format) when matching changeset keys against workspace members, so `Acme_Core`, `acme_core`, and `acme-core` all resolve to the same package. (changesets never needed this, so migrated changesets may use inconsistent casing.)

**Fix.** Nothing required. If you want the file to read cleanly, use the canonical normalized name (`acme-core`), but any equivalent spelling works.

## Where to go next

- [Design decisions](/reference/design-decisions) -- why molt refuses rather than guesses.
- [The config file](/config/config-file) -- config location and format.
- [Snapshot releases](/concepts/snapshots) and [Prerelease mode](/concepts/prerelease) -- the version-shape rules behind two of the errors above.
- [Comparison with changesets](/reference/comparison-with-changesets) -- how these behaviors differ from changesets.
