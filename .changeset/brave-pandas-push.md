---
"molt-release": patch
---

Give git a push credential in the GitHub Action. The Action never established one, so a workflow using the `persist-credentials: false` checkout its own guide recommends failed at the first push -- `could not read Username for 'https://github.com'` -- after the version phase had already computed the bump correctly. Both phases push, so the publish phase's tags were affected too. The token is supplied to git through the environment for the length of the Action's own step: it is never written to the runner's disk, and no later step in the workflow inherits it.
