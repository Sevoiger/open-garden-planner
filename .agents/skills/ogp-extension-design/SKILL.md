---
name: ogp-extension-design
description: Extend OGP catalogs, commands or integrations while keeping the personal fork small and compatible.
---

# Extend an existing boundary

Trace one real caller from UI/API to service, command and storage. Find the smallest
existing boundary that supports the requested variant. Prefer a data entry, provider
or focused parameter over a second parallel implementation or general plugin framework.

State what callers and persisted data must retain. Keep Qt work on its existing
thread boundary and pure calculations independent of UI where the current structure
allows it. Preserve unknown fields and undo semantics; consult the architecture
contract only for the boundary affected.

Demonstrate both the new case and a representative existing caller with existing
fixtures. Add a regression for any uncovered compatibility risk. A short explanation
of where the next variant belongs is enough; an ADR is needed only for a durable
architectural decision. Do not invent extension needs beyond the user request.
