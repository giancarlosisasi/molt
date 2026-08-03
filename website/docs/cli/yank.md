---
title: molt yank
---

# molt yank

Check a released version and print the exact steps to yank it on PyPI (PEP 592).

> **`molt yank` does not yank for you.** PyPI provides no supported way for a tool to yank a release: it is a web-UI action, there is no API endpoint, and an upload token does not grant it. So `molt yank` does the part it can: verify the version, tell you whether it is already yanked, and hand you the exact URL and steps. You complete the yank in your browser. See [The manual step](#the-manual-step).

## Synopsis

```bash
molt yank <package> <version> [OPTIONS]
```

## Description

PyPI is immutable: a published version cannot be deleted or overwritten. But PEP 592 lets you **yank** it. A yanked version is still installable by an exact pin that already depends on it, so nothing that already resolved to it breaks -- but resolvers stop *selecting* it for new installs. It is the correct response to a release that is broken but must remain downloadable for reproducibility.

`molt yank` is the guided front end to that. It reads the index's public JSON API and reports:

- whether `<package>` exists and `<version>` is actually on the index,
- whether that version is **already yanked**, and the reason recorded on it,
- the **release-management URL** and the steps to complete the yank, with your `--reason` text ready to paste.

It contacts only the public read API, so it needs **no credentials** and changes nothing. Run it before you go clicking -- it catches the typo'd version number that would otherwise have you yanking the wrong release.

### The manual step

PEP 592 specifies how installers *read* a yank, through the `data-yanked` attribute in the Simple API. It leaves it to each index to decide how a maintainer *sets* one. PyPI implements setting it as a web form on the release-management page only:

- there is **no documented API endpoint** for yanking,
- `twine` has **no yank command**,
- PyPI **API tokens are scoped to uploads**, so the credential your CI already holds could not perform a yank even if an endpoint existed. Project management requires a logged-in session with 2FA,
- only a project **Owner** may yank; a Maintainer cannot.

Molt does not drive an undocumented HTML form with your account password to work around that. Some third-party indexes, such as devpi and Artifactory, do expose yank APIs; `molt yank` reports on those indexes but does not complete the yank on them either.

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `<package>` | string (positional) | -- | Distribution name to check. Required. |
| `<version>` | string (positional) | -- | Exact version to check. Required. |
| `--reason <text>` | string | -- | The reason to record. Echoed in the printed steps, ready to paste into PyPI's confirmation dialog. |
| `--undo` | flag | off | Print the steps to **un-yank** instead -- restore the version to normal selection. |
| `--repository <name>` | string | `pypi` | Named repository/index the version lives on. Selects which index is queried and which management URL is printed. |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

`molt yank` takes no `--dry-run`: it never mutates, so every run is already a dry run. It takes no `--yes` either -- there is nothing to confirm.

## Exit codes

| Situation | Exit code |
|---|---|
| Version found; steps printed | 0 |
| Version found and already in the requested state (already yanked, or `--undo` on a version that is not yanked) | 0 |
| Package or version not found on the index | 1 |
| The index could not be reached | 1 |

## Examples

Check a broken patch and get the steps, with the reason ready to paste:

```bash
molt yank acme-core 1.1.0 --reason "Corrupt wheel; use 1.1.1."
```

```text
acme-core 1.1.0 is on pypi.org and is not yanked.

To yank it, PyPI requires you to do this in a browser -- there is no API for it:

  1. Open https://pypi.org/manage/project/acme-core/releases/
  2. Click Options next to 1.1.0, then Yank
  3. Paste this reason: Corrupt wheel; use 1.1.1.
  4. Confirm

You must be an Owner of the project, signed in with 2FA.
```

Check the steps to un-yank once the record is corrected:

```bash
molt yank acme-core 1.1.0 --undo
```

Check a version on another index:

```bash
molt yank acme-core 1.1.0 --repository testpypi
```

## See also

- [Yanking a release](/guide/yank) -- when to yank, and when to release a fix instead.
- [molt publish](/cli/publish).
