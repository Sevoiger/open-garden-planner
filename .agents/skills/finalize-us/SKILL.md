---
name: finalize-us
description: Commit, push and create or update the personal fork draft PR for completed OGP work.
---

# Deliver the fork change

Inspect status, branch, remotes and recent commits. Preserve unrelated work. Use
`gh` or `.tools/bin/gh` and target `Sevoiger/open-garden-planner` explicitly.
Reuse an existing draft PR for the branch; otherwise determine the fork default
base branch and create a draft. Include the actual result, checks already run,
material limits and only manual observations still needed. Use a body file.

Read the diff before staging explicit paths. Commit coherently and push to origin
without rewriting published history. Reuse passing validation; apply the risk-based
review policy in AGENTS.md. Do not turn finalization into a fresh full audit.
Inspect the PR state and available CI results; disclose pending or failed checks.
Stop at the draft PR. No merge, tag, release, version-sync PR or wiki publication.
If network/auth is unavailable, retain the local commit and state the precise blocker.
