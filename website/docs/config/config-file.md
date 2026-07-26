---
title: The config file
---

# The config file

Molt reads its configuration from one place -- either a `[tool.molt]` table in your root `pyproject.toml` or a standalone `.molt/config.json` -- and refuses to guess when both exist.

Configuration is optional. With no config at all, molt runs on defaults: `base_branch = "main"`, a single-package project (or an auto-detected [uv workspace](/ecosystems/overview)), the built-in changelog generator, and no auto-commit. You add config only to change one of those. Every option and its default is listed in the [options reference](/config/options).

## Where config lives

There are two supported locations, in priority order:

1. **`[tool.molt]` in the workspace-root `pyproject.toml`** -- the preferred, idiomatic home. It sits alongside `[tool.uv]`, `[tool.ruff]`, and the rest of your tooling, it takes comments, and every Python tool can already read it.
2. **`.molt/config.json`** -- a standalone JSON fallback for projects that do not keep a root `pyproject.toml`, or teams migrating a `.changeset/config.json` from changesets who want the closest possible shape.

Whichever you use, molt resolves it from the **workspace root**, not from your current directory. Running `molt` from inside `packages/acme-core/` finds the same root config as running it from the top.

> Note: the config file is separate from your changesets. Changeset files always live in `.changeset/*.md` (see [Changeset file format](/config/changeset-format)); only the tool's *settings* live in `pyproject.toml` or `.molt/config.json`.

### A minimal config

In `pyproject.toml`:

```toml
[tool.molt]
base_branch = "main"
```

Or, equivalently, in `.molt/config.json`:

```json
{
  "$schema": "https://molt.dev/schema/config.json",
  "baseBranch": "main"
}
```

Both express exactly the same thing. The `$schema` line in the JSON form is optional and drives editor autocomplete -- see [JSON schema](/config/json-schema).

## One source, never merged

If molt finds **both** a `[tool.molt]` table and a `.molt/config.json`, it stops with an error:

```
Both pyproject.toml [tool.molt] and .molt/config.json define configuration; pick one.
```

This is deliberate. Silently merging two config sources -- deciding which key wins when they disagree -- is a bug factory, and a release tool is the last place you want a surprising precedence rule. Molt picks exactly one file and reads only that file. Delete or empty one of the two to resolve the error.

## Key names: snake_case and camelCase both work

Molt's canonical option names are `snake_case`, which is what TOML users expect: `base_branch`, `update_internal_dependencies`, `changed_file_patterns`. But every changesets doc, blog post, and existing `config.json` uses `camelCase`. To keep migration painless, **molt accepts both spellings for every option:**

```toml
# Both of these set the same option. Prefer snake_case in new configs.
[tool.molt]
base_branch = "main"          # canonical
baseBranch  = "main"          # accepted (changesets-compatible alias)
```

This is powered by pydantic's `AliasChoices`, so it is a first-class part of the schema, not a preprocessing hack -- the [JSON schema](/config/json-schema) documents both names. Write snake_case in new configs; paste camelCase from an old changesets setup and it just works. A handful of options were also renamed where changesets' name no longer fits Python; those still accept the old name as an alias, and the [options reference](/config/options) flags each one.

## Validation never crashes

Molt does not throw an exception the moment it hits a bad config value. Instead, config loading returns a structured result with three parts:

| Part | Meaning |
|---|---|
| `config` | The fully normalized configuration, or absent if the config could not be resolved. |
| `warnings` | Non-fatal problems: an `ignore` glob that matches nothing, an unknown key, a `fixed` group member that matches no package. Molt runs anyway. |
| `errors` | Fatal problems: a value of the wrong type, a package listed in two `fixed` groups, a mistyped enum. Molt reports them and exits non-zero. |

The point is that molt collects **all** the problems in one pass and shows them together, rather than failing on the first one and making you fix issues one reload at a time. Commands like [`molt status`](/guides/status) and [`molt version`](/cli/version) surface the full list up front.

Unknown keys are a warning, not a silent drop. If you typo `base_brnach`, molt keeps going on defaults but tells you the key was ignored -- so a typo can never quietly disable a setting you thought you had configured.

## Where to go next

- [Options reference](/config/options) -- every option, its type, default, and meaning.
- [Changeset file format](/config/changeset-format) -- the grammar of the `.changeset/*.md` files this config governs.
- [JSON schema](/config/json-schema) -- editor autocomplete and validation for `.molt/config.json`.
- [Ecosystems](/ecosystems/overview) -- how molt discovers your workspace and packages.
