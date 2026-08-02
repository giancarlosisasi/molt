---
title: JSON schema
---

# JSON schema

Molt ships a JSON Schema for its configuration so your editor can autocomplete and validate `.molt/config.json` as you type.

## What ships, and why it is always correct

Molt's config is defined by a pydantic model. That same model generates the JSON Schema for free -- molt does not maintain a schema file by hand, so the schema can never drift out of sync with what the tool actually accepts. When an option is added, renamed, or given a new default, the published schema reflects it in the same release. Both the canonical `snake_case` names and their changesets-compatible `camelCase` aliases appear in the schema, so autocomplete works whichever spelling you use.

The schema declares that **no additional properties are allowed** -- for the document itself and for every nested table it defines -- so your editor flags an unknown key before molt is ever run. That matches what molt does at load time, where an unknown key stops the run. It also declares that `changed_file_patterns` holds at least one entry, for the same reason: an empty list is refused.

## Referencing it from `.molt/config.json`

Add a `$schema` key pointing at molt's published schema. Editors that understand JSON Schema (VS Code out of the box, plus most others) will then offer completion, inline docs, and validation:

```json
{
  "$schema": "https://molt.gio-labs.com/schema/config.json",
  "baseBranch": "main",
  "updateInternalDependencies": "patch",
  "fixed": [["acme-core", "acme-runtime"]]
}
```

The `$schema` key is informational only -- molt itself ignores it when reading config, so it never affects behavior. The schema is also bundled inside the installed `molt-cli` package, so tooling can resolve it offline against the exact version you have installed.

## Editor setup

**VS Code / JSON-aware editors.** The `$schema` key above is enough. No extension or settings change is required; the editor fetches the schema and starts validating immediately.

**Associating without `$schema`.** If you would rather not add the key to every file, associate the schema by filename in your editor settings. In VS Code:

```json
{
  "json.schemas": [
    {
      "fileMatch": [".molt/config.json"],
      "url": "https://molt.gio-labs.com/schema/config.json"
    }
  ]
}
```

**TOML (`[tool.molt]` in `pyproject.toml`).** JSON Schema does not attach to TOML automatically, but Taplo-based tooling (the Even Better TOML extension) can apply the same schema via a directive at the top of the file or a Taplo config entry, giving `[tool.molt]` the same completion. Molt registers its schema with SchemaStore so this works with minimal setup.

## It stays in sync automatically

Because the schema is derived from the implementation rather than written separately, there is nothing to keep updated and no chance of the schema promising an option the tool rejects (or omitting one it accepts). Pin the `$schema` URL to a specific molt version if you want completion to match an older installed version exactly; otherwise the latest schema tracks the latest release.

## Where to go next

- [Options reference](/config/options) -- the human-readable version of everything the schema encodes.
- [The config file](/config/config-file) -- where config lives and how molt validates it.
