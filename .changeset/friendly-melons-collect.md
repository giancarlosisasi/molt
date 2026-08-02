---
"molt-release": patch
---

Report the installed distribution version from `molt --version`. It read a literal in `molt/__init__.py` that `molt version` never rewrites, so it answered 0.1.0 for the 0.1.1 release.
