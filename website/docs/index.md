---
pageType: home

hero:
  name: Molt
  text: Changeset-driven versioning for Python
  tagline: Intent-based versioning, changelogs, and publishing for Python packages and monorepos. A Python-native port of changesets, built on PEP 440.
  actions:
    - theme: brand
      text: Get started
      link: /getting-started/installation
    - theme: alt
      text: Why Molt?
      link: /introduction/why-molt
    - theme: alt
      text: GitHub
      link: https://github.com/giancarlosisasi/molt

features:
  - title: PEP 440 native
    details: Version and specifier math built on packaging, not SemVer. Prereleases (a/b/rc/.dev), epochs, local versions, and PEP 503 name normalization are handled the way pip actually resolves them.
    icon: 🐍
  - title: Real monorepo propagation
    details: A fixpoint release-plan engine bumps dependents across your workspace and rewrites their constraints. This is the moat, the part two prior Python ports never reached.
    icon: 🔗
  - title: uv-first, ecosystem-agnostic
    details: First-class uv workspaces with lockfile updates on every version bump. A backend seam covers Poetry, Hatch, PDM, and setuptools instead of hard-wiring one tool.
    icon: ⚡
  - title: Single package or monorepo
    details: Single-package repos are first-class, not a degenerate special case. Most Python projects are one package, so molt treats that path as the default, not an afterthought.
    icon: 📦
  - title: A plan on every command
    details: add, version, publish, build, and git-tag each produce a machine-readable plan. Pass --dry-run to print exactly what would happen and write nothing to disk.
    icon: 📋
  - title: Prerelease is a flag
    details: molt version --pre rc. No pre.json branch state to leak, forget, or merge-conflict. Prerelease is an invocation, and it maps cleanly onto PEP 440 spellings.
    icon: 🚦
  - title: molt yank
    details: PyPI has no unpublish. molt makes PEP 592 yank a first-class verb: it checks the version, says whether it is already yanked, and prints the exact steps. PyPI has no yank API, so the final click is yours.
    icon: ♻️
  - title: Windows-correct from day one
    details: Tested on Windows from the first commit. No cp1252 surprises in output, no path bugs. Your release tool has to run in the CI you already have.
    icon: 🪟
---
