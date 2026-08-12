---
name: prepare-instrumental-video
description: Configure Album Landscape assets, prepare the effective timeline, review tail decisions, generate and approve a bounded Proof Pack, render a real or synthetic video, and create a validated local Video Package. Use when a user asks for an MP4, video preview, proof, thumbnail, chapters, or upload-ready local video package.
---

# Prepare Instrumental Video

Read `docs/agents/skill-runtime-contract.md`, `docs/adr/0002-second-pressing-video-branding.md`, and `docs/adr/0003-project-artifact-layout-and-safe-cleanup.md`.

1. Inspect current Selections, video config, preparation state, tail auditions, Proof Pack, render job, package, assets, and disk preflight.
2. Configure only supplied or already snapshotted artwork, fonts, and optional branding. Do not install fonts, approximate marks, or silently replace missing assets.
3. Refresh preparation and surface changed, overridden, or low-confidence tails. Expose real A/B tail audio and persist only the human's decision.
4. Generate a bounded Proof Pack when absent or stale. Synthetic proof demonstrates mechanics only.
5. Present proof images/clips to the user and stop for explicit visual and auditory approval. Reject or regenerate through the application when requested.
6. Before a real full-length render, report Project Folder, proof/timeline fingerprints, renderer mode, estimate, disk state, and active jobs; request explicit authorization.
7. Monitor the authorized job. Do not retry, switch renderer, or create a different output implicitly.
8. Create and validate the local Video Package from the approved render. Report MP4, thumbnail, chapters, description, manifest, hashes, duration, and provenance.

Do not upload or publish. A ready package remains pending human final review.
