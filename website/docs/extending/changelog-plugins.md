---
title: Changelog plugins
---

# Changelog plugins

molt resolves its changelog generator through a Python **entry point**, so third parties can publish and ship a generator without molt ever knowing it exists.

Every line molt writes into a `CHANGELOG.md` comes from a *changelog generator*: a small, named piece of code that turns one changeset (and the releases it caused) into Markdown. molt ships two -- `git` and `github` -- and treats them exactly like a plugin you wrote yourself. This page explains the plugin seam, the contract a generator implements, and how to point your config at one. To build one end to end, see [Custom generators](/extending/custom-generators).

## Entry points

changesets names its generator with an npm module path:

```json
{ "changelog": ["@changesets/changelog-github", { "repo": "acme/widgets" }] }
```

molt does the idiomatic Python equivalent -- a [`importlib.metadata`](https://docs.python.org/3/library/importlib.metadata.html) **entry point** in the `molt.changelog` group. A generator package declares itself:

```toml
# in the generator's own pyproject.toml
[project.entry-points."molt.changelog"]
emoji = "molt_changelog_emoji:generator"
```

Once that package is installed, `emoji` is a name molt can resolve -- with no code change and no import path baked into your config. This is the same mechanism pytest, Sphinx, and `console_scripts` use, and it satisfies molt's rule that **configuration must be readable without executing user code**: your config names a plugin, it does not point at a file that gets run just to parse settings. Executable config and shell hooks are out of scope (see [Design decisions](/reference/design-decisions)); a typed, named plugin is the extension point instead.

molt's own `git` and `github` generators are registered exactly this way, so there is no privileged built-in path -- a plugin you publish is a first-class peer of the defaults.

## The contract

A generator implements two functions. One renders a line for *this package changed*; the other renders a line for *a dependency of this package changed*. If you have written a changesets generator, they correspond to `getReleaseLine` and `getDependencyReleaseLine`.

```python
from typing import Any
from molt.changelog import ChangelogGenerator
from molt.forge import Forge

class MyGenerator:
    def get_release_line(
        self,
        changeset: Any,             # .id, .summary, .commit (sha or None), .front_matter
        bump: str,                  # "major" | "minor" | "patch"
        options: dict | None,       # the options table from config, verbatim
        forge: Forge | None,        # injected forge adapter, or None
    ) -> str: ...

    def get_dependency_release_line(
        self,
        changesets: list[Any],
        dependencies: list[Any],    # the internal deps that moved; each has .name, .new_version
        options: dict | None,
        forge: Forge | None,
    ) -> str: ...
```

`molt.changelog.ChangelogGenerator` is the protocol above, and it is real and importable -- but the two data positions, `changeset` / `changesets` and `dependencies`, are typed `Any`, not a `Changeset` or `DependencyRelease` class. molt has no such classes: it reads both **structurally** instead, so there is nothing to import for them. `Forge`, when supplied, is a real, importable protocol -- `from molt.forge import Forge` -- and `forge` is `None` whenever no forge is configured.

**What a dependency element guarantees.** Each entry of `dependencies` has a `name` (a `str`) and a `new_version` (a parsed `packaging.version.Version`, not a string -- it renders the same in an f-string, so it is easy to assume wrongly). Nothing else about it is promised. That pair is written down as `molt.changelog.DependencyReleaseLike`, which you may import and annotate your own parameter with if you like:

```python
from collections.abc import Sequence

from molt.changelog import DependencyReleaseLike


def get_dependency_release_line(
    self,
    changesets: list,
    dependencies: Sequence[DependencyReleaseLike],
    options: dict | None,
    forge: Forge | None,
) -> str: ...
```

Annotating it is entirely optional and does not break the seam: the protocol keeps the position `Any` on purpose, because protocol method parameters are contravariant and pinning a type there would stop every generator that names its own element class from satisfying the contract.

Both functions are **synchronous**. molt calls each directly, with no `await` and no inspection of whether it returns a coroutine, so a generator that needs the network (resolving commit authors, PR numbers) must resolve it synchronously -- exactly what the built-in `github` generator does, over a sync `httpx` client.

Key points:

- **You return lines, not layout.** A function returns the text for one bullet. molt owns the surrounding structure -- the `## <version>` heading, the `### Major/Minor/Patch Changes` sections, ordering, and blank-line spacing. That structural layer is a separate, Jinja2-templated concern; see [Changelog templates](/guides/changelog-templates).
- **Forge info is injected, not imported.** When the active [forge](/forges/overview) is GitHub, molt passes a `Forge` adapter as the `forge` argument, so the generator resolves commit -> author and PR links through molt's cached, rate-limited client instead of opening its own. A generator that does not need a forge simply ignores the argument; `forge` is `None` when no forge is configured. See [GitHub](/forges/github) for what the adapter exposes.
- **Options pass through verbatim.** Whatever you put in the config options table arrives as the `options` dict, untouched. A generator validates its own options (and may declare a typed options model so molt can check them at startup rather than mid-release).
- **Errors abort before any write.** If a generator raises, molt fails the whole `version` run before touching a single file -- consistent with its [atomic, buffer-then-flush](/reference/design-decisions) discipline. You never get a half-written changelog.

The contract is exactly the four parameters above, always supplied, in this order -- like changesets, molt does not pass the surrounding release plan to a generator.

## Choosing a generator

The generator is selected by the `changelog` option in `[tool.molt]`. It accepts the same three shapes changesets uses, in TOML:

```toml
# A bare name -> the entry point "git" (the default)
[tool.molt]
changelog = "git"
```

```toml
# A name plus an options table, passed through to the generator
[tool.molt]
changelog = ["github", { repo = "acme/widgets" }]
```

```toml
# Disable changelog writing entirely
[tool.molt]
changelog = false
```

molt resolves the `changelog` name in three ways, in order:

1. **Entry point** -- a name in the `molt.changelog` group (`git`, `github`, or any installed plugin). The normal case.
2. **Dotted path** -- `my_pkg.changelog:generator`, for an in-repo generator on `sys.path` you have not packaged.
3. **File path** -- `./changelog.py`, resolved relative to the `.changeset/` directory first, then the project root, for a one-off script.

Full option details live in the [Options reference](/config/options).

## The default generators

molt ships two, both registered as entry points:

| Name | What it produces | Needs a forge? |
|---|---|---|
| `git` | Plain bullets, optionally prefixed with the 7-character commit sha (`- a1b2c3d: Fix the thing`). Dependency bumps render as `- Updated dependencies` followed by an indented `- pkg@version` list. **The default.** | No |
| `github` | The `git` output enriched with links: commit and pull-request links, and `Thanks @author!` attribution, resolved through molt's cached GitHub client. | Yes -- see [GitHub](/forges/github) |

The `git` generator works with no configuration and no network, which is why it is the default. `github` adds the links most public projects want; point `changelog` at it and give it a `repo` (or let it read `GITHUB_REPOSITORY` in CI).

## Where to go next

- [Custom generators](/extending/custom-generators) -- write, register, and configure your own generator, with a worked Jinja2 example.
- [Changelog templates](/guides/changelog-templates) -- customize the *structure* of an entry (headings, dates, sections) without writing a plugin.
- [GitHub](/forges/github) -- what the injected forge adapter provides.
- [Options reference](/config/options) -- the full `changelog` option.
