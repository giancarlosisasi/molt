---
"molt-release": patch
---

Create the GitHub Releases the publish phase always documented. The Action read the publish
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
