---
title: Changelog templates
---

# Changelog templates

molt renders each changelog entry from a real Jinja2 template, so you can add dates, custom sections, and your own layout instead of one hard-coded shape.

Dates in the changelog have been [an open changesets request since 2019](https://github.com/changesets/changesets/issues/109) -- seven years, with a pull request that waited six of them. The blocker is structural: changesets assembles every entry from a fixed `## ${version}` template welded to three fixed sections, with no seam for a user to change it. molt makes that whole layer a Jinja2 template. Dates, sections, and reordering are configuration, not a code change.

This is separate from [changelog plugins](/extending/changelog-plugins), which control the *text of each line*. A template controls the *structure around the lines*: the heading, the section titles, their order, and anything extra you want in the entry. Most customization needs only a template and no code at all.

## The default structure

Out of the box, molt reproduces the changesets layout that Python users already recognize -- an entry per version, grouped by bump size:

```md
## 2.1.0

### Minor Changes

- Add streaming support to the export API

### Patch Changes

- Fix a crash when the input file is empty
- Updated dependencies
  - acme-core@1.4.1
```

The load-bearing rules molt keeps faithful to changesets:

- One `## <version>` heading per release.
- Sections render in order: **Major**, then **Minor**, then **Patch**. An empty section is omitted entirely.
- **Dependency bumps always render last, and always in the Patch section** -- a bump to an internal dependency is treated as a patch to the dependent, regardless of how big the dependency's own change was. (Add a separate changeset for the dependent if it deserves a louder entry.)
- Dev-only dependencies never produce an "Updated dependencies" line.

New entries are inserted at the top of the file, below the `# <package>` title, so the changelog reads newest-first.

## Correct Markdown, the first time

molt writes the changelog as structured Markdown and emits it correctly on the first pass. It does **not** shell out to Prettier, dprint, or any other formatter to clean up afterward -- a step changesets needs because its text-surgery approach leaves broken blank lines between entries.

Two consequences you can rely on:

- **Spacing is right without a formatter.** Blank lines between the heading, sections, and bullets are correct as written, on every platform, with no `format` toolchain to install or configure.
- **Your summaries are preserved literally.** A summary that contains a `#` heading, a `-` list, or a `$1` keeps its text exactly. Molt treats a summary as prose, never as a template. See [Design decisions](/reference/design-decisions).

## Customizing the template

Point molt at a Jinja2 file:

```toml
[tool.molt]
changelog = { template = "changelog-entry.md.jinja", dates = true }
```

`template` is a **filename**, and a relative one is resolved against the workspace root -- the directory your `[tool.molt]` configuration lives in -- so one template describes the whole monorepo. `dates` gives the template a release date: one timestamp for the whole `molt version` run, so two packages released together are never stamped seconds apart.

Both are members of the `changelog` table, alongside `generator` -- the option that used to be the whole value of that key (`changelog = "git"` still works and still means the same thing). Add a generator when you want one:

```toml
[tool.molt]
changelog = { generator = ["github", { repo = "acme/acme" }], template = "changelog-entry.md.jinja", dates = true }
```

Inside a template these appear under `config`, which is where the example below reads `config.dates` -- the written name and the template name are the same word.

:::warning Migrating from `changelog_template` / `changelog_dates`
Those two flat keys were removed. Move them into the `changelog` table -- an old configuration still loads, but molt warns that each key was ignored and no entry template is applied until you migrate.
:::

The template receives the [release](/concepts/release-plan) for one package. The most useful fields:

| In the template | What it is |
|---|---|
| `release.name` | The distribution name |
| `release.new_version` | The version being released |
| `release.date` | A `datetime` for this release (one timestamp per `version` run) |
| `release.major` / `.minor` / `.patch` | The rendered lines for each bump size, in plan order |
| `release.dependencies` | The rendered dependency-bump lines |

A template that adds a date to the heading and a dedicated section, otherwise matching the default:

```jinja
## {{ release.new_version }}{% if config.dates %} ({{ release.date.strftime("%Y-%m-%d") }}){% endif %}

{% if release.major %}### Major Changes

{% for line in release.major %}{{ line }}
{% endfor %}
{% endif %}{% if release.minor %}### Minor Changes

{% for line in release.minor %}{{ line }}
{% endfor %}
{% endif %}{% if release.patch or release.dependencies %}### Patch Changes

{% for line in release.patch %}{{ line }}
{% endfor %}{% for line in release.dependencies %}{{ line }}
{% endfor %}
{% endif %}
```

That renders:

```md
## 2.1.0 (2026-07-23)

### Minor Changes

- Add streaming support to the export API

### Patch Changes

- Fix a crash when the input file is empty
- Updated dependencies
  - acme-core@1.4.1
```

Because the section order and headings live in the template, you can rename `### Minor Changes` to `### Features`, split lines into your own categories, or move dependency bumps into their own section -- all without touching molt or writing a plugin. molt still normalizes the final spacing so the output stays valid Markdown no matter how the template is indented.

## Templates and generators together

The two layers compose:

- A [changelog generator](/extending/changelog-plugins) decides what each bullet *says* -- `- a1b2c3d: Fix the crash (#412)`.
- A template decides where those bullets *go* -- which section, under which heading, with or without a date.

Use a template alone for structural changes; add a generator only when the per-line text needs to change too. See [Custom generators](/extending/custom-generators) to build one.

## Where to go next

- [Changelog plugins](/extending/changelog-plugins) -- the per-line generator contract.
- [Custom generators](/extending/custom-generators) -- write your own line generator.
- [The release plan](/concepts/release-plan) -- the object a template renders.
- [Options reference](/config/options) -- the `changelog` and template options.
