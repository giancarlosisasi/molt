---
title: Options reference
---

# Options reference

Every molt configuration option, grouped by what it controls, with its type, default, and one-line meaning.

Options are shown by their canonical `snake_case` name. Every option also accepts the changesets-compatible `camelCase` spelling as an alias; see [The config file](/config/config-file#key-names-snake_case-and-camelcase-both-work). Examples below use `[tool.molt]` TOML, but each has an exact `.molt/config.json` equivalent.

## Change detection

| Option | Type | Default | Meaning |
|---|---|---|---|
| `base_branch` | `string` | `"main"` | Git ref used as the comparison base for "what changed". Overridable per command with `--since`. |
| `changed_file_patterns` | `string[]` | `["**"]` | Which files inside a package directory count as a change for that package. Globs match paths relative to the package dir. |

A package is considered changed when it has at least one changed file whose package-relative path matches `changed_file_patterns`. Narrow it to skip noise like docs or tests:

```toml
[tool.molt]
changed_file_patterns = ["src/**", "pyproject.toml"]
```

## Versioning and propagation

| Option | Type | Default | Meaning |
|---|---|---|---|
| `update_internal_dependencies` | `"patch" \| "minor"` | `"patch"` | Minimum bump on a dependency that triggers rewriting a dependent's version pin. |
| `update_internal_dependents` | `"out-of-range" \| "always"` | `"out-of-range"` | Whether an already-in-range dependent still gets a patch release when its dependency bumps. |
| `bump_workspace_sources_only` | `boolean` | `false` | Only rewrite dependency pins that are backed by a workspace source. |
| `ignore` | `string[]` | `[]` | Packages that must never be released. Package names or globs; expanded to concrete names at load time. |

`update_internal_dependents` is molt's promotion of a changesets option that used to live behind the `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper. In molt it is a plain, stable, top-level option. `"out-of-range"` (the default) only pulls a dependent into the release when the dependency's new version would fall outside the dependent's current pin. `"always"` releases every internal dependent with at least a patch, even when it is still in range -- useful when you want lockstep movement. See [Dependency propagation](/guides/dependency-propagation).

`bump_workspace_sources_only` is molt's rename of changesets' `bumpVersionsWithWorkspaceProtocolOnly` (which it still accepts as an alias). The Python analogue of npm's `workspace:` protocol is a dependency backed by `[tool.uv.sources]` with `workspace = true`; when this is on, molt only rewrites the pins of such dependencies and leaves externally-versioned ones untouched.

`ignore` accepts globs and expands them against your package **names** at load time. An `ignore` pattern that matches nothing is a warning, not an error -- molt simply drops it and continues:

```toml
[tool.molt]
# Never release the docs site or the internal example apps.
ignore = ["acme-docs", "examples-*"]
```

## Package grouping

| Option | Type | Default | Meaning |
|---|---|---|---|
| `fixed` | `string[][]` | `[]` | Groups of packages always released together, at the same shared version. |
| `linked` | `string[][]` | `[]` | Groups that share a version only when they happen to be released together. |

```toml
[tool.molt]
fixed  = [["acme-core", "acme-runtime"]]
linked = [["acme-cli", "acme-plugins"]]
```

Each group is a list of package names (globs are also accepted). The difference between the two -- and why molt keeps both, unlike changesets' near-identical published definitions -- is explained in [Linked vs fixed](/concepts/linked-vs-fixed).

## Private packages

| Option | Type | Default | Meaning |
|---|---|---|---|
| `private_packages` | `{ version: boolean } \| false` | `{ version = true }` | Whether private (non-published) packages are still versioned. |

Private packages -- apps and internal tools you version but never upload to PyPI -- are versioned by default so their internal dependency pins stay correct. Set `private_packages = false` to leave them out of versioning entirely:

```toml
[tool.molt]
private_packages = false
```

Molt drops changesets' `privatePackages.tag` sub-option. It gated whether private packages got a **git tag** during `version` -- not an npm dist-tag. Molt decides that with `molt git-tag`, per run, rather than from config, so the option has nothing left to gate (research README section 4.4).

## Changelog and commit

| Option | Type | Default | Meaning |
|---|---|---|---|
| `changelog` | `false \| string \| [string, table]` | molt's built-in generator | Changelog generator to run, with optional generator options. |
| `commit` | `boolean \| string \| [string, table]` | `false` | Auto-commit generator to run after `version`/`publish`, with optional options. |
| `format` | `false \| "mdformat"` | `false` | Optional external formatter run over files molt writes. |

`changelog` selects the generator that turns changeset summaries into changelog entries. `false` disables changelog generation. A bare string names a generator; a `[generator, options]` two-tuple passes options to it. Generators are resolved as Python **entry points** (or `module:attr` references), not shell commands, so third parties can publish generators you install like any package:

```toml
[tool.molt]
changelog = ["molt.changelog.github", { repo = "acme/acme" }]
```

See [Changelog plugins](/extending/changelog-plugins) for the generator contract and [Changelog templates](/guides/changelog-templates) for shaping the output.

`commit` works the same way: `false` (default) leaves committing to you; `true` uses molt's built-in commit generator; a string or tuple selects a custom one.

`format` exists mainly for parity and taste. Molt emits correct, deterministic Markdown itself -- it does not need a formatter pass to clean up broken blank lines the way changesets does -- so the default is `false`. Set it to `"mdformat"` if you want written files normalized by an external formatter. Changesets' JS-specific `format` values (`"auto"`, `"prettier"`, `"oxfmt"`, `"deno"`, `"dprint"`) are accepted for migration but do not pull in a Node toolchain.

## Snapshots

| Option | Type | Default | Meaning |
|---|---|---|---|
| `snapshot.prerelease_template` | `string \| null` | `null` | Template for the snapshot version suffix. |
| `snapshot.use_calculated_version` | `boolean` | `false` | Base the snapshot on the changeset-derived version instead of `0.0.0`. |

Snapshots produce throwaway, timestamped builds for testing. Because PyPI versions are immutable and it rejects local-version suffixes, molt's snapshot model is reshaped from changesets': suffixes compose a PEP 440 `.devN`-style build and snapshots target a **separate index** by default rather than burning a public version number. The template understands the placeholders `{tag}`, `{commit}`, `{commit-short}`, `{timestamp}`, and `{datetime}`:

```toml
[tool.molt.snapshot]
prerelease_template = "{tag}.dev{timestamp}"
use_calculated_version = true
```

See [Snapshot releases](/concepts/snapshots) for the full model and how it differs from changesets.

## Backends (molt-native)

These have no changesets equivalent. They select the pluggable backends that make molt work across Python's fragmented tooling.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `ecosystem` | `string` | `"auto"` | Which workspace backend discovers packages, versions, and internal dependencies. |
| `forge` | `string` | `"github"` | Which forge backend drives release automation (PRs, releases). |

`ecosystem` defaults to `"auto"`, which detects a uv workspace and otherwise treats the root as a single package. Set it explicitly (`"uv"`, `"poetry"`, `"hatch"`, `"pdm"`, `"setuptools"`) to pin a backend. For repos with no native workspace primitive, an explicit `[tool.molt.workspace]` members list is the escape hatch:

```toml
[tool.molt]
ecosystem = "uv"

# Escape hatch for non-uv backends: list members explicitly.
[tool.molt.workspace]
members = ["packages/*"]
exclude = ["packages/scratch"]
```

See [Ecosystems](/ecosystems/overview). `forge` selects the release-automation backend; GitHub ships first, but the seam exists from day one -- see [Forges](/forges/overview).

## Dropped from changesets

Molt deliberately does not carry these changesets options, because they encode npm/JavaScript concepts with no Python analogue. Configs that still contain them are accepted (the keys are ignored with a warning), so migration does not break.

| Dropped option | Why |
|---|---|
| `access` (`"public" \| "restricted"`) | npm scoped-package publish access. PyPI has no per-package access setting. A repository-selection option may take its place later; today, use `--repository` at publish time. |
| `onlyUpdatePeerDependentsWhenOutOfRange` | Governs `peerDependencies` propagation. Python has no peer-dependency concept, so the whole option -- and the `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper it lived in -- is gone (research README section 4.4). |
| `privatePackages.tag` | Gated whether private packages got a **git tag** during `version`. Molt makes that a `molt git-tag` decision per run instead of a config option. |

The `$schema` key is not a behavioral option -- it drives editor autocomplete for `.molt/config.json` and is documented under [JSON schema](/config/json-schema).

## Where to go next

- [The config file](/config/config-file) -- where these options live and how validation reports problems.
- [Dependency propagation](/guides/dependency-propagation) -- what `update_internal_dependencies` and `update_internal_dependents` actually do.
- [Linked vs fixed](/concepts/linked-vs-fixed) -- choosing between the two grouping options.
- [JSON schema](/config/json-schema) -- get these options autocompleted in your editor.
