---
name: export-instrumental-audio
description: Prepare and export a validated local full-album instrumental audio package from current Final Selections. Use when a user asks for an MP3, album audio mix, chapters, CUE, metadata, cover artwork, or a resumable audio export job.
---

# Export Instrumental Audio

Read `docs/agents/skill-runtime-contract.md` and `CONTEXT.md`.

1. Inspect current Selections, semantic validation, effective timeline, audio package state, active job, metadata, cover choice, and disk preflight.
2. Block on missing, stale, preview, invalid, or semantically pending Final Instrumentals. Never substitute a Candidate.
3. Reuse Project metadata and approved timeline defaults. Ask only for unresolved artist/album metadata, cover choice, or a scope-changing option.
4. Before a real export, report Project Folder, Track count, timeline fingerprint, effective duration, encoding/metadata settings, estimate, and destination; request explicit authorization.
5. Start only the audio job. Authorization for MP3 does not authorize MP4 or publication.
6. Monitor through the job endpoint. Stop or retry only when explicitly requested after showing current state and reusable work.
7. Validate the promoted package: MP3 duration, encoding, chapters/CUE, metadata, cover, hashes, manifest, source timeline, and provenance.

Report the local package path and validation evidence. Do not upload, publish, or claim musical approval beyond the persisted Selections.
