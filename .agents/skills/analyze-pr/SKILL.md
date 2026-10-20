---
name: analyze-pr
description: Review a specified OGP PR for concrete defects and select checks for its changed behavior.
---

# Review an OGP PR

Resolve the PR from the request or current branch using `gh` / `.tools/bin/gh`.
Read its base, head, diff and stated acceptance. Use the supplied repository; default
to the personal fork. If several targets are plausible, clarify the target.

Review read-only. Inspect through Git or a disposable worktree when checkout would
interfere with current work; do not ask the owner to clean their working tree.
Run checks only to answer concrete uncertainties in the diff. Reuse reported CI
and test evidence when adequate, distinguishing it from checks run here.

Report actionable defects with path, failure scenario and consequence. Prioritize
data loss, persistence/undo, Qt lifecycle and affected API contracts. No blanket full
suite, frozen executable, second reviewer or documentation quota. Mention only manual
checks that resolve an actual remaining uncertainty. Do not push or alter the PR.
