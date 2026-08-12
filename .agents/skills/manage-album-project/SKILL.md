---
name: manage-album-project
description: Open, create, preview, relink, rescan, or remove a recent AlbumPress Studio Album Project through the local application. Use when a user supplies an album folder or Project manifest, wants to switch Projects, repair a missing source link, refresh Track discovery, or inspect the Project Library.
---

# Manage Album Project

Read `docs/agents/skill-runtime-contract.md`, `CONTEXT.md`, and `docs/adr/0001-durable-project-library.md`.

1. Check health, preflight, settings, the Project Library, and the current Project.
2. For a source folder, preview Project creation before opening it. Show Project name, destination, discovered Tracks, unsupported files, and conflicts.
3. For a manifest, open the existing Project rather than creating a duplicate.
4. Create or open only after the source or manifest and destination are unambiguous. Keep the source read-only.
5. Rescan only through the application and explain Track changes before any downstream processing.
6. Relink a source only when the replacement identity is verified and the user approves the manifest change.
7. Removing a recent entry must not delete its Project Folder; state this before acting.

Do not migrate a Project Folder here; route that request to `$maintain-project-storage`. Report the canonical manifest, Project Folder, source state, Track count, and next safe workflow.
