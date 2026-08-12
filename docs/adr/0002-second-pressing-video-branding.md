---
status: accepted
---

# Treat Second Pressing as a snapshotted editorial layer over album-specific art direction

Second Pressing is the shared editorial identity of exported videos, while each album remains the dominant visual identity. The application must not force one palette, typeface, background, or genre language across projects. It applies a restrained Second Pressing signature around the album composition and snapshots the exact assets and settings used into the durable Album Project.

## Brand source and project snapshot

The user-owned brand library is configured through `ALBUMPRESS_BRAND_LIBRARY`. `STEM_COMPARISON_BRAND_LIBRARY` remains a compatibility alias. The library is an editable source, not a runtime dependency of an already prepared or rendered Album Project. When neither variable is set, the application uses the legacy-compatible `%LOCALAPPDATA%/StemComparison/branding/approved` directory.

When a project enables the Second Pressing profile, the application copies the selected monogram, wordmark, watermark export input, and brand configuration into `video/assets/brand/` inside that Project Folder. Preview, thumbnail generation, Reference Export, and Fast Export consume that immutable project snapshot. Updating the global brand library must not silently change or invalidate an existing project; adopting a newer brand revision is an explicit refresh.

## Visible application

- Opening ident: a restrained SP monogram appears over the album artwork for approximately 1.5–2 seconds while the first Track and the existing global fade-in begin. It adds no pre-roll or silence.
- Main album: the album artwork, album-specific typography, current Track, and progress treatment remain primary. No permanent Second Pressing mark is burned into the MP4 and the ident is not repeated at every Track boundary.
- Closing ident: during approximately the final 2–3 seconds of the effective timeline, Track information yields to the SP plus SECOND PRESSING lockup while the existing global audio/video fade-out completes. It does not extend the album with avoidable silence.
- Texture: the project grain/print treatment is composited above the brand elements as well as the artwork and type so that the ident belongs to the same visual object.
- Thumbnail: an optional small SP stamp, approximately 4–5% of frame width, may use a configurable safe corner. Bottom-right is not the default because YouTube overlays duration and player UI there. The stamp must not impose the channel palette on the album artwork.
- Platform watermark: the package exports a square, optimized SP asset for YouTube's native channel watermark rather than baking a persistent watermark into the MP4. It must be at least 150×150 pixels and below 1 MB according to current YouTube requirements.

Brand assets preserve the supplied marks exactly. The renderer must not regenerate the wordmark, approximate its typography, or add a slogan, catalog eyebrow, promotional call to action, waveform, visualizer, or generic music-player chrome.

## Considered options

- A permanent baked-in corner watermark was rejected because it competes with album art, duplicates YouTube's native channel watermark, and cannot be changed after export.
- A separate branded intro with extra runtime was rejected because it delays the music and becomes repetitive across full-album uploads.
- Applying the teal Second Pressing palette and typography to every album was rejected because it would erase the per-album art direction and make the catalogue visually homogeneous.
- Reading brand assets directly from the global library at render time was rejected because later edits would make old projects non-reproducible.

## Consequences

The Video configuration needs a small brand-profile surface rather than a general-purpose brand editor. Second Pressing can be enabled or disabled per project; thumbnail stamp position can be chosen when composition requires it; and an explicit refresh can replace the project snapshot after review. Both renderers and the package manifest must use and record the same snapshot, timing, asset hashes, and visibility settings. Automatic YouTube upload remains out of scope.

## Reference

- YouTube channel branding and video watermark requirements: https://support.google.com/youtube/answer/10456525
