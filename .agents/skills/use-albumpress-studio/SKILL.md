---
name: use-albumpress-studio
description: Route natural-language requests to the correct AlbumPress Studio skill and operate the local application through its agent-safe workflows. Use when a user asks generally to use AlbumPress Studio, is unsure which workflow fits, combines several album operations, or wants an AI harness to take the next safe step.
---

# Use AlbumPress Studio

Read `docs/agents/skill-runtime-contract.md`, `CONTEXT.md`, and the current Project state.

Route by the user's intended outcome:

- Open, create, relink, or rescan a Project: `$manage-album-project`.
- Discover Candidates, calibrate, or separate Tracks: `$choose-separation-candidates`.
- Audition Outputs and approve, reject, loop, or select: `$review-instrumental-outputs`.
- Diagnose an interrupted, failed, or ambiguous job: `$recover-albumpress-job`.
- Prepare Proof, render, or package MP4: `$prepare-instrumental-video`.
- Export a full-album MP3 package: `$export-instrumental-audio`.
- Inspect, migrate, or clean Project artifacts: `$maintain-project-storage`.
- Carry an Album Project from source through requested local deliverables: `$produce-instrumental-album`.

When the request spans adjacent workflows, run them in dependency order and stop at each human gate. Do not load every specialized skill when one is sufficient. If the user's goal is ambiguous, inspect read-only state first, then ask only the choice that changes scope or authorization.

End with the current Project, observed state, action performed, evidence, and the exact next approval or blocker.
