export type CheckStatus = "ready" | "missing" | "incompatible";

export type PreflightCheck = {
  key: string;
  label: string;
  status: CheckStatus;
  value: string | null;
  detail: string;
  action: string | null;
};

export type Preflight = {
  ready: boolean;
  platform: string;
  summary: string;
  checks: PreflightCheck[];
};

export type Settings = {
  schemaVersion: number;
  lastProjectManifest: string | null;
  lastSection: string;
  projectLibrary: string;
  recentProjects?: Array<{ manifestPath: string; lastOpenedAt?: string }>;
  modelCachePath?: string;
  logPath?: string;
};

export type ProjectSourceState = { status: "available" | "missing" | "changed" | "unreadable"; detail: string };
export type ProjectDescriptor = { projectId: string; projectName: string; albumName: string; projectFolder: string; manifestPath: string; sourceFolder: string; sourceState: ProjectSourceState; updatedAt: string; trackCount: number; selectionCount: number; origin: "library" | "recent" };
export type ProjectLibrary = { projectLibrary: string; projects: ProjectDescriptor[]; lastProjectManifest: string | null };
export type ProjectCreationPreview = { sourceFolder: string; projectName: string; projectFolder: string; projectLibrary: string; collision: boolean; collisionIndex: number; freeSpaceBytes: number; requiredFreeSpaceBytes: number; freeSpaceOk: boolean };
export type StorageLocations = { projectLibrary: string; modelCachePath: string; logPath: string };
export type StorageArtifact = { artifactId: string; path: string; area: string; bytes: number; sha256: string | null; hashSource: "manifest" | "verified" | "unverified"; category: "Protected" | "Safe temporary" | "Review required"; reason: string; registered: boolean; manifestPath?: string; role?: string };
export type ProjectStorage = { schemaVersion: number; layoutVersion: number; projectFolder: string; currentRelease: { layout: "legacy" | "new"; manifestPath: string; folder: string; releaseId: string; manifest: Record<string, unknown> } | null; inventoryFingerprint: string; artifacts: StorageArtifact[]; totals: Record<string, number>; reclaimableBytes: number; verifiedHashes: boolean; generatedAt: string };
export type CleanupPlan = { schemaVersion: number; planType: "cleanup"; projectFolder: string; inventoryFingerprint: string; planFingerprint: string; planFileSha256: string; targets: StorageArtifact[]; reclaimableBytes: number; unverifiedSafeBytes: number; unverifiedSafeCount: number; prunableDirectories: string[]; verifiedHashes: boolean; createdAt: string };
export type ArtifactMigrationPlan = { schemaVersion: number; planType: "layout-migration"; status: "planned" | "already_migrated"; projectFolder: string; migrationId?: string; planFingerprint?: string; bytes?: number; mappings: Array<{ sourceRelative: string; destinationRelative: string; bytes: number; sha256: string; role: string; technicalId?: string }>; releases: Array<{ technicalId: string; humanLabel: string; state: string; destinationFolder: string; sourceFolder: string; current: boolean }>; currentRelease?: Record<string, unknown>; currentDestinationFolder?: string; currentTechnicalId?: string; createdAt: string };
export type MigrationPreview = { sourceProjectFolder: string; destinationProjectFolder: string; destinationExists: boolean; destinationEmpty: boolean; artifactCount: number; bytes: number; registeredArtifactCount: number; canMigrate: boolean; preservationPlan: string };
export type MigrationResult = MigrationPreview & { status: string; copiedFiles: number; originalRetained: boolean; current: boolean; artifacts: Array<{ path: string; bytes: number; sha256: string }> };

export type Track = {
  trackId: string;
  sourcePath: string;
  title: string;
  sequence: number;
  extension: string;
  sizeBytes: number;
  modifiedTime: string;
  sourceFingerprint: string;
  durationSeconds: number;
  codec?: string | null;
};

export type ProjectManifest = {
  schemaVersion: number;
  projectId: string;
  albumName: string;
  projectName?: string;
  sourceFolder: string;
  outputFolder: string;
  projectFolder?: string;
  projectPaths?: Record<string, string>;
  sourceState?: ProjectSourceState;
  updatedAt: string;
  tracks: Track[];
  unsupportedFiles: Array<{ name: string; reason: string }>;
  candidates?: CandidateSelection[];
  outputs?: Record<string, CalibrationOutput>;
  tasks?: Record<string, ProjectTask>;
  loops?: Record<string, LoopState>;
  selections?: Record<string, SelectionState>;
  selectionSummary?: string;
  fastPath?: Record<string, unknown>;
};

export type VideoColors = { primary: string; secondary: string; accent: string; marker: string; scrim: string };
export type VideoTrack = { trackId: string; sequence: number; title: string; durationSeconds: number; outputId: string; slot: string; fileFingerprint: string | null; finalPath: string; startFrame?: number; durationInFrames?: number; audioUrl?: string; originalDurationSeconds?: number; effectiveDurationSeconds?: number; trailingSilenceSeconds?: number; retainedTailSeconds?: number; proposedRemovalSeconds?: number; silenceStatus?: string; silenceConfidence?: string; silenceReason?: string | null; preparationOverride?: string | null };
export type VideoPreparation = {
  schemaVersion: number;
  artworkMode: "Auto" | "Original";
  settings: { minimumSilenceSeconds: number; retainedTailSeconds: number; adaptiveNoiseDb: number; conservativeNoiseDb: number; reviewDisagreementSeconds: number; maxProposedRemovalSeconds: number; windowSeconds?: number; analysisSampleRate?: number; enterThresholdDb?: number; exitThresholdDb?: number; releaseHoldSeconds?: number; minimumActiveSeconds?: number; tailPaddingSeconds?: number; minimumTrimSeconds?: number; audioFadeInSeconds: number; audioFadeOutSeconds: number };
  trackOverrides: Record<string, string>;
  artwork: { source?: { path?: string; sha256?: string; width?: number; height?: number }; effective?: { path?: string; sha256?: string; width?: number; height?: number }; derived?: boolean; cacheHit?: boolean };
  texture: { path?: string; sha256?: string; width?: number; height?: number };
  summary: { tracksAnalyzed: number; secondsRemoved: number; reviewCount: number };
  status: "pending" | "ready" | "review" | string;
  updatedAt?: string | null;
};
export type VideoConfig = {
  schemaVersion: number;
  compositionId: string;
  width: number;
  height: number;
  fps: number;
  artist: string;
  album: string;
  typography: { displayFontFamily: string; utilityFontFamily: string };
  colors: VideoColors;
  cinematicFinish: "Off" | "Subtle" | "Textured";
  reducedMotion: boolean;
  descriptionNotes: string;
  brand: {
    enabled: boolean;
    profile?: string;
    revision?: string | null;
    libraryPath?: string;
    assets?: Record<string, { role?: string; path: string; sha256?: string; bytes?: number; mimeType?: string; width?: number; height?: number }>;
    timing?: { openingSeconds?: number; closingSeconds?: number };
    thumbnailStamp?: { enabled?: boolean; corner?: string; widthFraction?: number };
  };
  assets: Record<string, { role?: string; path: string; sha256?: string; bytes?: number; family?: string; mimeType?: string; width?: number; height?: number }>;
  tracks: VideoTrack[];
  preparation: VideoPreparation;
  provenance: Record<string, unknown>;
};
export type VideoConfigState = {
  status: "ready" | "blocked";
  ready: boolean;
  issues: string[];
  projectFolder: string;
  configPath: string;
  configRelativePath: string;
  config: VideoConfig;
  assets: VideoConfig["assets"];
  preparation: VideoPreparation;
  composition: { id: string; width: number; height: number; fps: number; durationInFrames: number; durationSeconds: number; timeline: VideoTrack[]; inputProps: Record<string, unknown> };
  provenance: Record<string, unknown>;
};

export type VideoRenderJob = {
  jobId: string;
  kind: string;
  sourceKind?: "synthetic" | "real";
  mode?: "fast" | "reference";
  status: "queued" | "running" | "stopping" | "complete" | "failed" | "cancelled" | "interrupted";
  stage: string;
  progress: number;
  message: string;
  concurrency?: number;
  stagingPath?: string | null;
  promotedPath?: string | null;
  renderManifestPath?: string | null;
  error?: string | null;
  validation?: { checks: Record<string, boolean>; ffprobe: Record<string, unknown>; sha256: string; bytes: number };
  renderer?: Record<string, unknown>;
  capability?: Record<string, unknown>;
  browser?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
};

export type VideoPackageState = {
  ready: boolean;
  status: "ready" | "missing" | "blocked";
  packageId?: string | null;
  packageFolder: string;
  manifestPath?: string | null;
  artifacts?: Record<string, { path: string; bytes: number; sha256: string }>;
  chapters?: string;
  description?: string;
  manifest?: Record<string, unknown>;
  issues?: string[];
};

export type AudioMetadata = {
  title: string;
  artist: string;
  album: string;
  albumArtist: string;
  year: string;
  genre: string;
  comment: string;
};

export type AudioPackageState = {
  ready: boolean;
  status: "ready" | "missing" | "blocked";
  packageId?: string | null;
  packageFolder: string;
  manifestPath?: string | null;
  artifacts?: Record<string, { path: string; bytes: number; sha256: string }>;
  manifest?: Record<string, unknown>;
  issues?: string[];
};

export type AudioPackageJob = {
  jobId: string;
  kind: string;
  status: "queued" | "running" | "stopping" | "complete" | "failed" | "cancelled" | "interrupted";
  stage: string;
  progress: number;
  message: string;
  inputFingerprint?: string;
  packageId?: string | null;
  promotedPath?: string | null;
  error?: string | null;
  manifest?: Record<string, unknown>;
};

export type VideoProofArtifact = {
  path: string;
  kind: string;
  caseId: string;
  selection: Record<string, unknown>;
  validation: Record<string, unknown>;
  evidenceFrames?: string[];
};

export type VideoProofState = {
  ready: boolean;
  status: "ready" | "missing" | "blocked";
  proofId?: string;
  proofFolder?: string;
  manifestPath?: string;
  inputFingerprint?: string;
  currentInputFingerprint?: string | null;
  approval: { status: "missing" | "pending" | "approved" | "rejected" | "stale"; approvedAt?: string | null; artifactHashes?: Record<string, string> };
  selection?: Record<string, Record<string, unknown>>;
  artifacts?: Record<string, VideoProofArtifact>;
  manifest?: Record<string, unknown>;
  issues?: string[];
};

export type VideoProofJob = {
  jobId: string;
  kind: string;
  sourceKind?: "synthetic" | "real";
  status: "queued" | "running" | "stopping" | "complete" | "failed" | "cancelled" | "interrupted";
  stage: string;
  progress: number;
  message: string;
  inputFingerprint?: string;
  promotedPath?: string | null;
  proofManifestPath?: string | null;
  error?: string | null;
  manifest?: Record<string, unknown>;
};

export type VideoTailAuditionCard = {
  trackId: string;
  sequence: number;
  title: string;
  currentSourceUrl: string;
  nextSourceUrl?: string | null;
  startSeconds: number;
  currentEndSeconds: number;
  proposedEndSeconds: number;
  nextPreviewSeconds: number;
  currentDurationSeconds: number;
  proposedDurationSeconds: number;
  removedSeconds: number;
  inputFingerprint: string;
  decision: "pending" | "keep-current" | "use-proposed";
  decisionUpdatedAt?: string | null;
};

export type VideoTailAuditionState = {
  schemaVersion: number;
  projectFolder: string;
  source: string;
  lookbackSeconds: number;
  nextTrackPreviewSeconds: number;
  cards: VideoTailAuditionCard[];
};

export type Candidate = {
  candidateId: string;
  type: "Model" | "Preset";
  label: string;
  engineIdentifier: string;
  technicalIdentifier: string;
  components: string[];
  algorithm: string | null;
  cacheState: string;
  targetStem?: string | null;
  stemOutputContract?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  processingProfile?: "fast" | "deep" | "slow" | string;
  defaultSlot?: string | null;
  onDemand?: boolean;
  reusableLoadedModel?: boolean;
  benchmark?: LocalBenchmark;
};

export type LocalBenchmark = { benchmarkId: string; inputSeconds: number; separationSeconds: number; wallClockSeconds: number; modelLoadSeconds: number; secondsPerSourceSecond: number; source: string };

export type CandidateSelection = Candidate & { slot: string };

export type CalibrationOutput = {
  outputId: string;
  taskId: string;
  trackId: string;
  slot: string;
  candidateId: string;
  stem: string;
  path: string;
  format: string;
  durationSeconds: number;
  fileFingerprint?: string;
  status: string;
  semanticStatus?: "pending" | "confirmed" | string;
  semanticConfirmedAt?: string;
  isPreview?: boolean;
  previewWindow?: { startSeconds: number; durationSeconds: number } | null;
  processedDurationSeconds?: number;
  engineStemName?: string;
  semanticValidation?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
};

export type ProjectTask = { taskId: string; kind: string; slot: string; candidateId: string; trackId: string; stage: string; error: string | null; outputId: string | null; returnedOutputs?: Array<Record<string, unknown>>; updatedAt: string; semanticStatus?: string; isPreview?: boolean };
export type LoopState = { trackId: string; inSeconds: number | null; outSeconds: number | null; enabled: boolean; updatedAt: string };
export type SelectionState = { trackId: string; trackTitle: string; slot: string; candidateId: string; outputId: string; outputFingerprint: string | null; selectedAt: string };
export type ExportItem = { trackId: string; trackTitle: string; sequence: number; slot: string | null; outputId: string | null; status: "valid" | "missing" | "invalid"; reason: string | null; sourcePath: string | null; destinationPath: string };
export type ExportPlan = { ready: boolean; destinationFolder: string; items: ExportItem[]; missing: Array<{ trackId: string; trackTitle: string; reason: string }>; selectionSummary: string };
export type ApproveSelectAllResult = { project: ProjectManifest; candidateSlot: string; candidateId: string; candidateLabel: string; pending: number; approved: number; results: Array<{ trackId: string; trackTitle: string; outputId?: string; status: string; reason?: string; slot?: string }> };

export type CalibrationTask = {
  taskId: string;
  slot: string;
  candidateId: string;
  candidateLabel: string;
  trackId: string;
  stage: string;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedSeconds: number | null;
  outputId: string | null;
  error: string | null;
  technicalError?: string | null;
  returnedOutputs?: Array<Record<string, unknown>>;
  estimatedSeconds?: number | null;
  previewOnly?: boolean;
};

export type CalibrationState = {
  jobId: string;
  kind: "calibration" | "album" | "candidate";
  status: "queued" | "running" | "complete" | "failed" | "stopped";
  stage: string;
  message: string;
  trackId: string | null;
  trackTitle: string | null;
  trackCount: number;
  startedAt: string;
  finishedAt: string | null;
  estimatedAlbumSeconds: number | null;
  estimateSource?: string | null;
  inputWindow?: { startSeconds: number; durationSeconds: number } | null;
  previewOnly?: boolean;
  tasks: CalibrationTask[];
  events: Array<{ eventId: number; at: string; stage: string; message: string; taskId: string | null; slot: string | null }>;
  hasFailures?: boolean;
};

export type CatalogueRecommendation = { candidateId: string; available: boolean; candidate: Candidate | null; reason: string | null };
export type Catalogue = { live: boolean; status: string; engine: { name: string; version: string | null }; generatedAt: string; recommendations: CatalogueRecommendation[]; candidates: Candidate[]; counts: { models: number; presets: number; total: number }; error: string | null; staleSnapshot?: Catalogue | null };

export async function fetchPreflight(): Promise<Preflight> {
  const response = await fetch("/api/preflight");
  if (!response.ok) throw new Error("Could not read local preflight");
  return response.json() as Promise<Preflight>;
}

export async function fetchSettings(): Promise<Settings> {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error("Could not read local settings");
  return response.json() as Promise<Settings>;
}

export async function saveSection(section: string): Promise<void> {
  const response = await fetch("/api/settings/lastSection", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value: section }),
  });
  if (!response.ok) throw new Error("Could not save the current section");
}

export async function saveSetting(name: string, value: unknown): Promise<Settings> {
  const response = await fetch(`/api/settings/${encodeURIComponent(name)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) });
  if (!response.ok) throw new Error("The setting could not be saved");
  return response.json() as Promise<Settings>;
}

export async function fetchProject(): Promise<ProjectManifest | null> {
  const response = await fetch("/api/projects/current");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Could not read the Album Project");
  return response.json() as Promise<ProjectManifest>;
}

export async function fetchVideoConfig(): Promise<VideoConfigState> {
  const response = await fetch("/api/video/config");
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Could not read Video configuration");
  return payload as VideoConfigState;
}

export async function configureVideo(payload: Partial<Pick<VideoConfig, "artist" | "album" | "colors" | "cinematicFinish" | "descriptionNotes" | "typography" | "reducedMotion">> & { preparation?: Partial<VideoPreparation>; artworkPath?: string; brand?: { enabled?: boolean; refresh?: boolean; libraryPath?: string; thumbnailStamp?: { enabled?: boolean; corner?: string; widthFraction?: number } } }): Promise<VideoConfigState> {
  const response = await fetch("/api/video/configure", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Video configuration could not be saved");
  return result as VideoConfigState;
}

export async function refreshVideoPreparation(payload: { artworkMode?: "Auto" | "Original"; trackOverrides?: Record<string, string>; settings?: Partial<VideoPreparation["settings"]>; force?: boolean } = {}): Promise<VideoConfigState> {
  const response = await fetch("/api/video/preparation/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Video preparation could not be refreshed");
  return result as VideoConfigState;
}

export async function startSyntheticVideoRender(): Promise<VideoRenderJob> {
  const response = await fetch("/api/video/render/synthetic", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Synthetic Video render could not start");
  return result as VideoRenderJob;
}

export async function startFastSyntheticVideoRender(): Promise<VideoRenderJob> {
  const response = await fetch("/api/video/render/fast/synthetic", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Fast synthetic Video render could not start");
  return result as VideoRenderJob;
}

export async function startFastRealVideoRender(): Promise<VideoRenderJob> {
  const response = await fetch("/api/video/render/fast/real", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Fast Video Export could not start");
  return result as VideoRenderJob;
}

export async function startReferenceVideoRender(): Promise<VideoRenderJob> {
  const response = await fetch("/api/video/render/real", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Reference Video Export could not start");
  return result as VideoRenderJob;
}

export async function fetchVideoRenderJob(jobId: string): Promise<VideoRenderJob> {
  const response = await fetch(`/api/video/render/${encodeURIComponent(jobId)}`);
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read Video render status");
  return result as VideoRenderJob;
}

export async function stopVideoRender(jobId: string): Promise<VideoRenderJob> {
  const response = await fetch(`/api/video/render/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Video render could not be stopped");
  return result as VideoRenderJob;
}

export async function retryVideoRender(jobId: string): Promise<VideoRenderJob> {
  const response = await fetch(`/api/video/render/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Video Export could not be retried");
  return result as VideoRenderJob;
}

export async function fetchVideoProof(): Promise<VideoProofState> {
  const response = await fetch("/api/video/proof");
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read the Release Proof Pack");
  return result as VideoProofState;
}

export async function fetchVideoTailAudition(): Promise<VideoTailAuditionState> {
  const response = await fetch("/api/video/tail-audition");
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read tail audition");
  return result as VideoTailAuditionState;
}

export async function saveVideoTailAuditionDecision(trackId: string, decision: VideoTailAuditionCard["decision"]): Promise<VideoTailAuditionState> {
  const response = await fetch(`/api/video/tail-audition/${encodeURIComponent(trackId)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Tail audition decision could not be saved");
  return result as VideoTailAuditionState;
}

export async function startVideoProof(): Promise<VideoProofJob> {
  const response = await fetch("/api/video/proof/generate", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Release Proof Pack could not start");
  return result as VideoProofJob;
}

export async function fetchVideoProofJob(jobId: string): Promise<VideoProofJob> {
  const response = await fetch(`/api/video/proof/jobs/${encodeURIComponent(jobId)}`);
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read Release Proof Pack status");
  return result as VideoProofJob;
}

export async function stopVideoProofJob(jobId: string): Promise<VideoProofJob> {
  const response = await fetch(`/api/video/proof/jobs/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Release Proof Pack could not be stopped");
  return result as VideoProofJob;
}

export async function retryVideoProofJob(jobId: string): Promise<VideoProofJob> {
  const response = await fetch(`/api/video/proof/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Release Proof Pack could not be retried");
  return result as VideoProofJob;
}

export async function approveVideoProof(proofId: string): Promise<VideoProofState> {
  const response = await fetch(`/api/video/proof/${encodeURIComponent(proofId)}/approve`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Proof Pack approval could not be saved");
  return result as VideoProofState;
}

export async function rejectVideoProof(proofId: string, reason?: string): Promise<VideoProofState> {
  const response = await fetch(`/api/video/proof/${encodeURIComponent(proofId)}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Proof Pack rejection could not be saved");
  return result as VideoProofState;
}

export async function fetchVideoPackage(): Promise<VideoPackageState> {
  const response = await fetch("/api/video/package");
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read Video Package");
  return result as VideoPackageState;
}

export async function generateSyntheticVideoPackage(notes: string): Promise<VideoPackageState> {
  const response = await fetch("/api/video/package/synthetic", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes }) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Synthetic Video Package could not be generated");
  return result as VideoPackageState;
}

export async function openVideoPackageFolder(): Promise<string> {
  const response = await fetch("/api/video/package/open-folder", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string; path?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Video Package folder could not be opened");
  return result?.path ?? "";
}

export async function fetchAudioPackage(): Promise<AudioPackageState> {
  const response = await fetch("/api/audio/package");
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read Audio Mix Package");
  return result as AudioPackageState;
}

export async function startAudioPackage(options: AudioMetadata & { coverChoice?: "artwork" | "thumbnail" | "custom" | "none"; customCoverPath?: string }, force = false): Promise<AudioPackageJob | AudioPackageState> {
  const response = await fetch("/api/audio/package/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ options, force }) });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Audio Mix Package could not start");
  return result as AudioPackageJob | AudioPackageState;
}

export async function fetchAudioPackageJob(jobId: string): Promise<AudioPackageJob> {
  const response = await fetch(`/api/audio/package/jobs/${encodeURIComponent(jobId)}`);
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Could not read Audio Mix Package status");
  return result as AudioPackageJob;
}

export async function stopAudioPackageJob(jobId: string): Promise<AudioPackageJob> {
  const response = await fetch(`/api/audio/package/jobs/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Audio Mix Package could not be stopped");
  return result as AudioPackageJob;
}

export async function retryAudioPackageJob(jobId: string): Promise<AudioPackageJob> {
  const response = await fetch(`/api/audio/package/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Audio Mix Package could not be retried");
  return result as AudioPackageJob;
}

export async function openAudioPackageFolder(): Promise<string> {
  const response = await fetch("/api/audio/package/open-folder", { method: "POST" });
  const result = await response.json().catch(() => null) as { detail?: string; path?: string } | null;
  if (!response.ok) throw new Error(result?.detail ?? "Audio Mix Package folder could not be opened");
  return result?.path ?? "";
}

export async function pickAlbumFolder(): Promise<string | null> {
  const response = await fetch("/api/projects/pick-folder", { method: "POST" });
  if (!response.ok) throw new Error("The native folder picker could not open");
  const result = await response.json() as { path: string | null };
  return result.path;
}

export async function fetchProjectLibrary(): Promise<ProjectLibrary> {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error("Could not discover Project Library projects");
  return response.json() as Promise<ProjectLibrary>;
}

export async function previewProject(sourcePath: string, options: { projectName?: string; projectLibrary?: string; projectFolder?: string } = {}): Promise<ProjectCreationPreview> {
  const response = await fetch("/api/projects/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sourcePath, ...options }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Project Folder could not be resolved");
  return payload as ProjectCreationPreview;
}

export async function openProject(payload: string | { sourcePath?: string; manifestPath?: string; projectName?: string; projectLibrary?: string; projectFolder?: string }): Promise<ProjectManifest> {
  const body = typeof payload === "string" ? { sourcePath: payload } : payload;
  const response = await fetch("/api/projects/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? "The Album Project could not be opened");
  }
  return response.json() as Promise<ProjectManifest>;
}

export async function openProjectFolder(manifestPath?: string): Promise<string> {
  const response = await fetch("/api/projects/open-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(manifestPath ? { manifestPath } : {}) });
  const payload = await response.json().catch(() => null) as { detail?: string; path?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Project Folder could not be opened");
  return payload?.path ?? "";
}

export async function removeRecentProject(manifestPath: string): Promise<ProjectLibrary> {
  const response = await fetch("/api/projects/remove-recent", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ manifestPath }) });
  if (!response.ok) throw new Error("The project could not be removed from recent");
  return response.json() as Promise<ProjectLibrary>;
}

export async function relinkProjectSource(sourcePath: string): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/relink-source", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sourcePath }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The source folder could not be relinked");
  return payload as ProjectManifest;
}

export async function previewProjectMigration(destinationPath: string, manifestPath?: string): Promise<MigrationPreview> {
  const response = await fetch("/api/projects/migration-preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ destinationPath, ...(manifestPath ? { manifestPath } : {}) }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The migration could not be previewed");
  return payload as MigrationPreview;
}

export async function migrateProject(destinationPath: string, manifestPath?: string): Promise<MigrationResult> {
  const response = await fetch("/api/projects/migrate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ destinationPath, ...(manifestPath ? { manifestPath } : {}) }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Project Folder could not be migrated");
  return payload as MigrationResult;
}

export async function fetchStorage(): Promise<StorageLocations> {
  const response = await fetch("/api/storage");
  if (!response.ok) throw new Error("Could not read storage locations");
  return response.json() as Promise<StorageLocations>;
}

export async function openStorage(kind: "projectLibrary" | "modelCache" | "logs"): Promise<string> {
  const response = await fetch("/api/storage/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }) });
  if (!response.ok) throw new Error("The storage location could not be opened");
  return (await response.json() as { path: string }).path;
}

export async function fetchProjectStorage(): Promise<ProjectStorage> {
  const response = await fetch("/api/projects/storage/artifacts");
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Could not read Project Storage");
  return payload as ProjectStorage;
}

export async function previewArtifactCleanup(verifyHashes = false): Promise<CleanupPlan> {
  const response = await fetch("/api/projects/storage/cleanup/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verifyHashes }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The cleanup preview could not be prepared");
  return payload as CleanupPlan;
}

export async function applyArtifactCleanup(plan: CleanupPlan): Promise<{ status: string; bytes: number; deleted: StorageArtifact[]; prunedDirectories: string[]; skippedDirectories: Array<{ path: string; reason: string }> }> {
  const response = await fetch("/api/projects/storage/cleanup/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan, confirmFingerprint: plan.planFingerprint, confirmPlanFileSha256: plan.planFileSha256 }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The cleanup could not be applied");
  return payload as { status: string; bytes: number; deleted: StorageArtifact[]; prunedDirectories: string[]; skippedDirectories: Array<{ path: string; reason: string }> };
}

export async function previewArtifactMigration(): Promise<ArtifactMigrationPlan> {
  const response = await fetch("/api/projects/storage/migration/preview", { method: "POST" });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The layout migration could not be planned");
  return payload as ArtifactMigrationPlan;
}

export async function rescanProject(): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/rescan", { method: "POST" });
  if (!response.ok) throw new Error("The Album Project could not be rescanned");
  return response.json() as Promise<ProjectManifest>;
}

export async function fetchCatalogue(refresh = false): Promise<Catalogue> {
  const response = await fetch(refresh ? "/api/catalogue/refresh" : "/api/catalogue", { method: refresh ? "POST" : "GET" });
  if (!response.ok) throw new Error("The installed Candidate catalogue could not be read");
  return response.json() as Promise<Catalogue>;
}

export async function saveCandidateSlots(slots: Record<string, CandidateSelection | null>): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/candidates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slots }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Candidate choices could not be saved");
  return payload as ProjectManifest;
}

export async function setFastDefaultCandidates(): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/candidates/fast-default", { method: "POST" });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Fast Candidate default could not be applied");
  return payload as ProjectManifest;
}

export async function saveLoop(trackId: string, loop: { inSeconds: number | null; outSeconds: number | null; enabled: boolean }): Promise<ProjectManifest> {
  const response = await fetch(`/api/projects/loops/${encodeURIComponent(trackId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(loop) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Loop state could not be saved");
  return payload as ProjectManifest;
}

export async function saveSelection(trackId: string, slot: string, outputId?: string): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/selections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trackId, slot, outputId }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Selection could not be saved");
  return payload as ProjectManifest;
}

export async function confirmOutputSemantics(outputId: string): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/outputs/semantic-confirmation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ outputId }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Output could not be semantically confirmed");
  return payload as ProjectManifest;
}

export async function approveAndSelectOutput(trackId: string, slot: string, outputId?: string): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/outputs/approve-select", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trackId, slot, outputId }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Output could not be approved and selected");
  return payload as ProjectManifest;
}

export async function approveAndSelectAll(): Promise<ApproveSelectAllResult> {
  const response = await fetch("/api/projects/outputs/approve-select-all", { method: "POST" });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Album Project could not be approved and selected");
  return payload as ApproveSelectAllResult;
}

export async function invalidateOutput(outputId: string, reason?: string): Promise<ProjectManifest> {
  const response = await fetch("/api/projects/outputs/invalidate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ outputId, reason }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The Output could not be rejected");
  return payload as ProjectManifest;
}

export async function fetchExportStatus(): Promise<ExportPlan> {
  const response = await fetch("/api/projects/export/status");
  if (!response.ok) throw new Error("Could not read export status");
  return response.json() as Promise<ExportPlan>;
}

export async function exportProject(destinationPath?: string): Promise<{ status: string; destinationFolder: string; items: ExportItem[]; selectionSummary: string }> {
  const response = await fetch("/api/projects/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(destinationPath ? { destinationPath } : {}) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Export could not complete");
  return payload as { status: string; destinationFolder: string; items: ExportItem[]; selectionSummary: string };
}

export async function openExportFolder(): Promise<string> {
  const response = await fetch("/api/projects/export/open-folder", { method: "POST" });
  if (!response.ok) throw new Error("The export folder could not open");
  return (await response.json() as { path: string }).path;
}

export async function fetchCalibrationStatus(): Promise<CalibrationState | null> {
  const response = await fetch("/api/process/calibration/status");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("Could not read calibration status");
  return response.json() as Promise<CalibrationState>;
}

export async function startCalibration(trackId?: string, preview?: { startSeconds: number; durationSeconds: number }): Promise<CalibrationState> {
  const body = {
    ...(trackId ? { trackId } : {}),
    ...(preview ? { previewStartSeconds: preview.startSeconds, previewDurationSeconds: preview.durationSeconds } : {}),
  };
  const response = await fetch("/api/process/calibration", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Calibration could not start");
  return payload as CalibrationState;
}

export async function stopCalibration(jobId: string): Promise<CalibrationState> {
  const response = await fetch(`/api/process/calibration/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
  if (!response.ok) throw new Error("Calibration could not be stopped");
  return response.json() as Promise<CalibrationState>;
}

export async function skipCalibration(): Promise<ProjectManifest> {
  const response = await fetch("/api/process/calibration/skip", { method: "POST" });
  if (!response.ok) throw new Error("Calibration could not be skipped");
  return response.json() as Promise<ProjectManifest>;
}

export async function fetchCalibrationJob(jobId: string): Promise<CalibrationState> {
  const response = await fetch(`/api/process/calibration/${encodeURIComponent(jobId)}`);
  if (!response.ok) throw new Error("Could not read calibration job");
  return response.json() as Promise<CalibrationState>;
}

export async function startAlbumProcessing(): Promise<CalibrationState> {
  const response = await fetch("/api/process/album", { method: "POST" });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Album processing could not start");
  return payload as CalibrationState;
}

export async function startCandidateForTrack(trackId: string, slot: string): Promise<CalibrationState> {
  const response = await fetch("/api/process/candidate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trackId, slot }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "Candidate processing could not start");
  return payload as CalibrationState;
}

export async function fetchAlbumJob(jobId: string): Promise<CalibrationState> {
  const response = await fetch(`/api/process/album/${encodeURIComponent(jobId)}`);
  if (!response.ok) throw new Error("Could not read album processing job");
  return response.json() as Promise<CalibrationState>;
}

export async function stopAlbumProcessing(jobId: string): Promise<CalibrationState> {
  const response = await fetch(`/api/process/album/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
  if (!response.ok) throw new Error("Album processing could not be stopped");
  return response.json() as Promise<CalibrationState>;
}

export async function retryProcessing(scope: "output" | "track" | "candidate" | "remaining", trackId?: string, slot?: string, force = false): Promise<CalibrationState> {
  const response = await fetch(force ? "/api/process/reprocess" : "/api/process/retry", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope, trackId, slot }) });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? "The requested retry could not start");
  return payload as CalibrationState;
}
