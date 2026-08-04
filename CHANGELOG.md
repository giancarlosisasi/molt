# molt-release

## 0.2.0

### Minor Changes

- Add `molt doctor`: a read-only check of your workspace setup. It reports every configuration problem in one pass, names which packages a release would skip, and never prints a credential.

## 0.1.4

### Patch Changes

- Update documentation, merge sections and improve wording.

## 0.1.3

### Patch Changes

- Move the composite GitHub Action to its own repository, `giancarlosisasi/molt-action`. Workflows
  reference it as `uses: giancarlosisasi/molt-action@v1` instead of `uses: giancarlosisasi/molt@v1`;
  the inputs, the outputs and their defaults are unchanged, so the only edit a workflow needs is that
  line. The old `giancarlosisasi/molt@v1` reference no longer resolves.

  An action is versioned by a moving major ref and a Python distribution by immutable PEP 440 tags.
  Sharing one tag namespace meant molt's release workflow force-pushed `v1` into the same tag list
  that carries `v0.1.2`, and a reader of that list could not tell a release from a pointer.

  The action's conformance test moved with it and gained CI of its own: it resolves `action.yml`'s
  inputs against an installed `molt-release` rather than a local source tree, and runs on every push,
  weekly on a schedule, and again before `v1` moves.

- Create the GitHub Releases the publish phase always documented. The Action read the publish
  command's stdout to learn which packages went out, but `molt publish` announces each tag through
  molt's console, which writes every human-facing line to stderr so that stdout stays clean for
  `--output json`. The Action therefore saw an empty run every time: `molt-release` 0.1.0, 0.1.1 and
  0.1.2 each uploaded to PyPI, pushed a tag, created no GitHub Release, reported `published=false`
  and exited green.

  The published set now comes from the git-tag event stream `molt publish` already writes, and the
  Action names the destination for it. A publish command that is not molt keeps being read from its
  `New tag:` stdout lines, so a shell script or a `twine` wrapper is unaffected.

  The release step also reports itself now: each release created, each one the host already carried,
  and — when releases are switched on and nothing was reported as published — that it created none
  and why. A run that published nothing and a run that published something it could not detect both
  write `published=false`, and nothing in the log used to tell them apart.

## 0.1.2

### Patch Changes

- Give git a push credential in the GitHub Action. The Action never established one, so a workflow
  using the `persist-credentials: false` checkout its own guide recommends failed at the first push
  -- `could not read Username for 'https://github.com'` -- after the version phase had already
  computed the bump correctly. Both phases push, so the publish phase's tags were affected too. The
  token is supplied to git through the environment for the length of the Action's own step: it is
  never written to the runner's disk, and no later step in the workflow inherits it.

- Report the installed distribution version from `molt --version`. It read a literal in
  `molt/__init__.py` that `molt version` never rewrites, so it answered 0.1.0 for the 0.1.1 release.

- Deploy to pypi

## 0.1.1

### Patch Changes

- Documentation site and repository workflows only. Nothing in the `molt` command changed.

## 0.1.0

First release.
