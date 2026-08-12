---
status: accepted
---

# Use a human-readable Project Artifact Library with fail-closed cleanup

The Project Folder is the system of record for generated media. User-facing video
releases live under `video/releases/`, review material under `video/proofs/`, and
snapshotted assets use explicit `video/assets/artwork/`, `video/assets/fonts/` and
`video/assets/branding/` roles. Resumable work and small durable job summaries remain
hidden under `.stem-comparison/work/` and `.stem-comparison/jobs/`; caches and append-only
cleanup evidence remain hidden as well.

## Naming and identity

Release folders and media filenames are derived only from configured artist, album,
release state and human note. Windows-invalid characters, reserved names, trailing dots,
and repeated whitespace are sanitized deterministically, with a numeric suffix for a
collision. Technical render/job IDs remain in the release manifest and provenance, never
as the primary name a person must interpret.

The Project manifest records one immutable `artifactLibrary.currentRelease` pointer. A
release manifest records a stable release ID, relative paths, human label, state, hashes,
timestamps and provenance. The pointer is switched only after every staged artifact has
been validated. A separate Current folder or a second permanent MP4 copy is forbidden.

## Migration and compatibility

Legacy `video/packages/` and `video/renders/` remain readable. The current layout introduces a
versioned migration plan that maps registered legacy package artifacts into the new
layout, stages them under `.stem-comparison/work/video/<migration-id>/`, verifies bytes and
hashes, atomically promotes the new release folders and then removes only the verified
legacy source paths. The old manifest is retained in an audit backup so rollback can move
the exact files back and restore the previous pointer. Cancellation or interruption before
pointer promotion removes only staging; recovery after promotion finishes source cleanup
or rolls back from the saved plan. Reapplying an already-promoted plan is a no-op.

## Cleanup classes and protection

The deep `ProjectArtifactLibrary` class inventories registered and unregistered files and
assigns exactly one class:

- `Protected`: project manifest, Final Instrumentals, Selections, active assets, current
  release and active/resumable jobs.
- `Safe temporary`: completed staging, validated orphan chunks, verified duplicate render
  copies and synthetic artifacts accidentally inside a real Project Folder.
- `Review required`: superseded, rejected or Needs-fix releases, old proofs, unused assets,
  corrupt/ambiguous registrations and failed work inside its retention window.

Cleanup previews contain exact resolved paths, bytes, hashes, reasons and an inventory
fingerprint. Apply requires the unchanged fingerprint, exact registered paths inside the
Project Folder, no glob or broad root, and no symlink/junction escape. Any filesystem
change invalidates the plan. Apply appends a small audit record without recording media
contents. Successful render promotion may delete its validated staging through the same
seam; failed or interrupted work is retained for seven days and is never deleted while
active or resumable.

## Rollback and failure policy

All writes use same-volume staging and atomic replacement where possible. A failed hash,
manifest, path or state check fails closed and leaves the source layout untouched. The
library never guesses between multiple current releases, silently trusts a filename, or
deletes a path that is not present in the approved plan. Real-project migration and
cleanup require a separate explicit authorization after a read-only dry-run.

## Consequences

Render/package callers use the library seam for release resolution and promotion while
legacy readers continue to work. Synthetic fixtures prove cancellation, interruption,
rollback, idempotency, duplicate detection and path safety without touching user media.
The UI can explain storage in human terms while retaining the technical IDs and hashes in
the manifests and audit evidence.
