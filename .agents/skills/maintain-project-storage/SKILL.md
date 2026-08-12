---
name: maintain-project-storage
description: Inspect AlbumPress Studio Project artifacts, preview safe cleanup, apply a fingerprint-bound cleanup plan, preview or perform layout migration, and explain protected, temporary, and review-required files. Use when disk usage grows, artifacts need migration, a user asks what can be deleted, or Project storage needs repair.
---

# Maintain Project Storage

Read `docs/agents/skill-runtime-contract.md` and `docs/adr/0003-project-artifact-layout-and-safe-cleanup.md`.

1. Inspect the current Project, active jobs, artifact inventory, categories, hashes, bytes, provenance, and storage paths.
2. Explain `Protected`, `Safe temporary`, and `Review required` classifications. Never reinterpret a protected or ambiguous artifact as disposable.
3. Generate cleanup or migration previews through the application. A preview is read-only and authorizes no apply.
4. Show exact targets, reclaimed bytes, collisions, preservation plan, verification state, and plan fingerprint.
5. Refuse apply while relevant jobs are active, hashes are unverified, the plan is stale, or the destination conflicts.
6. Ask for explicit authorization naming the exact plan. Apply only with the fingerprint and migration ID required by the running OpenAPI schema.
7. Verify the post-state, current release pointer, preserved source/final artifacts, reclaimed bytes, and rollback metadata.

Never delete Project folders, source audio, Final Instrumentals, active staging, or model caches directly. Report whether applied changes are recoverable and how.
