---
name: produce-instrumental-album
description: Safely create or resume an Album Project for instrumental listening, prepare separated Tracks, and produce a local MP4, MP3, or both through the application's approval gates. Use for requests to create an instrumental album, separate an album for instrumental listening, prepare a full-album video, generate a local upload-ready package, export a full-album MP3, or resume one of those workflows.
---

# Produce an instrumental album

Use the application as the system of record. Read `docs/agents/skill-runtime-contract.md`,
`CONTEXT.md`, the relevant accepted
ADRs, and the current Project/Job state before acting. Use the application's UI/API and
catalogue discovery; do not reproduce separation, timeline, rendering, packaging, or
validation logic in this skill.

## Workflow

1. Resolve the Project Library and open or create the Album Project. Keep source audio
   read-only and keep canonical artifacts in the durable Project Folder.
2. Inspect the manifest, Selections, Outputs, effective timeline, current Proof Pack,
   Audio Mix Package, active jobs, cache fingerprints, disk space, and required assets.
   Resume valid work instead of reprocessing it.
3. Ask only for unresolved high-impact choices: Project/source folder, desired outputs
   (`MP4`, `MP3`, or both), artwork, missing display font, branding profile, thumbnail,
   or a low-confidence tail decision. Take all minor settings from saved project defaults.
4. Discover the installed `audio-separator` Models and Presets through the application.
   Never assume a catalogue entry or hardcode a current model name. Reuse a recorded
   Candidate only when its fingerprint and semantic contract are still valid.
5. For an unvalidated Candidate, run only the application's bounded calibration first.
   Require human confirmation that the returned stem is the intended Instrumental before
   authorizing sustained album separation. Exclude invalid, inverted, incomplete, or
   semantically pending Outputs from Selection and Export.
6. Request explicit authorization before sustained separation, force processing, or an
   expensive retry. Reuse valid cached Outputs and recover interrupted jobs through the
   application. Never infer this authorization from an earlier approval.
7. Confirm or select Final Instrumentals through the application. Prepare the effective
   timeline and review only changed, low-confidence, or overridden tails with real audio;
   use the application's hysteresis/padding and override controls. Do not edit source WAVs.
8. Validate artwork, fonts, and any branding snapshot. Accept local `.ttf`/`.otf` files and
   snapshot validated files into the Project Folder; do not install fonts or substitute a
   missing font. Keep branding as a project snapshot and preserve album-specific art.
9. Generate a bounded Release Proof when the current proof is absent or stale. Stop for
   human visual/auditory approval before a sustained video render. A stale proof cannot
   unlock export.
10. Request separate explicit authorization for each sustained MP4 or MP3 export. Route
    each output through its application job; both must consume the same approved timeline
    fingerprint. Do not start the other output, a force operation, or an expensive retry
    implicitly.
11. Validate the resulting local package through the application, record paths, hashes,
    durations, provenance and approval state, then stop for human review. Keep YouTube,
    OAuth, uploads, publication and external sharing outside this skill.

## Hard gates and recovery

- Treat missing or corrupt Project/Output/Proof/Audio snapshots as blocked states. Do not
  guess, regenerate evidence, or promote temporary files.
- Treat stale Selections, invalid semantic status, missing fonts, insufficient disk, and
  contradictory job state as fail-closed. Explain the smallest corrective action.
- Recover an interrupted job using its application recovery path. Ask before repeating any
  sustained separation, full render, real MP3 export, force operation, or costly retry.
- Keep temporary calibration, proof and staging material outside the canonical Project
  Folder unless the application promotes it after validation.
- Report what is complete, pending human approval, blocked, or authorized; never claim a
  check that was not observed.

For decision defaults and the small set of questions, read [decision-defaults.md](references/decision-defaults.md).
For the output and human-approval gates, read [release-gates.md](references/release-gates.md).
