---
name: recover-albumpress-job
description: Diagnose and safely recover interrupted, failed, stale, or apparently stuck AlbumPress Studio calibration, separation, proof, video render, or audio export jobs. Use when the application was restarted, progress stopped, a job failed, Outputs look missing, or a user asks to resume, retry, cancel, or inspect processing.
---

# Recover AlbumPress Studio Job

Read `docs/agents/skill-runtime-contract.md` and the ADR relevant to the affected artifact.

1. Reproduce the problem with observational health, Project, and job-specific status endpoints. Identify job kind, ID, stage, last error, active process state, Outputs already promoted, and fingerprints.
2. Do not call the aggregate process status endpoints as a read-only check: they reconcile orphaned jobs and remove job-scoped temporary data. Explain that effect and obtain authorization before reconciliation. Never edit job JSON or delete locks manually.
3. Distinguish active, resumable, complete, failed, cancelled, and orphaned state. Preserve every fingerprint-valid Output.
4. If a job is active, monitor it unless the user explicitly asks to stop. Explain the next safe boundary before stopping.
5. If failed or interrupted, show the cause and reusable work. Ask before retrying sustained separation, render, audio export, or any force operation.
6. Use only the job-type retry/stop/recovery endpoint discovered in OpenAPI. After uncertain transport failure, read status before repeating a mutation.
7. Route artifact cleanup to `$maintain-project-storage`; recovery is not permission to delete evidence.

Report diagnosis, preserved artifacts, action taken, current stage, and whether the job is complete, pending authorization, or blocked.
