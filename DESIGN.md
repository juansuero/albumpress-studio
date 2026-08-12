# AlbumPress Studio — built visual system

## Direction

The Listening Archive is a dark, local-first workspace for careful audio decisions. The interface is quiet, high-contrast, and text-led: every processing state has a name, a slot, and a non-color cue. Listening actions remain visually static so the user can concentrate on the shared musical timestamp.

## Tokens

- Canvas: `oklch(0.17 0.012 220)`
- Surface: `oklch(0.22 0.014 220)`
- Raised surface: `oklch(0.26 0.016 220)`
- Primary text: `oklch(0.93 0.012 90)`
- Secondary text: `oklch(0.86 0.018 210)`
- Interactive blue: `oklch(0.77 0.1 245)`
- Success: `oklch(0.76 0.1 145)`
- Warning: `oklch(0.8 0.13 85)`
- Danger: `oklch(0.78 0.12 25)`
- Font: Atkinson Hyperlegible Next Variable

## Layout

The fixed-width navigation rail establishes the local workspace. Content uses a 1240px maximum measure, 24px panel gaps, 12px panel corners, and an 8px control radius. At 1100px the rail collapses to an icon rail; at 780px panels and controls stack; at 520px padding and transport controls compress.

## Interaction rules

- Candidate slots use A/B/C/D labels, readable text, disabled unavailable states, and visible selection state.
- Process stages use text and status styling; the UI never invents a percentage.
- Compare preloads only the current Track's registered Outputs, mutes inactive elements, preserves time/play state on switch, and releases elements when the Track changes.
- Listening controls have no visual animation, crossfade, or gain normalization.
- Destructive or uncertain actions use explicit confirmation: skip calibration, Force reprocess, and export only after completeness passes.

## Accessibility

The implementation includes a skip link, semantic headings/tables, labeled selects/range controls/audio elements, visible focus outlines, keyboard equivalents for listening actions, focus suppression for editable controls, status/live regions, reduced-motion and forced-colors CSS handling, and non-color state text. The release review verified no horizontal overflow at 1024, 1280, or 1920px CSS widths and no active animations on the Compare surface.

## Album Video v3

Album Landscape is a shared 1920×1080, 30 fps composition. The supplied Little Songs artwork is full-bleed with a restrained scrim and textured finish; the artist/album identity stays left, while the two-digit Track marker, current title, timestamps, and progress line stay right. The package thumbnail is the same hierarchy rendered at 1280×720. It must preserve the complete artwork canvas: thumbnail post-processing must never mask or crop the right side.

Video configuration and copied assets live inside the permanent Project Folder. The workflow is staged as configuration and Player preview, one bounded synthetic render, package validation, and—only after explicit authorization—one sustained real-album render. A real package reuses the validated MP4 and records its render snapshot, current HQ5 Final Instrumentals, hashes, FFprobe checks, chapters, and description.

The Player honors the Reduced motion setting by removing the authored drift/bloom motion while preserving the same hierarchy and controls. Keyboard focus remains visible, long Track titles wrap inside the title column, and the 1024px and 1920px layouts must not introduce horizontal overflow.

The application creates a local Video Package but does not upload it. Manual upload is the user's responsibility: inspect the MP4, thumbnail, chapters, description, and manifest, then upload them through the chosen platform's own interface. Juan must hold the necessary rights or permissions for the supplied artwork, source audio, Final Instrumentals, fonts, and any platform upload; the application does not clear copyright, publishing, synchronization, or Content ID claims.
