---
title: Custom generators
---

# Custom generators

Write a changelog generator as a small Python package, register it as a `molt.changelog` entry point, and point your config at it by name.

This is the hands-on companion to [Changelog plugins](/extending/changelog-plugins), which covers the contract and the plugin seam in the abstract. Here we build one from an empty directory to a working `molt version`. The example produces categorized, emoji-prefixed release lines rendered with a small Jinja2 template.

## When you need a generator (and when you do not)

A generator controls the **text of each changelog line** -- how a single changeset's summary becomes a bullet, and how a dependency bump is phrased. You need one when you want to change that text: link to a different forge, add attribution, prefix a category emoji, or reshape the bullet.

If you only want to change the **structure around the lines** -- the version heading, section titles, dates, ordering -- you do not need a generator at all. That is a [changelog template](/guides/changelog-templates), a Jinja2 file with no code. Reach for a generator only when the per-line text is what you want to change.

## 1. Lay out the package

A generator is an ordinary distribution. The smallest useful layout:

```bash
molt-changelog-emoji/
  pyproject.toml
  src/
    molt_changelog_emoji/
      __init__.py
      entry.md.jinja
```

## 2. Implement the contract

A generator provides `get_release_line` and `get_dependency_release_line`. molt owns the sections and spacing, so each function returns just the text for one bullet. This example buckets by a `category` key in the changeset front matter and renders each line through a Jinja2 template.

```python
# src/molt_changelog_emoji/__init__.py
from __future__ import annotations

from importlib.resources import files

from jinja2 import Template

from molt.changelog import Changeset, DependencyRelease, Forge

_LINE = Template((files(__package__) / "entry.md.jinja").read_text(encoding="utf-8"))

_EMOJI = {"feature": "sparkles", "fix": "wrench", "docs": "books"}


class EmojiGenerator:
    """Prefix each line with a category emoji and, on GitHub, a PR link."""

    def get_release_line(
        self,
        changeset: Changeset,
        bump: str,
        options: dict | None,
        forge: Forge | None,
    ) -> str:
        category = changeset.front_matter.get("category", "fix")
        link = ""
        if forge is not None and changeset.commit is not None:
            info = forge.commit_info(changeset.commit)
            if info is not None and info.pull is not None:
                link = f" ({info.pull.markdown_link})"
        return _LINE.render(
            emoji=_EMOJI.get(category, "package"),
            summary=changeset.summary.strip(),
            link=link,
        )

    def get_dependency_release_line(
        self,
        changesets: list[Changeset],
        dependencies: list[DependencyRelease],
        options: dict | None,
        forge: Forge | None,
    ) -> str:
        if not dependencies:
            return ""
        bumps = "\n".join(f"  - {d.name}@{d.new_version}" for d in dependencies)
        return f"- Updated dependencies\n{bumps}"


# The entry point resolves to this object.
generator = EmojiGenerator()
```

The template is plain Jinja2:

```jinja
{# src/molt_changelog_emoji/entry.md.jinja #}
- :{{ emoji }}: {{ summary }}{{ link }}
```

A few things this example demonstrates:

- **molt injects the forge.** `forge` is molt's cached, rate-limited [GitHub](/forges/github) adapter when GitHub is active, and `None` otherwise -- so the same generator degrades gracefully off a forge instead of crashing or opening its own client.
- **The forge hands back links, not ids.** `forge.commit_info(sha)` returns `None` when the forge has nothing for that commit, and otherwise an object with three parts: `info.commit`, `info.author` and `info.pull`. Each carries `.url` and a ready-made `.markdown_link`, so a generator never builds a URL itself -- that is what keeps the same generator working against a forge other than GitHub. `info.commit` is always present; `info.author` and `info.pull` can be `None`.
- **You never touch the file.** No reading `CHANGELOG.md`, no regex insertion, no blank-line juggling. You return one bullet's text; molt places it in the right `### Major/Minor/Patch Changes` section, keeps dependency bumps last in the patch section, and clamps the spacing. molt emits correct Markdown directly -- there is no formatter pass to repair it afterward.
- **Summaries are passed through literally.** molt does not run your changeset summary through a template engine or strip Markdown from it, so a `#` heading or a `$1` in a summary survives intact. (Both are upstream bugs molt refuses to port -- see [Design decisions](/reference/design-decisions).)

## 3. Register the entry point

Declare the `molt.changelog` entry point in the generator's `pyproject.toml`. The name on the left is what config will reference; the value on the right is `module:object`.

```toml
# molt-changelog-emoji/pyproject.toml
[project]
name = "molt-changelog-emoji"
version = "0.1.0"
dependencies = ["jinja2"]

[project.entry-points."molt.changelog"]
emoji = "molt_changelog_emoji:generator"
```

## 4. Install it and point config at it

Install the generator into the same environment as molt, then reference it by name:

```bash
uv add --dev molt-changelog-emoji
```

```toml
# your project's pyproject.toml
[tool.molt]
changelog = ["emoji", { repo = "acme/widgets" }]
```

The options table (`{ repo = "acme/widgets" }`) arrives as the `options` argument, verbatim. The next `molt version` uses your generator with no further wiring.

## Entry points, not shell hooks

molt extends **only** through named entry points like this one -- never through an arbitrary shell command in config. That is a deliberate refusal, not a missing feature:

- A shell `postversion` hook makes dependency-bump and changelog semantics undefined -- molt cannot reason about what the hook did to the tree.
- Executable config (a `changelog.py` that runs on parse) means no tool can read your settings without running your code -- it breaks static analysis, editor tooling, and any bot that inspects the repo.

A typed, importable generator gives you the same power with none of those costs: it is discoverable, testable in isolation, versioned as a dependency, and inert until molt calls it. See [Why molt](/introduction/why-molt) for the full list of what molt refuses and why.

## Where to go next

- [Changelog plugins](/extending/changelog-plugins) -- the contract and resolution rules in full.
- [Changelog templates](/guides/changelog-templates) -- change entry structure without writing code.
- [GitHub](/forges/github) -- the forge adapter your generator receives.
- [Options reference](/config/options) -- the `changelog` option.
