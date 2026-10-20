---
name: ogp-change-control
description: Prepare OGP branches, commits and draft PRs within the personal fork delivery limits.
---

# Change control for the personal fork

The root AGENTS.md contains the active delivery policy. Start from a clean
understanding of existing work; use a feature branch and preserve owner data.
Before pushing, check the remote: only Sevoiger/open-garden-planner is authorized.
Use coherent conventional commits and update the branch's existing draft PR.

Apply the risk-based independent review policy in AGENTS.md. Review the actual
diff; resolve correctness/data/security findings and review risky corrections.
If review cannot run, disclose that limit in the draft PR. No merge, release,
tag, version-sync release PR or wiki publication is authorized in this fork.

Choose checks by changed behavior using ogp-validation-and-qa. UI text uses Qt
translation and complete compiled catalogs, checked by scripts/check_translations.py.
No unconditional full-suite, new integration-test or Windows-exe requirement.
No broad CI/dependency/coverage policy changes as a side effect of routine work.

Before removing a worktree, check status, untracked files, ignored non-cache data
and active use. Preserve unique files and a binary diff in an ignored local archive,
verify the archive, then remove the registered worktree. Do not use blanket git clean.
Published history remains stable unless the owner explicitly requests a rewrite.

## Provenance

Owner policy: root AGENTS.md, docs/fork/AGENT_WORKFLOW.md and
MISSION_AGENT_JARDIN.md when locally present. Upstream deployment history remains
in docs/07-deployment-view/ and the Git history; it does not extend fork authority.

## Historical cross-reference compatibility

### 2.5 Translation and integration evidence

Existing references to this section now mean Qt translation completeness plus
regression coverage proportional to the changed behavior. A new integration test
per edit is not required. See AGENTS.md for the current policy.

### 2.6 Version synchronization — upstream only

The upstream CI owns version tags; the personal fork does not merge, release or
perform release-triggered version synchronization. Historical pipeline details
are in docs/07-deployment-view/.

### 2.8 Frozen Windows build — upstream only

Historical references to smoke/selftest concern the upstream Windows deployment,
not this fork's acceptance. Use native macOS source execution here; do not run or
add Windows packaging checks. Upstream context: docs/07-deployment-view/.
