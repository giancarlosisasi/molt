---
title: Roadmap
---

# Roadmap

An honest ledger of what is shipped, what is being built next, and what is planned -- sequenced so the riskiest, most-differentiating piece is proven before the rest.

molt is early. The versioning core is implemented and property-tested; most of the surface documented across this site is designed and specified but not yet shipped. This page marks which is which, grounded in the build order and the differentiation list that justify the project. The sequencing is deliberate: it front-loads the two things that killed the prior Python attempts, so molt either clears them early or fails fast.

> **Status: pre-alpha.** Treat everything below the "Shipped" heading as planned, not available. Where another page describes a command in the present tense, it is documenting the intended final behavior, not a shipped one.

## The build order

molt is being built in nine steps. Steps 1-2 are the go/no-go gates -- if the range math and the ecosystem seam land cleanly, the rest is well-specified porting work. Step 4, the release-plan engine, is the moat: it is exactly where both abandoned Python ports stopped.

| # | Step | Status |
|---|---|---|
| 1 | PEP 440 bump math + range matching (`satisfies`) | **Shipped** |
| 2 | Ecosystem backend protocol + uv workspace discovery | Next (go/no-go) |
| 3 | Config + changeset parse/write | Planned |
| 4 | The release-plan engine -- the moat | Planned |
| 5 | `apply` with atomic buffer-then-flush + `uv lock` | Planned |
| 6 | CLI: `init`, `add`, `version`, `status`, with `--dry-run` throughout | Planned |
| 7 | Changelog + entry-point plugin surface | Planned |
| 8 | `pack` / `publish` via OIDC trusted publishing | Planned |
| 9 | GitHub Action, then the forge seam | Planned |

## Shipped

- **PEP 440 bump arithmetic and range matching.** The `molt.versioning` module implements bump math over `major`/`minor`/`patch` and the `satisfies` range check, built on the `packaging` library. This is the highest-risk, everything-depends-on-it piece, and it is done and covered by property tests (including a ported conformance matrix from changesets' own fixtures). The deliberate PEP 440 vs node-semver prerelease divergence is pinned by a test. See [Versioning and PEP 440](/concepts/versioning-pep440).

## Next: the go/no-go gates

- **Ecosystem backend protocol + uv workspace discovery.** The seam that lets molt discover workspace members and know where each package's version lives. uv is the first backend; the protocol is what makes Poetry, Hatch, PDM, and setuptools additions rather than rewrites. This is the feature changesets rejected in 2020, proven first here. See [Ecosystems](/ecosystems/overview) and [uv](/ecosystems/uv).

## Planned, in sequence

- **Config + changeset parse/write.** Reading `[tool.molt]` (with camelCase aliasing for changesets compatibility) and the `.changeset/*.md` format, with batch diagnostics rather than fail-on-first. See [The config file](/config/config-file) and [Changeset format](/config/changeset-format).
- **The release-plan engine.** The 3-pass fixpoint loop -- dependent propagation, fixed-group and linked-group resolution -- that turns a pile of changesets into concrete version numbers. This is the moat; it is where credible monorepo dependency propagation lives, and where the two prior Python ports died. See [The release plan](/concepts/release-plan).
- **Atomic apply + lockfile.** Buffer-then-flush writes so a mid-run failure never double-bumps, plus `uv lock` in the same commit. See [Design decisions](/reference/design-decisions).
- **The CLI.** `init`, `add`, `version`, and `status`, with `--dry-run` printing a machine-readable plan on every mutating command from the start. Non-interactive `add` (for Dependabot, Renovate, and codegen) is part of this step. See [CLI overview](/cli/overview) and [Dry runs and plans](/guides/dry-run-and-plans).
- **Changelog + plugins.** The Jinja2 templating layer and the `molt.changelog` entry-point seam, with `git` and `github` generators. See [Changelog templates](/guides/changelog-templates) and [Changelog plugins](/extending/changelog-plugins).
- **Publish.** `pack` -> `publish` via PyPI trusted publishing (OIDC), with exhaustive pre-upload validation, and [`molt yank`](/cli/yank) as the recovery verb. See [Publishing](/guides/publishing).
- **Automation.** A GitHub Action first, then the forge seam for GitLab, Gitea, and others. See [CI: GitHub Action](/guides/ci-github-action) and [Forges](/forges/overview).

## What ships first, and why not everything

molt commits to **uv and GitHub first** and treats every other ecosystem and forge as an addition through a seam, not a rewrite. That is a scope decision, not a limitation of the design: the backends and forges are protocols from day one, so a Poetry backend or a GitLab forge is new code behind an existing interface. Shipping one of each keeps the surface small enough to get right.

## Differentiation, ranked by demand

The features that make molt more than a port are the ones changesets users have asked for -- in some cases for years -- and never received. This is the demand record, not a promise of dates; each maps to a build-order step above.

| Differentiator | Upstream status | molt |
|---|---|---|
| Ecosystem / version-source abstraction | Rejected 2020; re-requested 6 years; OpenAI PR parked | Step 2 |
| Lockfile updates in `version` | Absent | Step 5 |
| Uniform plan object + `--dry-run` everywhere | Partial (`publish-plan` only) | Step 6 |
| Non-interactive `add` | Not possible | Step 6 |
| Real changelog templating (dates, sections) | Open 7 years (#109) | Step 7 |
| No `pre.json` -- prerelease as a flag | ~15 open issues | Steps 4-6 |
| Forge-agnostic integration | Open 4 years, no maintainer reply | Step 9 |
| Single-package + root-workspace first-class | Degenerate case upstream | Steps 2-6 |
| `molt yank` | Impossible on npm | Step 8 |
| Atomic writes + resumable failure | Double-bumps on retry | Step 5 |
| Correct changelog Markdown (no formatter pass) | Needs Prettier/dprint | Step 7 |
| Windows-correct from day one | Windows CI added 2026-07 | Every step |

The strategic case for all of this -- who asked, when, and why upstream deferred it -- is in [Why molt](/introduction/why-molt).

## Explicitly deferred or refused

Not on the roadmap, on purpose:

- **Deferred:** polyglot (Node + Python in one repo) support -- possible later through the ecosystem seam, but being the best Python tool comes first. A TUI dashboard: no.
- **Refused outright:** full commit-derived versioning, executable config, arbitrary shell hooks as the primary extension point, per-tool bespoke integrations, and chat bots. See [Comparison with changesets](/reference/comparison-with-changesets) and [Design decisions](/reference/design-decisions).

## Where to go next

- [Why molt](/introduction/why-molt) -- the demand and the strategic opening.
- [The release plan](/concepts/release-plan) -- the moat, in detail.
- [Comparison with changesets](/reference/comparison-with-changesets) -- parity and divergence.
- [Design decisions](/reference/design-decisions) -- the choices behind the sequencing.
