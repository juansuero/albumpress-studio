# AlbumPress Studio

A local workspace for turning a source album into reviewed instrumental audio and video packages.

## Language

**Candidate**:
A version-producing separation configuration selected for comparison. A candidate can be either one Model or one Preset.
_Avoid_: Model when the configuration may be a preset, version

**Model**:
One trained separator identified by the filename exposed by the installed `audio-separator` catalogue.
_Avoid_: Preset

**Preset**:
A named `audio-separator` ensemble that combines two or more Models with a defined algorithm.
_Avoid_: Model

**Album Project**:
One input folder together with its detected Tracks, selected Candidates, generated Outputs, and saved Selections.
_Avoid_: Job, session

**Project Library**:
The user-visible directory that contains one or more Project Folders and provides the default home for new Album Projects.
_Avoid_: Temporary directory, cache

**Project Folder**:
The durable, user-visible directory that contains one Album Project's canonical manifest and every artifact generated for it. The source album may remain outside it as a read-only reference.
_Avoid_: Output workspace, temporary workspace

**Track**:
One source audio file detected inside an Album Project.
_Avoid_: Song when the source may not be a song

**Selection**:
The Candidate chosen as the preferred instrumental for one Track.
_Avoid_: Winner in persisted data

**Final Instrumental**:
The exported Output corresponding to a Track's current Selection.
_Avoid_: Master

**Audio Mix Package**:
A full-album MP3 with its chapters, metadata, cover, manifest, and provenance, assembled from the current Final Instrumentals.
_Avoid_: Upload, publication

**Album Video**:
One locally rendered full-album video assembled in Track order from the current Final Instrumentals.
_Avoid_: Upload, publication

**Video Package**:
An Album Video together with its thumbnail, chapter list, and description prepared for manual upload.
_Avoid_: YouTube upload
