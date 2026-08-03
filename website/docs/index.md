---
# Keep every prose value double-quoted. This block is YAML, so an unquoted value
# containing a colon-space -- "... a first-class verb: it checks ..." -- parses as a
# nested mapping and throws. Rspress swallows that error (@rspress/shared
# `loadFrontMatter` catches and returns {}), so the page silently loses `pageType:
# home` and renders this whole block as body text instead of the hero + feature grid.
pageType: home

hero:
  name: Molt
  text: Changeset-driven versioning for Python
  tagline: "Versioning, changelogs, and publishing for Python packages and monorepos, driven by small files you write as you work. Built on PEP 440 and PEP 508."
  actions:
    - theme: brand
      text: Get started
      link: /getting-started/installation
    - theme: alt
      text: How it works
      link: /introduction/the-changeset-workflow
    - theme: alt
      text: GitHub
      link: https://github.com/giancarlosisasi/molt

features:
  - title: PEP 440 versions
    details: "Version and specifier math built on packaging. Prereleases (a/b/rc/.dev), epochs, local versions, and PEP 503 name normalization are handled the way pip and uv actually resolve them."
    icon: 🐍
  - title: Monorepo propagation
    details: "A fixpoint release-plan engine bumps dependents across your workspace and rewrites their PEP 508 constraints. A dependent is released when the new version leaves its declared range, and not before."
    icon: 🔗
  - title: uv workspaces
    details: "Workspace members are discovered from your uv layout, and uv.lock is refreshed in the same commit that bumps a version, so a --frozen install in CI still resolves."
    icon: ⚡
  - title: Single package or monorepo
    details: "Most Python projects ship one package, so that is molt's default path. molt init, add, version, and publish work end to end with no workspace configuration."
    icon: 📦
  - title: Plans and dry runs
    details: "add, version, publish, build, and git-tag each produce a machine-readable plan. Pass --dry-run to print exactly what would happen and write nothing to disk."
    icon: 📋
  - title: Prereleases
    details: "molt version --pre rc cuts release candidates for one run. There is no mode to enter or exit and no state file to commit, and the spellings map onto PEP 440."
    icon: 🚦
  - title: Yanking a release
    details: "PyPI keeps every version forever. molt yank checks the version, reports whether it is already yanked, and prints the PEP 592 steps. PyPI publishes no yank API, so the last click is yours."
    icon: ♻️
  - title: Test suite
    details: "2,104 tests cover the release-plan engine, the CLI, the changelog, and the publish path, with property-based cases over PEP 440 bump math."
    icon: 🧪
---
