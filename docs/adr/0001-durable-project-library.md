---
status: accepted
---

# Keep Album Projects in a durable user-visible Project Library

Album Projects and every generated artifact are stored in a durable Project Folder, normally under a user-selectable Project Library, rather than beside the source album or in a temporary directory. Source audio remains an external read-only reference, internal artifact paths are relative for portability, and the shared model cache remains in application data to avoid duplicating multi-gigabyte Models across projects.

## Considered options

- A sibling folder beside every source album was rejected because projects become scattered and source locations may be read-only or externally managed.
- Temporary or application-private project storage was rejected because users cannot reliably find, back up, move, or reuse their instrumentals and Video Packages.
- Copying every source album into its Project Folder was rejected as the default because it duplicates large audio libraries; a later explicit consolidation feature may provide portability when needed.

## Consequences

The application must expose exact Project Folder paths, support opening and relocating Project Folders, preserve usability when a source folder is unavailable, and migrate temporary validation projects without rerunning separation. Canonical project artifacts never depend on system temporary storage.
