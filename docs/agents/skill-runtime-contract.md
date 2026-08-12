# Agent runtime contract

This contract applies to every skill under `.agents/skills/`.

## Connect to the application

1. Resolve the repository root from the skill location or current working directory.
2. Use `ALBUMPRESS_URL` when set. Accept `STEM_COMPARISON_URL` as a compatibility alias; otherwise use `http://127.0.0.1:8765`.
3. Refuse non-loopback URLs. AlbumPress Studio is a local application.
4. Call `GET /api/health`. If the app is unavailable, use `scripts/launch.ps1` from the repository root and wait for health. Do not launch a second instance when one is already healthy.
5. Fetch `/openapi.json` before constructing request bodies. Treat the running application's schema as authoritative; do not guess payload fields from a skill.

Use the HTTP API for state and operations. Use the browser only when a human must audition audio, inspect images/video, choose a folder, or review an interaction. Never edit Project manifests, job JSON, Selections, Outputs, or generated artifacts directly.

## Read before writing

Before any mutation, inspect the relevant combination of:

- `/api/preflight`, `/api/settings`, `/api/projects`, `/api/projects/current`
- `/api/catalogue`
- Known job-specific reads such as `/api/process/album/{job_id}` and `/api/process/calibration/{job_id}`
- `/api/video/config`, `/api/video/proof`, `/api/video/package`
- `/api/audio/package`
- `/api/projects/storage/artifacts`

Record the current Project Folder, active job, fingerprints or revision fields, readiness, and blocking issues. A `404` for an optional current resource means absent state, not permission to invent it.

Do not assume `GET` means observational. In the current API, `/api/process/status` and `/api/process/calibration/status` reconcile orphaned jobs, rewrite their state, and remove their job-scoped temporary directory. Treat them as local mutations: explain that reconciliation may occur and require authorization before calling them. Prefer job-specific status reads when a job ID is already known.

## Authorization boundaries

A user's request authorizes ordinary local setup and the directly requested bounded operation. Ask immediately before:

- sustained full-album separation or force reprocessing;
- approving musical semantics or choosing a Final Instrumental the human has not auditioned;
- a real full-length MP4 or MP3 export, or an expensive retry;
- stopping an active job the user did not explicitly ask to stop;
- applying cleanup or migration plans;
- replacing artwork, fonts, branding snapshots, or source links when the choice is unresolved.

Authorization for one operation never authorizes another. Preview, synthetic, calibration, and dry-run evidence never count as real-content approval.

## Invariants

- Keep source audio read-only.
- Preserve fingerprint-valid Outputs and resume state.
- Use runtime catalogue IDs; never hardcode a Model or Preset.
- Keep concurrency and CPU safeguards controlled by the application.
- Do not upload, publish, obtain OAuth, or contact external services.
- Never claim a user listened to or approved media unless they explicitly did.
- Report `complete`, `pending approval`, `blocked`, or `failed` with observed evidence and local paths.

## Error handling

Treat HTTP `409` as a state conflict and surface the smallest corrective action. Treat `422` as an invalid request and re-check OpenAPI plus current state. On transport failure, re-check health once; do not repeat a mutation whose result is uncertain. Read job status before any retry.
