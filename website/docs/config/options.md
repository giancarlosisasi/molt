---
title: Options reference
---

# Options reference

Every molt configuration option, grouped by what it controls, with its type, default, and one-line meaning.

[`molt init`](/cli/init) writes **all** of these into your `pyproject.toml`, each at the default shown here, so the file you open after setup is the full list rather than a subset. A TOML table has no `$schema` line for an editor to follow, which makes the written file the discovery surface. Delete any line you do not need -- a missing key is read as its default.

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

**An empty list is refused.** `changed_file_patterns = []` is not "detect nothing", it is "never report anything": no file inside a package would ever count as a change to it, so the safety net that tells you *these packages changed and have no changeset* could never fire again -- silently, on every run. Molt fails the parse and names the two fixes: remove the key to get the default `["**"]`, or list the patterns that count.

A **non-empty** list is legal even when it matches nothing today, for the same reason an `ignore` glob that matches nothing is. It is also silent: molt holds no file list while parsing your configuration, so unlike an unmatched `ignore` entry, there is nothing to check the pattern against.

`base_branch` must not be empty either. An empty ref cannot resolve to a commit, and without the check it would fail much later inside a `git merge-base` error that never names the option.

## Versioning and propagation

| Option | Type | Default | Meaning |
|---|---|---|---|
| `update_internal_dependencies` | `"patch" \| "minor"` | `"patch"` | Minimum bump on a dependency that triggers rewriting a dependent's version pin. |
| `update_internal_dependents` | `"out-of-range" \| "always"` | `"out-of-range"` | Whether an already-in-range dependent still gets a patch release when its dependency bumps. |
| `bump_workspace_sources_only` | `boolean` | `false` | Only rewrite dependency pins that are backed by a workspace source. |
| `ignore` | `string[]` | `[]` | Packages that must never be released. Package names or globs; expanded to concrete names at load time. |

`update_internal_dependents` is molt's promotion of a changesets option that used to live behind the `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper. In molt it is a plain, stable, top-level option. `"out-of-range"` (the default) only pulls a dependent into the release when the dependency's new version would fall outside the dependent's current pin. `"always"` releases every internal dependent with at least a patch, even when it is still in range -- useful when you want lockstep movement. See [Dependency propagation](/guide/dependency-propagation).

`bump_workspace_sources_only` is molt's rename of changesets' `bumpVersionsWithWorkspaceProtocolOnly` (which it still accepts as an alias). The Python analogue of npm's `workspace:` protocol is a dependency backed by `[tool.uv.sources]` with `workspace = true`; when this is on, molt only rewrites the pins of such dependencies and leaves externally-versioned ones untouched.

`ignore` accepts globs and expands them against your package **names** at load time. An `ignore` pattern that matches nothing is a warning, not an error; molt drops it and continues:

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

Each group is a list of package names; globs are also accepted. The difference between the two is explained in [Linked vs fixed](/concepts/linked-vs-fixed).

## Private packages

| Option | Type | Default | Meaning |
|---|---|---|---|
| `private_packages` | `{ version: boolean } \| false` | `{ version = true }` | Whether private (non-published) packages are still versioned. |

Private packages -- apps and internal tools you version but never upload to PyPI -- are versioned by default so their internal dependency pins stay correct. Set `private_packages = false` to leave them out of versioning entirely:

```toml
[tool.molt]
private_packages = false
```

There is no `privatePackages.tag` sub-option. In changesets it gated whether private packages got a **git tag** during `version`. Molt decides that with `molt git-tag`, per run, so there is nothing for a config key to gate.

## Changelog and commit

| Option | Type | Default | Meaning |
|---|---|---|---|
| `changelog` | `false \| string \| [string, table] \| table` | molt's built-in generator | The changelog subsystem: the generator to run, and optionally an entry template and a release date. |
| `changelog.generator` | `false \| string \| [string, table]` | molt's built-in generator | Changelog generator to run, with optional generator options. |
| `changelog.template` | `string` | -- | Filename of a Jinja2 changelog-entry template, resolved against the workspace root. |
| `changelog.dates` | `boolean` | `false` | Give the entry template a release date -- one timestamp per `version` run. |
| `commit` | `boolean \| string \| [string, table]` | `false` | Auto-commit generator to run after `version`/`publish`, with optional options. |
| `format` | `false \| "mdformat"` | `false` | Optional external formatter run over files molt writes. |

`changelog` selects the generator that turns changeset summaries into changelog entries. `false` disables changelog generation. A bare string names a generator; a `[generator, options]` two-tuple passes options to it. Generators are resolved as Python **entry points** (or `module:attr` references), not shell commands, so third parties can publish generators you install like any package:

```toml
[tool.molt]
changelog = ["molt.changelog.github", { repo = "acme/acme" }]
```

See [Changelog plugins](/extending/changelog-plugins) for the generator contract.

### The table form

Write `changelog` as a **table** when you want more than a generator. `template` and `dates` shape the entry *around* the generator's lines -- the heading, the section titles and their order, and whether a date is rendered:

```toml
[tool.molt]
changelog = { generator = "molt.changelog.github", template = "changelog-entry.md.jinja", dates = true }
```

Every member is optional. `generator` falls back to molt's built-in generator, so `changelog = { dates = true }` means "the built-in generator, dated". A generator written inside the table is resolved exactly like one written directly under `changelog` -- same entry points, same `module:attr` references, one code path.

TOML's section syntax is the same document, so this is equivalent:

```toml
[tool.molt.changelog]
generator = "molt.changelog.github"
template = "changelog-entry.md.jinja"
dates = true
```

See [Changelog templates](/guide/changelog-templates).

:::warning Migrating from `changelog_template` / `changelog_dates`
Those two flat keys were removed, and a configuration that still writes them **does not load**. Move them into the `changelog` table:

```toml
[tool.molt]
changelog = { template = "changelog-entry.md.jinja", dates = true }
```

Molt's error names the replacement for you, one line per key. The `camelCase` spellings `changelogTemplate` / `changelogDates` are refused the same way.
:::

**`generator = false` beside `template` or `dates` is refused.** Switching the generator off writes no `CHANGELOG.md` at all, so a template beside it is read and then discarded -- you configured an entry template and got no changelog. Remove the template and dates, or name a generator instead of `false`. `changelog = { dates = true }` on its own is fine: the generator is still on, and that means "the built-in generator, dated".

`commit` works the same way: `false` (default) leaves committing to you; `true` uses molt's built-in commit generator; a string or tuple selects a custom one.

`format` exists mainly for parity and taste. Molt emits correct, deterministic Markdown itself -- it does not need a formatter pass to clean up broken blank lines the way changesets does -- so the default is `false`. Set it to `"mdformat"` if you want written files normalized by an external formatter.

**A JavaScript formatter is refused.** `"auto"`, `"prettier"`, `"oxfmt"`, `"deno"`, `"dprint"` and `"biome"` are all Node programs, and molt does not shell out to Node. Molt used to accept them, normalize them to `false` and warn; it now fails the parse and names the three things that work instead:

- `format = "mdformat"` -- run the Python formatter;
- `format = false` -- run none, which is also the default;
- delete the line.

This one bites migrating users who changed nothing: a stock changesets 3.0 configuration carries `format: "auto"`, and that configuration now fails. Deleting the line is the right answer for almost everybody, because molt's own output does not need a cleanup pass.

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

**The placeholder set is closed, and an unrecognised `{token}` is refused.** Molt substitutes what it knows and would otherwise write anything else into the version string as literal text -- and a version an index has stored is permanent. So `{brnach}` fails the parse, with an error naming the token and listing the five placeholders molt accepts. The empty string is refused too.

See [Snapshot releases](/concepts/snapshots) for the full model and how it differs from changesets.

## Backends (molt-native)

These have no changesets equivalent. They select the pluggable backends that make molt work across Python's fragmented tooling.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `ecosystem` | `string` | `"auto"` | Which workspace backend discovers packages, versions, and internal dependencies. |
| `forge` | `string` | `"github"` | Which forge backend drives release automation (PRs, releases). |

`ecosystem` defaults to `"auto"`, which detects a uv workspace and otherwise treats the root as a single package. Set it to `"uv"` to pin the backend and skip detection:

```toml
[tool.molt]
ecosystem = "uv"
```

**`"auto"` and `"uv"` are the values molt accepts.** Naming Poetry, Hatch, PDM, or setuptools is an error that says so, rather than a silent fallback that would discover the wrong set of packages. See [Ecosystems](/ecosystems/overview) for what a backend answers.

If your repository has no uv workspace, you do not need this option at all. A single-package repository runs the whole loop, and it is molt's default path.

See [Ecosystems](/ecosystems/overview). `forge` selects the release-automation backend; GitHub ships first, but the seam exists from day one -- see [Forges](/forges/overview).

## Where a package keeps its version (molt-native)

| Option | Type | Default | Meaning |
|---|---|---|---|
| `version_source` | `table` | absent | Where **this** package's version lives. Absent means molt detects it. |
| `version_source.kind` | `string` | *required* | `"static"`, `"file"`, `"tag"`, or a source an installed plugin registers. |
| `version_source.path` | `string` | absent | The file holding the version, relative to this package's own directory. Required for `kind = "file"`. |
| `version_source.pattern` | `string` | absent | A regular expression locating the version inside that file. It must capture a group named `version`. |

```toml
# packages/acme-core/pyproject.toml -- NOT the workspace root's
[project]
name = "acme-core"
dynamic = ["version"]

[tool.molt.version_source]
kind = "file"
path = "src/acme_core/__about__.py"
```

**This option is declared in the package's own manifest.** Where a version lives is a property of a
package, not of a workspace. A root-level table would need a name-to-source map that duplicates what
discovery already knows, breaks the moment a package is renamed, and puts one package's build detail
in another package's file. There is no workspace-level default.

In a single-package repository the root manifest *is* the member manifest, so the table sits in the
same `[tool.molt]` section molt parses, and it is validated like every other option.

Three ways to get this wrong are refused when your configuration loads, each naming the fix: a table
with no `kind`; `kind = "file"` with no `path`; and a `pattern` that does not compile, or that
compiles without a group named `version`. That group is the text molt replaces when it writes a new
version, so a pattern without it locates a line molt could not edit.

`kind` is a free string rather than a fixed list because an installed distribution may register a
fourth source. Naming one that is not installed fails with a message listing the ones that are.

See [Dynamic versions](/ecosystems/dynamic-versions) for the detection table, what happens to a
package molt cannot resolve, and why a git-tag-derived version is not releasable yet.

## Dropped from changesets

These changesets options encode npm concepts with no Python analogue, so molt does not carry them. **A configuration that still contains one does not load**: molt names the key and says why it has no equivalent. Remove `access`, `onlyUpdatePeerDependentsWhenOutOfRange`, `privatePackages.tag`, the `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper (except its `updateInternalDependents` member, which molt promotes to a plain top-level option), and `prettier`, which changesets 3.0 already replaced with `format`.

| Dropped option | Why |
|---|---|
| `access` (`"public" \| "restricted"`) | npm scoped-package publish access. PyPI has no per-package access setting. A repository-selection option may take its place later; today, use `--repository` at publish time. |
| `onlyUpdatePeerDependentsWhenOutOfRange` | Governs `peerDependencies` propagation. Python has no peer-dependency concept, so the whole option -- and the `___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH` wrapper it lived in -- is gone (research README section 4.4). |
| `privatePackages.tag` | Gated whether private packages got a **git tag** during `version`. Molt makes that a `molt git-tag` decision per run instead of a config option. |

The `$schema` key is not a behavioral option -- it drives editor autocomplete for `.molt/config.json` and is documented under [JSON schema](/config/json-schema).

## Where to go next

- [The config file](/config/config-file) -- where these options live and how validation reports problems.
- [Dependency propagation](/guide/dependency-propagation) -- what `update_internal_dependencies` and `update_internal_dependents` actually do.
- [Linked vs fixed](/concepts/linked-vs-fixed) -- choosing between the two grouping options.
- [JSON schema](/config/json-schema) -- get these options autocompleted in your editor.
