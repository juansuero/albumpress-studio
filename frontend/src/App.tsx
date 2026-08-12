import { useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { Player } from "@remotion/player";
import {
  ArrowClockwise,
  ArrowSquareOut,
  CheckCircle,
  CircleNotch,
  Export,
  FilmStrip,
  FolderOpen,
  Gauge,
  House,
  ListChecks,
  MusicNotes,
  ShieldCheck,
  SlidersHorizontal,
  SpeakerHigh,
  WarningCircle,
  Waveform,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { applyArtifactCleanup, approveAndSelectAll, approveAndSelectOutput, approveVideoProof, configureVideo, exportProject, fetchAudioPackage, fetchAudioPackageJob, fetchExportStatus, fetchPreflight, fetchSettings, fetchProject, fetchProjectLibrary, fetchProjectStorage, fetchCatalogue, fetchAlbumJob, fetchCalibrationJob, fetchCalibrationStatus, fetchVideoConfig, fetchVideoPackage, fetchVideoProof, fetchVideoProofJob, fetchVideoRenderJob, fetchVideoTailAudition, generateSyntheticVideoPackage, invalidateOutput, migrateProject, openAudioPackageFolder, openExportFolder, openProject, openProjectFolder, openVideoPackageFolder, pickAlbumFolder, previewArtifactCleanup, previewArtifactMigration, previewProject, previewProjectMigration, refreshVideoPreparation, rejectVideoProof, relinkProjectSource, removeRecentProject, rescanProject, retryAudioPackageJob, retryProcessing, retryVideoProofJob, retryVideoRender, saveCandidateSlots, saveLoop, saveSection, saveSelection, saveSetting, saveVideoTailAuditionDecision, setFastDefaultCandidates, skipCalibration, startAlbumProcessing, startAudioPackage, startCandidateForTrack, startCalibration, startFastRealVideoRender, startFastSyntheticVideoRender, startReferenceVideoRender, startSyntheticVideoRender, startVideoProof, stopAlbumProcessing, stopAudioPackageJob, stopVideoProofJob, stopCalibration, stopVideoRender, type ArtifactMigrationPlan, type AudioMetadata, type AudioPackageJob, type AudioPackageState, type CalibrationOutput, type CalibrationState, type Candidate, type Catalogue, type CandidateSelection, type CleanupPlan, type ExportPlan, type Preflight, type ProjectCreationPreview, type ProjectDescriptor, type ProjectLibrary, type ProjectManifest, type ProjectStorage, type Settings, type MigrationPreview, type VideoConfigState, type VideoPackageState, type VideoProofJob, type VideoProofState, type VideoRenderJob, type VideoTailAuditionCard, type VideoTailAuditionState } from "./api";
import { Button, StatusMark, useConfirmDialog } from "./ui";
import { AlbumLandscape, type AlbumVideoProps } from "./remotion/AlbumLandscape";
import { canApproveVideoProof, isVideoProofApproved, proofAssetFilename, proofStatusLabel } from "./videoProof";
import { hasPickedFolder, resolveProjectView, resolveResourceState } from "./projectWorkflow";
import { canRetryVideoRender, formatElapsedMs, isVideoRenderActive, rendererElapsedMs, videoExportModeLabel, type VideoExportMode } from "./videoExport";

const VIDEO_RENDER_JOB_STORAGE_KEY = "stem-comparison.video-render-job";
const VIDEO_PROOF_JOB_STORAGE_KEY = "stem-comparison.video-proof-job";

type Section = "album" | "projects" | "storage" | "process" | "compare" | "video" | "export";
type SectionGroup = "workspace" | "create" | "system";

const sections: Array<{ id: Section; label: string; icon: typeof House; description: string; group: SectionGroup }> = [
  { id: "album", label: "Album", icon: House, description: "Choose a source folder and confirm Tracks.", group: "workspace" },
  { id: "projects", label: "Projects", icon: FolderOpen, description: "Discover and reopen durable Project Folders.", group: "workspace" },
  { id: "process", label: "Process", icon: Gauge, description: "Run calibration and sequential processing.", group: "workspace" },
  { id: "compare", label: "Compare", icon: SlidersHorizontal, description: "Audition Candidates at one shared moment.", group: "workspace" },
  { id: "video", label: "Video", icon: FilmStrip, description: "Configure and preview the Album Landscape composition.", group: "create" },
  { id: "export", label: "Export", icon: Export, description: "Export the current Selections.", group: "create" },
  { id: "storage", label: "Storage", icon: FolderOpen, description: "Review Project artifacts, releases and reclaimable bytes.", group: "system" },
];

const navigationGroups: Array<{ id: SectionGroup; label: string }> = [
  { id: "workspace", label: "Workspace" },
  { id: "create", label: "Create" },
  { id: "system", label: "System" },
];

function App() {
  const [section, setSection] = useState<Section>("album");
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [projectRevision, setProjectRevision] = useState(0);
  const [newProjectRequest, setNewProjectRequest] = useState(false);

  useEffect(() => {
    Promise.all([fetchPreflight(), fetchSettings()])
      .then(([nextPreflight, nextSettings]) => {
        setPreflight(nextPreflight);
        setSettings(nextSettings);
        if (sections.some((candidate) => candidate.id === nextSettings.lastSection)) {
          setSection(nextSettings.lastSection as Section);
        }
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load local state"))
      .finally(() => setLoading(false));
  }, []);

  const currentSection = useMemo(() => sections.find((candidate) => candidate.id === section)!, [section]);

  async function navigate(next: Section) {
    setSection(next);
    setSettings((current) => current ? { ...current, lastSection: next } : current);
    try {
      await saveSection(next);
    } catch {
      toast.error("The current section could not be saved", { description: "The workspace will still remain usable." });
    }
  }

  async function refreshPreflight() {
    setError(null);
    try {
      setPreflight(await fetchPreflight());
      toast.success("Preflight refreshed");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not refresh preflight");
    }
  }

  return (
    <div className="archive-app">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <aside className="archive-sidebar" aria-label="Primary navigation">
        <div className="archive-brand">
          <span className="archive-brand-mark" aria-hidden="true"><Waveform size={22} weight="regular" /></span>
          <span className="archive-brand-copy"><span className="archive-brand-name">AlbumPress Studio</span><span className="archive-brand-subtitle">Instrumental album workspace</span></span>
        </div>
        <nav className="archive-nav" aria-label="Workspace sections">
          {navigationGroups.map((group) => <div className="archive-nav-group" key={group.id}>
            <span className="archive-nav-group-label">{group.label}</span>
            {sections.filter((item) => item.group === group.id).map(({ id, label, icon: Icon, description }) => (
              <button
                key={id}
                type="button"
                className={clsx("archive-nav-item", section === id && "archive-nav-item-active")}
                aria-label={label}
                aria-current={section === id ? "page" : undefined}
                title={description}
                onClick={() => void navigate(id)}
              >
                <Icon size={20} weight={section === id ? "fill" : "regular"} aria-hidden="true" />
                <span>{label}</span>
              </button>
            ))}
          </div>)}
        </nav>
        <div className="archive-sidebar-foot">
          <span className="archive-sidebar-note"><SpeakerHigh size={16} aria-hidden="true" /><span>CPU-only</span></span>
          <span className="archive-sidebar-note"><ShieldCheck size={16} aria-hidden="true" /><span>Local only</span></span>
        </div>
      </aside>

      <main id="main-content" className="archive-main" aria-busy={loading} data-section={section}>
        <header className="archive-page-header">
          <div>
            <h1>{currentSection.label}</h1>
            <p>{currentSection.description}</p>
          </div>
          <div className="archive-header-status">
            {!loading && preflight && !preflight.ready ? <StatusMark status="missing" label="Setup needed" /> : null}
          </div>
        </header>

        {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Local state unavailable</strong><span>{error}</span></div></div>}

        {section === "album" && <AlbumSurface key={projectRevision} preflight={preflight} loading={loading} startNewProject={newProjectRequest} onNewProjectRequestHandled={() => setNewProjectRequest(false)} onRefresh={() => void refreshPreflight()} onProjectChanged={() => setProjectRevision((value) => value + 1)} />}
        {section === "projects" && <ProjectsSurface onOpen={() => { setNewProjectRequest(true); setProjectRevision((value) => value + 1); void navigate("album"); }} />}
        {section === "storage" && <ProjectStorageSurface />}
        {section === "process" && <ProcessSurface onOpenAlbum={() => void navigate("album")} />}
        {section === "compare" && <CompareSurface onOpenProcess={() => void navigate("process")} />}
        {section === "video" && <VideoSurface />}
        {section === "export" && <ExportSurface onOpenCompare={() => void navigate("compare")} />}

        <footer className="archive-footer"><span>Source audio remains untouched.</span><span>Models download only into the configured local cache.</span></footer>
      </main>
    </div>
  );
}

function ProjectsSurface({ onOpen }: { onOpen: () => void }) {
  const confirm = useConfirmDialog();
  const [library, setLibrary] = useState<ProjectLibrary | null>(null);
  const [storage, setStorage] = useState<Settings | null>(null);
  const [configuredLibrary, setConfiguredLibrary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [migrationSource, setMigrationSource] = useState<ProjectDescriptor | null>(null);
  const [migrationDestination, setMigrationDestination] = useState("");
  const [migrationPreview, setMigrationPreview] = useState<MigrationPreview | null>(null);
  const [migrationWorking, setMigrationWorking] = useState(false);
  useEffect(() => {
    Promise.all([fetchProjectLibrary(), fetchSettings()]).then(([nextLibrary, nextStorage]) => { setLibrary(nextLibrary); setStorage(nextStorage); setConfiguredLibrary(nextStorage.projectLibrary); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load Project Library"));
  }, []);
  async function open(descriptor: ProjectDescriptor) {
    try { await openProject({ manifestPath: descriptor.manifestPath }); toast.success("Project reopened"); onOpen(); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The Project Folder could not be reopened"); }
  }
  async function remove(descriptor: ProjectDescriptor) {
    try { setLibrary(await removeRecentProject(descriptor.manifestPath)); toast.message("Removed from recent", { description: "The Project Folder and its files were not deleted." }); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The recent pointer could not be removed"); }
  }
  async function reveal(descriptor: ProjectDescriptor) {
    try { await openProjectFolder(descriptor.manifestPath); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The Project Folder could not be opened"); }
  }
  async function previewMigration(descriptor: ProjectDescriptor) {
    const destination = `${library?.projectLibrary ?? ""}\\${descriptor.projectName} (Migrated)`;
    setMigrationSource(descriptor); setMigrationDestination(destination);
    try { setMigrationPreview(await previewProjectMigration(destination, descriptor.manifestPath)); setError(null); } catch (reason: unknown) { setMigrationPreview(null); setError(reason instanceof Error ? reason.message : "The migration could not be previewed"); }
  }
  async function refreshMigrationPreview() {
    if (!migrationSource || !migrationDestination) return;
    try { setMigrationPreview(await previewProjectMigration(migrationDestination, migrationSource.manifestPath)); setError(null); } catch (reason: unknown) { setMigrationPreview(null); setError(reason instanceof Error ? reason.message : "The migration could not be previewed"); }
  }
  async function migrate() {
    if (!migrationSource || !migrationDestination || !migrationPreview?.canMigrate) return;
    if (!(await confirm({
      title: "Migrate this Project Folder?",
      description: `Copy and validate the Project Folder at ${migrationDestination}. The original will be retained.`,
      confirmLabel: "Migrate Project Folder",
    }))) return;
    setMigrationWorking(true);
    try { await migrateProject(migrationDestination, migrationSource.manifestPath); toast.success("Project migrated", { description: migrationDestination }); setMigrationSource(null); setMigrationPreview(null); setLibrary(await fetchProjectLibrary()); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The Project Folder could not be migrated"); } finally { setMigrationWorking(false); }
  }
  async function saveLibrarySetting() {
    try { const next = await saveSetting("projectLibrary", configuredLibrary); setStorage(next); setLibrary(await fetchProjectLibrary()); toast.success("Project Library saved", { description: configuredLibrary }); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The Project Library setting could not be saved"); }
  }
  return <div className="archive-project-stack">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><span>{error}</span></div>}
    <section className="archive-panel" aria-labelledby="projects-library-title"><div className="archive-panel-heading"><div><h2 id="projects-library-title">Durable Album Projects</h2><p className="archive-path">{library?.projectLibrary ?? "Reading library…"}</p></div><Button tone="primary" onClick={onOpen}>New project</Button></div><p className="archive-caption">Projects are discovered from the library and remembered external folders. Removing a recent item only removes its pointer.</p></section>
    <section className="archive-panel" aria-labelledby="projects-list-title"><div className="archive-panel-heading"><div><h2 id="projects-list-title">{library?.projects.length ?? 0} available</h2></div></div>{!library ? <div className="archive-loading" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Reading Project Library…</div> : library.projects.length === 0 ? <div className="archive-empty-state"><FolderOpen size={22} aria-hidden="true" /><span>No durable Project Folders have been created yet.</span></div> : <div className="project-library-list">{library.projects.map((descriptor) => <article className="project-library-card" key={descriptor.manifestPath}><div><h3>{descriptor.projectName}</h3><p className="archive-path" title={descriptor.projectFolder}>{descriptor.projectFolder}</p><span className="archive-caption">{descriptor.trackCount} Tracks · {descriptor.selectionCount} Selections · Source {descriptor.sourceState.status}</span></div><div className="archive-panel-actions"><Button tone="primary" size="compact" onClick={() => void open(descriptor)}>Open</Button><Button tone="quiet" size="compact" onClick={() => void reveal(descriptor)}><ArrowSquareOut size={17} aria-hidden="true" />Open folder</Button><Button tone="quiet" size="compact" onClick={() => void previewMigration(descriptor)}>Preview migration</Button>{descriptor.origin === "recent" && <Button tone="quiet" size="compact" onClick={() => void remove(descriptor)}>Remove recent</Button>}</div></article>)}</div>}</section>
    {migrationSource && <section className="archive-panel" aria-labelledby="migration-title"><div className="archive-panel-heading"><div><h2 id="migration-title">Migration rehearsal</h2></div><Button tone="quiet" size="compact" onClick={() => { setMigrationSource(null); setMigrationPreview(null); }}>Cancel</Button></div><p className="archive-caption">Original: <span className="archive-path">{migrationSource.projectFolder}</span></p><label>Exact destination<input value={migrationDestination} onChange={(event) => setMigrationDestination(event.target.value)} /></label>{migrationPreview && <div className="project-preview-card"><strong>{migrationPreview.destinationProjectFolder}</strong><span>{migrationPreview.artifactCount} files · {formatBytes(migrationPreview.bytes)} · {migrationPreview.registeredArtifactCount} registered artifacts</span><span>{migrationPreview.preservationPlan}</span><span>{migrationPreview.canMigrate ? "Ready for explicit authorization" : "Destination must be unused before migration"}</span></div>}<div className="archive-panel-actions"><Button tone="quiet" onClick={() => void refreshMigrationPreview()}>Recalculate migration</Button><Button tone="primary" onClick={() => void migrate()} disabled={!migrationPreview?.canMigrate || migrationWorking}>{migrationWorking ? "Migrating…" : "Migrate Project Folder"}</Button></div></section>}
    {storage && <section className="archive-panel" aria-labelledby="storage-title"><div className="archive-panel-heading"><div><h2 id="storage-title">Shared paths</h2></div></div><div className="storage-list"><div><strong>Model cache</strong><span className="archive-path">{storage.modelCachePath}</span></div><div><strong>Application logs</strong><span className="archive-path">{storage.logPath}</span></div><div><strong>Project Library</strong><input aria-label="Project Library setting" value={configuredLibrary} onChange={(event) => setConfiguredLibrary(event.target.value)} /><Button tone="quiet" size="compact" onClick={() => void saveLibrarySetting()}>Save Project Library</Button></div></div></section>}
  </div>;
}

function ProjectStorageSurface() {
  const confirm = useConfirmDialog();
  const [storage, setStorage] = useState<ProjectStorage | null>(null);
  const [cleanup, setCleanup] = useState<CleanupPlan | null>(null);
  const [migration, setMigration] = useState<ArtifactMigrationPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  async function refresh() {
    setError(null);
    try { setStorage(await fetchProjectStorage()); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Project Storage could not be read"); }
  }
  useEffect(() => { void refresh(); }, []);
  async function reviewCleanup() {
    setWorking(true);
    try { setCleanup(await previewArtifactCleanup(false)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The cleanup preview could not be prepared"); } finally { setWorking(false); }
  }
  async function cleanTemporaryFiles() {
    if (!cleanup) return;
    if (!(await confirm({
      title: "Clean verified temporary files?",
      description: `${cleanup.targets.length} files will be removed, reclaiming ${formatBytes(cleanup.reclaimableBytes)}. Protected and review-required files will remain untouched.`,
      confirmLabel: "Clean temporary files",
      tone: "danger",
    }))) return;
    setWorking(true);
    try { await applyArtifactCleanup(cleanup); toast.success("Temporary files cleaned"); setCleanup(null); await refresh(); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Cleanup was blocked because the filesystem changed"); } finally { setWorking(false); }
  }
  async function previewLayoutMigration() {
    setWorking(true);
    try { setMigration(await previewArtifactMigration()); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The layout migration could not be planned"); } finally { setWorking(false); }
  }
  return <div className="archive-project-stack">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Project Storage unavailable</strong><span>{error}</span></div></div>}
    <section className="archive-panel" aria-labelledby="project-storage-title">
      <div className="archive-panel-heading"><div><h2 id="project-storage-title">Project Storage</h2><p className="archive-path" title={storage?.projectFolder}>{storage?.projectFolder ?? "Reading current Project Folder…"}</p></div><div className="archive-panel-actions"><Button tone="quiet" size="compact" onClick={() => void openProjectFolder()}><ArrowSquareOut size={17} aria-hidden="true" />Open project folder</Button><Button tone="quiet" size="compact" onClick={() => void refresh()} disabled={working}><ArrowClockwise size={17} aria-hidden="true" />Refresh</Button></div></div>
      {!storage ? <div className="archive-loading" role="status"><CircleNotch size={20} className="archive-spin" />Reading Project Storage…</div> : storage.artifacts.length === 0 ? <div className="archive-empty-state"><FolderOpen size={22} aria-hidden="true" /><span>No registered Project artifacts were found.</span></div> : <>
        <p className="archive-caption">Current release: <strong>{storage.currentRelease?.manifestPath ?? "None resolved"}</strong> · Inventory {storage.inventoryFingerprint.slice(0, 12)} · Hashes from {storage.verifiedHashes ? "filesystem verification" : "registered manifests"}.</p>
        <div className="storage-summary-grid" aria-label="Project Storage totals">{["Protected", "Safe temporary", "Review required"].map((category) => <div className="storage-summary-card" key={category}><span>{category}</span><strong>{formatBytes(storage.totals[category] ?? 0)}</strong><small>{storage.artifacts.filter((item) => item.category === category).length} files</small></div>)}</div>
      </>}
    </section>
    {storage && <section className="archive-panel" aria-labelledby="artifact-inventory-title"><div className="archive-panel-heading"><div><h2 id="artifact-inventory-title">Artifact inventory</h2><p className="archive-caption">Every row has a resolved relative path, category, bytes and provenance reason.</p></div><div className="archive-panel-actions"><Button tone="quiet" size="compact" onClick={() => void previewLayoutMigration()} disabled={working}>Preview layout migration</Button><Button tone="secondary" size="compact" onClick={() => void reviewCleanup()} disabled={working}>Review cleanup</Button></div></div><div className="storage-table-wrap"><table className="storage-table"><caption className="sr-only">Project artifact inventory and cleanup classification</caption><thead><tr><th scope="col">Artifact</th><th scope="col">Area</th><th scope="col">Class</th><th scope="col">Size</th><th scope="col">Reason</th></tr></thead><tbody>{storage.artifacts.map((item) => <tr key={item.artifactId}><th scope="row"><span className="storage-path" title={item.path}>{item.path}</span><small>{item.role ?? "Unregistered"}</small></th><td>{item.area}</td><td><span className={`storage-status storage-status-${item.category.toLowerCase().replace(" ", "-")}`}>{item.category}</span></td><td className="tabular-nums">{formatBytes(item.bytes)}</td><td>{item.reason}</td></tr>)}</tbody></table></div></section>}
    {cleanup && <section className="archive-panel" aria-labelledby="cleanup-preview-title"><div className="archive-panel-heading"><div><h2 id="cleanup-preview-title">Cleanup preview</h2><p className="archive-caption">Plan {cleanup.planFingerprint.slice(0, 16)} · filesystem changes invalidate this preview.</p></div><div className="archive-panel-actions"><Button tone="quiet" size="compact" onClick={() => setCleanup(null)}>Close</Button><Button tone="primary" size="compact" onClick={() => void cleanTemporaryFiles()} disabled={!cleanup.targets.length || working}>Clean temporary files</Button></div></div><div className="project-preview-card"><strong>{cleanup.targets.length} Safe temporary files · {formatBytes(cleanup.reclaimableBytes)} reclaimable</strong>{cleanup.targets.length === 0 ? <span>No automatically safe cleanup candidates.</span> : <ul className="storage-cleanup-list">{cleanup.targets.slice(0, 20).map((item) => <li key={item.artifactId}><span title={item.path}>{item.path}</span><span>{formatBytes(item.bytes)}</span></li>)}</ul>}</div></section>}
    {migration && <section className="archive-panel" aria-labelledby="artifact-migration-title"><div className="archive-panel-heading"><div><h2 id="artifact-migration-title">Layout migration rehearsal</h2><p className="archive-caption">No files were moved. Applying this exact plan requires separate authorization.</p></div><Button tone="quiet" size="compact" onClick={() => setMigration(null)}>Close</Button></div><div className="project-preview-card"><strong>{migration.status === "already_migrated" ? "The Project already uses the new layout." : `${migration.mappings.length} mapped files · ${formatBytes(migration.bytes ?? 0)}`}</strong>{migration.status === "planned" && <><span>Current release destination: {migration.currentDestinationFolder}</span><ul className="storage-cleanup-list">{migration.releases.map((release) => <li key={release.technicalId}><span title={release.destinationFolder}>{release.humanLabel}</span><span>{release.state}</span></li>)}</ul></>}</div></section>}
  </div>;
}

function AlbumSurface({ preflight, loading, startNewProject, onNewProjectRequestHandled, onRefresh, onProjectChanged }: { preflight: Preflight | null; loading: boolean; startNewProject: boolean; onNewProjectRequestHandled: () => void; onRefresh: () => void; onProjectChanged: () => void }) {
  const [project, setProject] = useState<ProjectManifest | null>(null);
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [pendingSource, setPendingSource] = useState<string | null>(null);
  const [pendingName, setPendingName] = useState("");
  const [pendingLibrary, setPendingLibrary] = useState("");
  const [pendingFolder, setPendingFolder] = useState("");
  const [creationPreview, setCreationPreview] = useState<ProjectCreationPreview | null>(null);

  useEffect(() => {
    fetchProject().then(setProject).catch((reason: unknown) => setProjectError(reason instanceof Error ? reason.message : "Could not load the Album Project")).finally(() => setProjectLoading(false));
  }, []);

  async function chooseAlbum() {
    setProjectError(null);
    try {
      const sourcePath = await pickAlbumFolder();
       if (!hasPickedFolder(sourcePath)) return;
      const settings = await fetchSettings();
      const name = sourcePath.split(/[\\/]/).filter(Boolean).pop() ?? "Album Project";
      setPendingSource(sourcePath);
      setPendingName(name);
      setPendingLibrary(settings.projectLibrary);
      setPendingFolder("");
      setCreationPreview(await previewProject(sourcePath, { projectName: name, projectLibrary: settings.projectLibrary }));
    } catch (reason: unknown) {
      setProjectError(reason instanceof Error ? reason.message : "The Album Project could not be opened");
    }
  }

  useEffect(() => {
    if (!startNewProject || projectLoading) return;
    onNewProjectRequestHandled();
    void chooseAlbum();
  }, [startNewProject, projectLoading]);

  async function refreshCreationPreview() {
    if (!pendingSource) return;
    try { setCreationPreview(await previewProject(pendingSource, { projectName: pendingName, projectLibrary: pendingLibrary, projectFolder: pendingFolder || undefined })); setProjectError(null); } catch (reason: unknown) { setProjectError(reason instanceof Error ? reason.message : "The Project Folder could not be resolved"); }
  }

  async function createPendingProject() {
    if (!pendingSource || !creationPreview?.freeSpaceOk) return;
    try {
      const latest = await previewProject(pendingSource, { projectName: pendingName, projectLibrary: pendingLibrary, projectFolder: pendingFolder || undefined });
      if (!latest.freeSpaceOk) throw new Error("The Project Folder does not have enough free space.");
      await saveSetting("projectLibrary", pendingLibrary);
      setProject(await openProject({ sourcePath: pendingSource, projectName: pendingName, projectLibrary: pendingLibrary, projectFolder: pendingFolder || latest.projectFolder }));
      setPendingSource(null); setCreationPreview(null); setProjectError(null); onProjectChanged(); toast.success("Album Project created", { description: latest.projectFolder });
    } catch (reason: unknown) { setProjectError(reason instanceof Error ? reason.message : "The Album Project could not be created"); }
  }

  async function rescanAlbum() {
    setProjectError(null);
    try {
      setProject(await rescanProject());
      toast.success("Tracks rescanned");
    } catch (reason: unknown) {
      setProjectError(reason instanceof Error ? reason.message : "The Album Project could not be rescanned");
    }
  }

  const projectView = resolveProjectView(projectLoading, pendingSource, Boolean(project));
  if (projectView === "loading") return <div className="archive-loading archive-loading-page" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Reading the last Album Project…</div>;
  if (projectView === "creating" && pendingSource) return <ProjectCreationPanel sourcePath={pendingSource} projectName={pendingName} projectLibrary={pendingLibrary} projectFolder={pendingFolder} preview={creationPreview} error={projectError} onName={setPendingName} onLibrary={setPendingLibrary} onFolder={setPendingFolder} onRefresh={() => void refreshCreationPreview()} onCreate={() => void createPendingProject()} onCancel={() => { setPendingSource(null); setCreationPreview(null); setProjectError(null); }} />;
  if (projectView === "current" && project) return <ProjectSurface project={project} onRescan={() => void rescanAlbum()} onChoose={() => void chooseAlbum()} error={projectError} />;
  return (
    <div className="archive-content-grid">
      <section className="archive-hero-panel" aria-labelledby="album-start-title">
        <div className="archive-hero-icon" aria-hidden="true"><MusicNotes size={30} weight="regular" /></div>
        <h2 id="album-start-title">Turn one folder into a listening experiment.</h2>
        <p>Choose a local album folder to detect its Tracks in natural order. The source stays read-only; Outputs and the project manifest live in a separate workspace.</p>
        <Button tone="primary" onClick={() => void chooseAlbum()}><FolderOpen size={19} aria-hidden="true" />Choose album folder</Button>
        <p className="archive-caption">Models download on first use. CPU separation can take time, so the application is designed to remain open and resumable.</p>
      </section>

      <section className="archive-panel" aria-labelledby="preflight-title">
        <div className="archive-panel-heading"><div><h2 id="preflight-title">Preflight</h2></div><Button tone="quiet" size="compact" aria-label="Refresh preflight" onClick={onRefresh}><ArrowClockwise size={18} aria-hidden="true" />Refresh</Button></div>
        {loading && <div className="archive-loading" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Checking the local environment…</div>}
        {!loading && preflight && <>
          <div className="preflight-summary"><StatusMark status={preflight.ready ? "ready" : "missing"} label={preflight.summary} /><span className="archive-caption">{preflight.platform}</span></div>
          <ul className="preflight-list">
            {preflight.checks.map((check) => <li key={check.key} className="preflight-row"><div><span className="preflight-label">{check.label}</span><span className="preflight-detail">{check.detail}</span>{check.action && <span className="preflight-action">{check.action}</span>}</div><div className="preflight-value"><StatusMark status={check.status} label={check.value ?? check.status} /></div></li>)}
          </ul>
        </>}
      </section>
      <section className="archive-panel archive-panel-wide" aria-labelledby="workflow-title">
        <div className="archive-panel-heading"><div><h2 id="workflow-title">A calm path from source to Selection</h2></div></div>
        <div className="workflow-steps">
          {["Choose an album", "Discover Candidates", "Calibrate one Track", "Compare and export"].map((step, index) => <div key={step} className="workflow-step"><span className="workflow-number">{String(index + 1).padStart(2, "0")}</span><span>{step}</span></div>)}
        </div>
      </section>
    </div>
  );
}

function ProjectCreationPanel({ sourcePath, projectName, projectLibrary, projectFolder, preview, error, onName, onLibrary, onFolder, onRefresh, onCreate, onCancel }: { sourcePath: string; projectName: string; projectLibrary: string; projectFolder: string; preview: ProjectCreationPreview | null; error: string | null; onName: (value: string) => void; onLibrary: (value: string) => void; onFolder: (value: string) => void; onRefresh: () => void; onCreate: () => void; onCancel: () => void }) {
  return <div className="archive-content-grid"><section className="archive-panel archive-panel-wide" aria-labelledby="project-create-title"><div className="archive-panel-heading"><div><h2 id="project-create-title">Confirm the durable Project Folder</h2></div><Button tone="quiet" onClick={onCancel}>Cancel</Button></div>{error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={18} aria-hidden="true" /><span>{error}</span></div>}<div className="project-create-fields"><label>Source folder<input value={sourcePath} readOnly /></label><label>Project name<input value={projectName} onChange={(event) => onName(event.target.value)} /></label><label>Project Library<input value={projectLibrary} onChange={(event) => onLibrary(event.target.value)} /></label><label>Project Folder override <span className="archive-caption">optional</span><input value={projectFolder} onChange={(event) => onFolder(event.target.value)} placeholder="Leave empty to use the library" /></label></div><div className="project-preview-card" aria-live="polite"><strong>Exact location before creation</strong><strong>{preview?.projectFolder ?? "Resolve the destination"}</strong>{preview && <span className={preview.freeSpaceOk ? "status-ready" : "status-danger"}>{preview.freeSpaceOk ? `${Math.round(preview.freeSpaceBytes / 1024 / 1024)} MB free · ready` : "Not enough free space"}{preview.collision ? ` · collision resolved as #${preview.collisionIndex}` : ""}</span>}</div><div className="archive-panel-actions"><Button tone="quiet" onClick={onRefresh}>Recalculate exact folder</Button><Button tone="primary" onClick={onCreate} disabled={!preview?.freeSpaceOk}>Create Project Folder</Button></div></section></div>;
}

function ProjectSurface({ project, onRescan, onChoose, error }: { project: ProjectManifest; onRescan: () => void; onChoose: () => void; error: string | null }) {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [catalogueLoading, setCatalogueLoading] = useState(true);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);
  const [savedProject, setSavedProject] = useState(project);
  const [sourceState, setSourceState] = useState(project.sourceState);
  useEffect(() => { fetchCatalogue().then(setCatalogue).catch((reason: unknown) => setCatalogueError(reason instanceof Error ? reason.message : "Could not read the installed catalogue")).finally(() => setCatalogueLoading(false)); }, []);
  useEffect(() => { setSavedProject(project); setSourceState(project.sourceState); }, [project]);
  async function saveSlots(slots: Record<string, CandidateSelection | null>) { try { setSavedProject(await saveCandidateSlots(slots)); toast.success("Candidate choices saved"); } catch (reason: unknown) { setCatalogueError(reason instanceof Error ? reason.message : "Candidate choices could not be saved"); } }
  async function relink() { try { const sourcePath = await pickAlbumFolder(); if (!sourcePath) return; setSavedProject(await relinkProjectSource(sourcePath)); setSourceState({ status: "available", detail: "Source folder and Track fingerprints match the project." }); toast.success("Source folder relinked"); } catch (reason: unknown) { setCatalogueError(reason instanceof Error ? reason.message : "The source folder could not be relinked"); } }
  async function reveal() { try { await openProjectFolder(); } catch (reason: unknown) { setCatalogueError(reason instanceof Error ? reason.message : "The Project Folder could not be opened"); } }
  return <div className="archive-project-stack">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Album Project action failed</strong><span>{error}</span></div></div>}
    {sourceState && sourceState.status !== "available" && <div className="archive-alert archive-alert-warning" role="status"><WarningCircle size={20} aria-hidden="true" /><div><strong>Source {sourceState.status}</strong><span>{sourceState.detail}</span><Button tone="quiet" size="compact" onClick={() => void relink()}>Locate source folder</Button></div></div>}
    <section className="archive-panel" aria-labelledby="project-title">
      <div className="archive-panel-heading"><div><h2 id="project-title">{project.projectName ?? project.albumName}</h2><p className="archive-path" title={project.sourceFolder}>{project.sourceFolder}</p></div><div className="archive-panel-actions"><Button tone="quiet" size="compact" onClick={onChoose}><FolderOpen size={18} aria-hidden="true" />New project</Button><Button tone="quiet" size="compact" onClick={() => void reveal()}><ArrowSquareOut size={18} aria-hidden="true" />Open folder</Button><Button tone="secondary" size="compact" onClick={onRescan} disabled={sourceState?.status !== "available"}><ArrowClockwise size={18} aria-hidden="true" />Rescan</Button></div></div>
      <p className="archive-caption">Updated {new Date(project.updatedAt).toLocaleString("en-GB")} · Project Folder: <span className="archive-path" title={project.projectFolder ?? project.outputFolder}>{project.projectFolder ?? project.outputFolder}</span></p>
    </section>
    <section className="archive-panel" aria-labelledby="tracks-title">
      <div className="archive-panel-heading"><div><h2 id="tracks-title">{project.tracks.length} {project.tracks.length === 1 ? "Track" : "Tracks"}</h2></div></div>
      {project.tracks.length === 0 ? <div className="archive-empty-state"><MusicNotes size={22} aria-hidden="true" /><span>No supported audio files were found in the top level of this folder.</span></div> : <div className="track-table-wrap"><table className="track-table"><caption className="sr-only">Detected Tracks in natural order</caption><thead><tr><th scope="col">#</th><th scope="col">Track</th><th scope="col">Duration</th><th scope="col">Format</th></tr></thead><tbody>{project.tracks.map((track) => <tr key={track.trackId}><td className="tabular-nums">{String(track.sequence).padStart(2, "0")}</td><th scope="row" title={track.title}>{track.title}</th><td className="tabular-nums">{formatDuration(track.durationSeconds)}</td><td>{track.extension.replace(".", "").toUpperCase()}</td></tr>)}</tbody></table></div>}
    </section>
    {project.unsupportedFiles.length > 0 && <section className="archive-panel" aria-labelledby="unsupported-title"><div className="archive-panel-heading"><div><h2 id="unsupported-title">Not included</h2></div></div><ul className="unsupported-list">{project.unsupportedFiles.map((file) => <li key={file.name}><span title={file.name}>{file.name}</span><span>{file.reason}</span></li>)}</ul></section>}
    <FastDefaultButton onApplied={setSavedProject} disabled={!catalogue?.live} />
    <CandidateSurface catalogue={catalogue} loading={catalogueLoading} error={catalogueError} project={savedProject} onRefresh={() => { setCatalogueLoading(true); fetchCatalogue(true).then(setCatalogue).catch((reason: unknown) => setCatalogueError(reason instanceof Error ? reason.message : "Could not refresh the catalogue")).finally(() => setCatalogueLoading(false)); }} onSave={saveSlots} />
    <TrackProcessingPanel project={savedProject} />
    <CalibrationPreviewPanel project={savedProject} />
  </div>;
}

function CandidateSurface({ catalogue, loading, error, project, onRefresh, onSave }: { catalogue: Catalogue | null; loading: boolean; error: string | null; project: ProjectManifest; onRefresh: () => void; onSave: (slots: Record<string, CandidateSelection | null>) => Promise<void> }) {
  const initialSlots = () => Object.fromEntries(["A", "B", "C", "D"].map((slot) => [slot, project.candidates?.find((candidate) => candidate.slot === slot) ?? null]));
  const [slots, setSlots] = useState<Record<string, CandidateSelection | null>>(initialSlots);
  const [search, setSearch] = useState("");
  useEffect(() => setSlots(initialSlots()), [project]);
  const selectedIds = new Set(Object.values(slots).filter(Boolean).map((candidate) => candidate!.candidateId));
  const filtered = (catalogue?.candidates ?? []).filter((candidate) => `${candidate.label} ${candidate.technicalIdentifier}`.toLowerCase().includes(search.toLowerCase())).slice(0, 16);
  function assign(candidate: Candidate, slot: string) { if (selectedIds.has(candidate.candidateId) && slots[slot]?.candidateId !== candidate.candidateId) return; setSlots((current) => ({ ...current, [slot]: { ...candidate, slot } })); }
  return <section className="archive-panel" aria-labelledby="candidates-title"><div className="archive-panel-heading"><div><h2 id="candidates-title">Candidates</h2></div><Button tone="quiet" size="compact" onClick={onRefresh}><ArrowClockwise size={18} aria-hidden="true" />Refresh</Button></div>{loading && <div className="archive-loading" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Discovering Models and Presets…</div>}{error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Catalogue unavailable</strong><span>{error}</span></div></div>}{!loading && catalogue && <><p className="archive-caption">{catalogue.live ? `${catalogue.counts.presets} Presets · ${catalogue.counts.models} Models · audio-separator ${catalogue.engine.version ?? "unknown"}` : "Live discovery unavailable; no Candidates are selectable."}</p><div className="candidate-slots">{["A", "B", "C", "D"].map((slot) => <div className="candidate-slot" key={slot}><span className="candidate-slot-label">{slot}</span><span className="candidate-slot-name" title={slots[slot]?.technicalIdentifier}>{slots[slot]?.label ?? "Empty slot"}</span><select aria-label={`Candidate slot ${slot}`} value={slots[slot]?.candidateId ?? ""} onChange={(event) => { const candidate = catalogue.candidates.find((item) => item.candidateId === event.target.value); if (candidate) assign(candidate, slot); else setSlots((current) => ({ ...current, [slot]: null })); }} disabled={!catalogue.live}><option value="">Empty slot</option>{catalogue.candidates.map((candidate) => <option key={candidate.candidateId} value={candidate.candidateId} disabled={selectedIds.has(candidate.candidateId) && slots[slot]?.candidateId !== candidate.candidateId}>{candidate.type}: {candidate.label}</option>)}</select></div>)}</div><div className="recommendation-heading"><h3>Recommended for instrumental</h3><span className="archive-caption">Validated against this installed catalogue</span></div><div className="recommendation-grid">{catalogue.recommendations.map((recommendation) => <div className="recommendation-card" key={recommendation.candidateId}><div><strong>{recommendation.candidate?.label ?? recommendation.candidateId.replace("preset:", "")}</strong><span>{recommendation.available ? `${recommendation.candidate?.cacheState} · ${recommendation.candidate?.algorithm}` : recommendation.reason}</span></div><Button tone="quiet" size="compact" disabled={!recommendation.available || selectedIds.has(recommendation.candidateId)} onClick={() => { const target = ["A", "B", "C", "D"].find((slot) => !slots[slot]); if (target && recommendation.candidate) assign(recommendation.candidate, target); }}>{selectedIds.has(recommendation.candidateId) ? "Selected" : "Add"}</Button></div>)}</div><details className="catalogue-details"><summary>Browse full catalogue</summary><label className="catalogue-search">Search Models and Presets<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search by label or filename" /></label><div className="catalogue-list">{filtered.map((candidate) => <button key={candidate.candidateId} type="button" className="catalogue-item" disabled={selectedIds.has(candidate.candidateId)} onClick={() => { const target = ["A", "B", "C", "D"].find((slot) => !slots[slot]); if (target) assign(candidate, target); }}><span><strong>{candidate.type}: {candidate.label}</strong><small title={candidate.technicalIdentifier}>{candidate.technicalIdentifier} · {candidate.cacheState}</small></span><span aria-hidden="true">+</span></button>)}</div></details><div className="candidate-save-row"><span className="archive-caption">{Object.values(slots).filter(Boolean).length} of 4 slots selected</span><Button tone="primary" onClick={() => void onSave(slots)} disabled={!catalogue.live || Object.values(slots).filter(Boolean).length === 0}>Save Candidate choices</Button></div></>}</section>;
}

function FastDefaultButton({ onApplied, disabled }: { onApplied: (project: ProjectManifest) => void; disabled: boolean }) {
  const [working, setWorking] = useState(false);
  async function apply() {
    setWorking(true);
    try {
      onApplied(await setFastDefaultCandidates());
      toast.success("Fast HQ5 is now Candidate A", { description: "Older Outputs remain invalidated; process a Track when ready." });
    } catch (reason: unknown) {
      toast.error(reason instanceof Error ? reason.message : "The Fast Candidate default could not be applied");
    } finally {
      setWorking(false);
    }
  }
  return <section className="archive-panel archive-panel-compact" aria-labelledby="fast-default-title"><div><h2 id="fast-default-title">HQ5 is the default path</h2><p className="archive-caption">CPU benchmark: about 5 minutes for a full-length Track, with one loaded HQ5 instance reused across the album. Deep and slow Candidates stay on demand.</p></div><Button tone="secondary" size="compact" onClick={() => void apply()} disabled={disabled || working}>{working ? "Applying…" : "Use Fast HQ5 as A"}</Button></section>;
}

function TrackProcessingPanel({ project }: { project: ProjectManifest }) {
  const [trackId, setTrackId] = useState(project.tracks[0]?.trackId ?? "");
  const [slot, setSlot] = useState(project.candidates?.find((candidate) => candidate.slot === "A")?.slot ?? project.candidates?.[0]?.slot ?? "A");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const candidate = project.candidates?.find((item) => item.slot === slot);
  const track = project.tracks.find((item) => item.trackId === trackId);
  const estimate = candidate?.benchmark && track ? Math.ceil((track.durationSeconds * candidate.benchmark.secondsPerSourceSecond + (candidate.reusableLoadedModel ? 0 : candidate.benchmark.modelLoadSeconds)) / 60) : null;
  async function process() {
    if (!trackId || !slot) return;
    setWorking(true);
    setError(null);
    try {
      await startCandidateForTrack(trackId, slot);
      toast.success(`Candidate ${slot} queued for ${track?.title ?? "the Track"}`, { description: "The Output will remain pending human semantic confirmation." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Candidate processing could not start");
    } finally {
      setWorking(false);
    }
  }
  return <section className="archive-panel archive-panel-compact" aria-labelledby="track-process-title"><div className="archive-panel-heading"><div><h2 id="track-process-title">Process Candidate for this Track</h2><p className="archive-caption">HQ5 Fast is reused when processing the album. Deep remains available by adding <code>instrumental_full</code> to a slot. Preview calibration never becomes a Final Instrumental.</p></div></div>{error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={18} aria-hidden="true" /><span>{error}</span></div>}<div className="calibration-controls"><label>Track<select value={trackId} onChange={(event) => setTrackId(event.target.value)} disabled={working}>{project.tracks.map((item) => <option key={item.trackId} value={item.trackId}>{String(item.sequence).padStart(2, "0")} · {item.title}</option>)}</select></label><label>Candidate<select value={slot} onChange={(event) => setSlot(event.target.value)} disabled={working}>{(project.candidates ?? []).map((item) => <option key={item.slot} value={item.slot}>{item.slot} · {item.label}</option>)}</select></label>{estimate !== null && <span className="calibration-estimate">≈ {estimate} min estimate</span>}<Button tone="secondary" onClick={() => void process()} disabled={working || !trackId || !candidate}>{working ? "Starting…" : "Process Candidate for this Track"}</Button></div></section>;
}

function CalibrationPreviewPanel({ project }: { project: ProjectManifest }) {
  const [trackId, setTrackId] = useState(project.tracks[0]?.trackId ?? "");
  const [startSeconds, setStartSeconds] = useState(0);
  const [durationSeconds, setDurationSeconds] = useState(45);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function calibrate() {
    setWorking(true);
    setError(null);
    try {
      await startCalibration(trackId, { startSeconds, durationSeconds });
      toast.success("Preview calibration started", { description: "This Output is diagnostic only and cannot become Final." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Preview calibration could not start");
    } finally {
      setWorking(false);
    }
  }
  return <section className="archive-panel archive-panel-compact" aria-labelledby="preview-calibration-title"><div className="archive-panel-heading"><div><h2 id="preview-calibration-title">Calibrate a 30–60 second fragment</h2><p className="archive-caption">Use this to measure a slow Model before committing to a Track. Preview Outputs stay quarantined from Selection and Export.</p></div></div>{error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={18} aria-hidden="true" /><span>{error}</span></div>}<div className="calibration-controls"><label>Track<select value={trackId} onChange={(event) => setTrackId(event.target.value)} disabled={working}>{project.tracks.map((item) => <option key={item.trackId} value={item.trackId}>{String(item.sequence).padStart(2, "0")} · {item.title}</option>)}</select></label><label>Start (s)<input type="number" min="0" step="1" value={startSeconds} onChange={(event) => setStartSeconds(Number(event.target.value))} disabled={working} /></label><label>Duration (s)<input type="number" min="30" max="60" step="1" value={durationSeconds} onChange={(event) => setDurationSeconds(Number(event.target.value))} disabled={working} /></label><Button tone="quiet" onClick={() => void calibrate()} disabled={working || !trackId}>{working ? "Starting…" : "Calibrate preview"}</Button></div></section>;
}

function formatDuration(seconds: number) { const total = Math.max(0, Math.round(seconds)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; }
function formatBytes(bytes: number) { if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }

function ProcessSurface({ onOpenAlbum }: { onOpenAlbum: () => void }) {
  const confirm = useConfirmDialog();
  const [project, setProject] = useState<ProjectManifest | null>(null);
  const [calibration, setCalibration] = useState<CalibrationState | null>(null);
  const [trackId, setTrackId] = useState<string>("");
  const [previewMode, setPreviewMode] = useState(false);
  const [previewStart, setPreviewStart] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(45);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchProject(), fetchCalibrationStatus()]).then(([nextProject, nextCalibration]) => {
      setProject(nextProject);
      setCalibration(nextCalibration);
      setTrackId(nextCalibration?.trackId ?? nextProject?.tracks[0]?.trackId ?? "");
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load processing state")).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!calibration || ["complete", "failed", "stopped"].includes(calibration.status)) return;
    const albumWorker = calibration.kind !== "calibration";
    const refresh = () => void (albumWorker ? fetchAlbumJob(calibration.jobId) : fetchCalibrationJob(calibration.jobId)).then(setCalibration).catch(() => undefined);
    const source = new EventSource(`/api/process/${albumWorker ? "album" : "calibration"}/${encodeURIComponent(calibration.jobId)}/events`);
    source.onmessage = refresh;
    const timer = window.setInterval(refresh, 1000);
    return () => { source.close(); window.clearInterval(timer); };
  }, [calibration?.jobId, calibration?.status]);

  async function begin() {
    setError(null);
    setStarting(true);
    try {
      setCalibration(await startCalibration(trackId || undefined, previewMode ? { startSeconds: previewStart, durationSeconds: previewDuration } : undefined));
      toast.success(previewMode ? "Calibration preview started" : "Calibration started");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Calibration could not start");
    } finally {
      setStarting(false);
    }
  }

  async function processCandidateForTrack() {
    const slot = project?.candidates?.find((candidate) => candidate.slot === "A")?.slot ?? project?.candidates?.[0]?.slot;
    if (!trackId || !slot) return;
    setError(null);
    setStarting(true);
    try {
      setCalibration(await startCandidateForTrack(trackId, slot));
      toast.success(`Candidate ${slot} processing started`, { description: "The Output remains pending human semantic confirmation." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Candidate processing could not start");
    } finally {
      setStarting(false);
    }
  }

  async function beginAlbum() {
    setError(null);
    setStarting(true);
    try {
      setCalibration(await startAlbumProcessing());
      toast.success("Album processing started");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Album processing could not start");
    } finally {
      setStarting(false);
    }
  }

  async function stop() {
    if (!calibration) return;
    try {
      setCalibration(await (calibration.kind !== "calibration" ? stopAlbumProcessing(calibration.jobId) : stopCalibration(calibration.jobId)));
      toast.message("Stop requested", { description: "The worker will stop at the next safe Candidate boundary." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Calibration could not be stopped");
    }
  }

  async function skip() {
    if (!(await confirm({
      title: "Skip calibration?",
      description: "Candidate quality will remain unverified before the album run. You can still stop after the current Output.",
      confirmLabel: "Skip calibration",
      tone: "danger",
    }))) return;
    try {
      await skipCalibration();
      toast.warning("Calibration skipped", { description: "Candidate quality remains unverified." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Calibration could not be skipped");
    }
  }

  async function retryTask(task: CalibrationState["tasks"][number]) {
    setError(null);
    try {
      setCalibration(await retryProcessing("output", task.trackId, task.slot));
      toast.success(`Retry started for Candidate ${task.slot}`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "The requested retry could not start");
    }
  }

  async function forceAlbum() {
    if (!(await confirm({
      title: "Force reprocess remaining Outputs?",
      description: "Existing valid Outputs will be replaced only after each new Output passes validation. This can consume substantial CPU time.",
      confirmLabel: "Force reprocess",
      tone: "danger",
    }))) return;
    setError(null);
    try {
      setCalibration(await retryProcessing("remaining", undefined, undefined, true));
      toast.message("Force reprocess started");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Force reprocess could not start");
    }
  }

  if (loading) return <div className="archive-loading archive-loading-page" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Reading processing state…</div>;
  if (!project) return <PlaceholderSurface icon={<Gauge size={26} />} title="Processing stays sequential" copy="Choose a local Album Project first. Calibration will run one selected Candidate at a time in a separate CPU worker." action="Review Album" onAction={onOpenAlbum} />;
  const candidates = project.candidates ?? [];
  const running = calibration && ["queued", "running"].includes(calibration.status);
  return <div className="archive-project-stack">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Processing action failed</strong><span>{error}</span></div></div>}
    <section className="archive-panel" aria-labelledby="calibration-title">
      <div className="archive-panel-heading"><div><h2 id="calibration-title">Find the clearest Candidate before the album run.</h2><p className="archive-path">CPU-only · concurrency 1 · source audio remains untouched</p></div><span className="calibration-badge">Worker process</span></div>
      {candidates.length === 0 ? <div className="archive-empty-state"><WarningCircle size={22} aria-hidden="true" /><span>Choose at least one Candidate in Album before calibrating.</span></div> : <div className="calibration-controls"><label>Calibration Track<select aria-label="Calibration Track" value={trackId} onChange={(event) => setTrackId(event.target.value)} disabled={Boolean(running)}>{project.tracks.map((track) => <option key={track.trackId} value={track.trackId}>{String(track.sequence).padStart(2, "0")} · {track.title}</option>)}</select></label><div className="archive-panel-actions"><Button tone="primary" onClick={() => void begin()} disabled={Boolean(running) || starting || !trackId}>{starting ? "Starting…" : "Calibrate selected Track"}</Button>{running && <Button tone="quiet" onClick={() => void stop()}>Stop worker</Button>}{!running && <Button tone="quiet" onClick={() => void skip()}>Skip with acknowledgement</Button>}</div></div>}
      {calibration && <div className="calibration-status" role="status"><div><strong>{calibration.stage}</strong><span>{calibration.message}</span></div>{calibration.estimatedAlbumSeconds !== null && <span className="calibration-estimate">≈ {Math.ceil(calibration.estimatedAlbumSeconds / 60)} min album estimate</span>}</div>}
    </section>
      {calibration && <section className="archive-panel" aria-labelledby="ledger-title"><div className="archive-panel-heading"><div><h2 id="ledger-title">{calibration.kind === "album" ? "Every Track, one Candidate at a time." : "Sequential Candidate results"}</h2></div><span className="archive-caption">{calibration.tasks.filter((task) => task.stage === "Complete").length} / {calibration.tasks.length} valid Outputs</span></div><div className="calibration-ledger"><table className="track-table"><caption className="sr-only">Processing Candidate status</caption><thead><tr>{calibration.kind === "album" && <th scope="col">Track</th>}<th scope="col">Slot</th><th scope="col">Candidate</th><th scope="col">Stage</th><th scope="col">Elapsed</th><th scope="col">Listen</th></tr></thead><tbody>{calibration.tasks.map((task) => { const track = project.tracks.find((item) => item.trackId === task.trackId); const source = calibration.kind === "album" ? `/api/process/album/${encodeURIComponent(calibration.jobId)}/outputs/${encodeURIComponent(task.trackId)}/${encodeURIComponent(task.slot)}` : `/api/process/calibration/${encodeURIComponent(calibration.jobId)}/outputs/${encodeURIComponent(task.slot)}`; return <tr key={task.taskId}>{calibration.kind === "album" && <td title={track?.title}>{track ? `${String(track.sequence).padStart(2, "0")} · ${track.title}` : task.trackId}</td>}<th scope="row" className="calibration-slot">{task.slot}</th><td title={task.candidateId}>{task.candidateLabel}</td><td><span className={`task-stage task-stage-${task.stage.toLowerCase().replaceAll(" ", "-")}`}>{task.stage}</span>{task.error && <><small className="task-error">{task.error}</small>{task.technicalError && <details className="task-error-detail"><summary>Technical detail</summary><code>{task.technicalError}</code></details>}</>}</td><td className="tabular-nums">{task.elapsedSeconds === null ? "—" : `${task.elapsedSeconds.toFixed(1)}s`}</td><td>{task.stage === "Complete" && <audio controls preload="none" src={source} aria-label={`Listen to Candidate ${task.slot}`} />}{task.stage === "Failed" && <Button tone="quiet" size="compact" onClick={() => void retryTask(task)}>Retry</Button>}</td></tr>; })}</tbody></table></div></section>}
      {candidates.length > 0 && !running && <section className="archive-panel album-run-panel" aria-labelledby="album-run-title"><div><h2 id="album-run-title">Continue with every Track</h2><p className="archive-caption">Track-first order · {project.tracks.length * candidates.length} total tasks · previously valid Outputs are resumed.</p></div><div className="archive-panel-actions"><Button tone="secondary" onClick={() => void beginAlbum()} disabled={starting}>{starting ? "Starting…" : "Process full album"}</Button><Button tone="quiet" onClick={() => void forceAlbum()} disabled={starting}>Force reprocess remaining</Button></div></section>}
  </div>;
}

function CompareSurface({ onOpenProcess }: { onOpenProcess: () => void }) {
  const confirm = useConfirmDialog();
  const [project, setProject] = useState<ProjectManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [trackId, setTrackId] = useState("");
  const [activeSlot, setActiveSlot] = useState("A");
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [approvingAll, setApprovingAll] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [loopIn, setLoopIn] = useState<number | null>(null);
  const [loopOut, setLoopOut] = useState<number | null>(null);
  const [loopEnabled, setLoopEnabled] = useState(false);
  const audioMap = useRef(new Map<string, HTMLAudioElement>());
  const loopRef = useRef({ inSeconds: null as number | null, outSeconds: null as number | null, enabled: false });
  const compareSurfaceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchProject().then((nextProject) => {
      setProject(nextProject);
      const firstTrackWithOutput = nextProject?.tracks.find((item) => Object.values(nextProject.outputs ?? {}).some((output) => output.trackId === item.trackId && output.status === "valid"));
      setTrackId(firstTrackWithOutput?.trackId ?? nextProject?.tracks[0]?.trackId ?? "");
    }).catch((reason: unknown) => setProjectError(reason instanceof Error ? reason.message : "Could not load the comparison project")).finally(() => setLoading(false));
  }, []);

  const track = project?.tracks.find((item) => item.trackId === trackId) ?? project?.tracks[0];
  const availableSlots = ["A", "B", "C", "D"].filter((slot) => Object.values(project?.outputs ?? {}).some((output) => output.trackId === track?.trackId && output.slot === slot && output.status === "valid"));
  const mediaSignature = Object.values(project?.outputs ?? {}).filter((output) => output.trackId === track?.trackId && output.status === "valid").map((output) => `${output.outputId}:${output.fileFingerprint}:${output.path}`).sort().join("|");
  const selection = project?.selections?.[track?.trackId ?? ""];
  const activeOutput = Object.values(project?.outputs ?? {}).find((output) => output.trackId === track?.trackId && output.slot === activeSlot && output.status === "valid");

  useEffect(() => {
    if (availableSlots.length > 0 && !availableSlots.includes(activeSlot)) setActiveSlot(availableSlots[0]);
  }, [track?.trackId, mediaSignature]);

  useEffect(() => {
    const savedLoop = project?.loops?.[track?.trackId ?? ""];
    setLoopIn(savedLoop?.inSeconds ?? null);
    setLoopOut(savedLoop?.outSeconds ?? null);
    setLoopEnabled(savedLoop?.enabled ?? false);
  }, [project?.loops, track?.trackId]);

  useEffect(() => {
    loopRef.current = { inSeconds: loopIn, outSeconds: loopOut, enabled: loopEnabled };
  }, [loopIn, loopOut, loopEnabled]);

  useEffect(() => {
    if (!track) return;
    let disposed = false;
    for (const audio of audioMap.current.values()) {
      audio.pause();
      audio.src = "";
      audio.load();
    }
    audioMap.current.clear();
    setCurrentTime(0);
    setDuration(0);
    setPlaying(false);
    setMediaError(null);
    for (const slot of ["A", "B", "C", "D"]) {
      const output = Object.values(project?.outputs ?? {}).find((item) => item.trackId === track.trackId && item.slot === slot && item.status === "valid");
      if (!output) continue;
      const audio = new Audio(`/api/projects/media/${encodeURIComponent(track.trackId)}/${slot}`);
      audio.preload = "auto";
      audio.muted = slot !== activeSlot;
      audio.addEventListener("timeupdate", () => { if (!audio.muted) { const loop = loopRef.current; if (loop.enabled && loop.inSeconds !== null && loop.outSeconds !== null && audio.currentTime >= loop.outSeconds) { audio.currentTime = loop.inSeconds; if (audio.paused) void audio.play().catch(() => undefined); } setCurrentTime(audio.currentTime); } });
      audio.addEventListener("loadedmetadata", () => { if (!audio.muted) setDuration(audio.duration); });
      audio.addEventListener("ended", () => { if (!audio.muted) setPlaying(false); });
      audio.addEventListener("error", () => { if (!disposed && !audio.muted) setMediaError("This registered Output could not be played."); });
      audio.load();
      audioMap.current.set(slot, audio);
    }
    return () => {
      disposed = true;
      for (const audio of audioMap.current.values()) { audio.pause(); audio.src = ""; audio.load(); }
      audioMap.current.clear();
    };
  }, [track?.trackId, mediaSignature]);

  useEffect(() => {
    for (const [slot, audio] of audioMap.current.entries()) audio.muted = slot !== activeSlot;
    const next = audioMap.current.get(activeSlot);
    if (next) { setDuration(Number.isFinite(next.duration) ? next.duration : duration); setCurrentTime(next.currentTime); }
  }, [activeSlot, duration]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const compareHasFocus = Boolean(compareSurfaceRef.current?.contains(document.activeElement));
      const controlOwnsFocus = Boolean(target && (target.isContentEditable || ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "AUDIO"].includes(target.tagName) || target.closest("[role=dialog], [role=menu]")));
      if (controlOwnsFocus) return;
      if (event.key === "Enter" && compareHasFocus) { event.preventDefault(); void (activeOutput?.semanticStatus === "confirmed" ? selectCurrent() : approveAndSelectCurrent()); return; }
      if (event.key === " ") { event.preventDefault(); togglePlayback(); return; }
      const candidateByKey: Record<string, string> = { "1": "A", "2": "B", "3": "C", "4": "D", a: "A", b: "B", c: "C", d: "D" };
      const candidateSlot = candidateByKey[event.key.toLowerCase()];
      if (candidateSlot) { event.preventDefault(); if (availableSlots.includes(candidateSlot)) switchCandidate(candidateSlot); return; }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); const index = project?.tracks.findIndex((item) => item.trackId === track?.trackId) ?? 0; const offset = event.key === "ArrowUp" ? -1 : 1; const next = project?.tracks[Math.min((project?.tracks.length ?? 1) - 1, Math.max(0, index + offset))]; if (next) setTrackId(next.trackId); return; }
      if (event.key.toLowerCase() === "i") { void setLoopBoundary("in"); return; }
      if (event.key.toLowerCase() === "o") { void setLoopBoundary("out"); return; }
      if (event.key.toLowerCase() === "l") { event.preventDefault(); if (event.shiftKey) void clearLoop(); else if (loopIn !== null && loopOut !== null) void persistLoop({ inSeconds: loopIn, outSeconds: loopOut, enabled: !loopEnabled }); }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  function switchCandidate(slot: string) {
    const previous = audioMap.current.get(activeSlot);
    const next = audioMap.current.get(slot);
    if (!next) return;
    const timestamp = previous?.currentTime ?? currentTime;
    const shouldPlay = Boolean(previous && !previous.paused);
    if (previous) previous.pause();
    next.currentTime = Math.min(timestamp, Number.isFinite(next.duration) ? next.duration : timestamp);
    next.muted = false;
    setActiveSlot(slot);
    setCurrentTime(next.currentTime);
    if (shouldPlay) void next.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  }

  function togglePlayback() {
    const audio = audioMap.current.get(activeSlot);
    if (!audio) return;
    if (audio.paused) void audio.play().then(() => setPlaying(true)).catch(() => setMediaError("Playback was blocked by the browser."));
    else { audio.pause(); setPlaying(false); }
  }

  function seek(value: number) {
    setCurrentTime(value);
    for (const audio of audioMap.current.values()) audio.currentTime = Math.min(value, Number.isFinite(audio.duration) ? audio.duration : value);
  }

  async function persistLoop(next: { inSeconds: number | null; outSeconds: number | null; enabled: boolean }) {
    if (!track) return;
    try {
      const updated = await saveLoop(track.trackId, next);
      setProject(updated);
      setLoopIn(next.inSeconds);
      setLoopOut(next.outSeconds);
      setLoopEnabled(next.enabled);
    } catch (reason: unknown) {
      setMediaError(reason instanceof Error ? reason.message : "Loop state could not be saved");
    }
  }

  async function setLoopBoundary(boundary: "in" | "out") {
    const nextIn = boundary === "in" ? currentTime : loopIn;
    const nextOut = boundary === "out" ? currentTime : loopOut;
    await persistLoop({ inSeconds: nextIn, outSeconds: nextOut, enabled: loopEnabled });
  }

  async function clearLoop() {
    await persistLoop({ inSeconds: null, outSeconds: null, enabled: false });
  }

  async function selectCurrent() {
    if (!track || !availableSlots.includes(activeSlot) || !activeOutput) return;
    if (activeOutput.isPreview) {
      setMediaError("This is a calibration preview; process the full Track before Selection.");
      return;
    }
    if (activeOutput.semanticStatus !== "confirmed") {
      setMediaError("Confirm the Instrumental semantic check before Selection.");
      return;
    }
    try {
      const updated = await saveSelection(track.trackId, activeSlot);
      setProject(updated);
      toast.success(`Candidate ${activeSlot} selected for ${track.title}`);
    } catch (reason: unknown) {
      setMediaError(reason instanceof Error ? reason.message : "Selection could not be saved");
    }
  }

  async function approveAndSelectCurrent() {
    if (!track || !activeOutput || activeOutput.isPreview || activeOutput.semanticStatus === "confirmed") return;
    setApproving(true);
    try {
      setProject(await approveAndSelectOutput(track.trackId, activeSlot, activeOutput.outputId));
      toast.success(`Candidate ${activeSlot} approved and selected for ${track.title}`);
    } catch (reason: unknown) {
      setMediaError(reason instanceof Error ? reason.message : "The Output could not be approved and selected");
    } finally {
      setApproving(false);
    }
  }

  async function approveAndSelectAllTracks() {
    if (!singleCandidate || pendingValidCount === 0) return;
    if (!(await confirm({
      title: `Approve Candidate ${singleCandidate.slot} for every ready Track?`,
      description: `${pendingValidCount} Tracks will be human-confirmed and Candidate ${singleCandidate.slot} will become their final Selection.`,
      confirmLabel: `Approve ${pendingValidCount} Tracks`,
    }))) return;
    setApprovingAll(true);
    try {
      const result = await approveAndSelectAll();
      setProject(result.project);
      toast.success(`${result.approved} Tracks approved and selected`, { description: result.approved === result.pending ? "Every pending valid Output was updated." : `${result.pending - result.approved} Tracks were skipped; see the per-Track result.` });
    } catch (reason: unknown) {
      setMediaError(reason instanceof Error ? reason.message : "The Album Project could not be approved and selected");
    } finally {
      setApprovingAll(false);
    }
  }

  async function rejectCurrent() {
    if (!activeOutput || activeOutput.isPreview) return;
    if (!(await confirm({
      title: `Reject Candidate ${activeSlot}?`,
      description: `Reject the Output for ${track?.title ?? "this Track"}. It will no longer be available for Selection or Export.`,
      confirmLabel: "Reject Output",
      tone: "danger",
    }))) return;
    setRejecting(true);
    try {
      setProject(await invalidateOutput(activeOutput.outputId, "Output rejected during human review."));
      toast.message(`Candidate ${activeSlot} rejected`);
    } catch (reason: unknown) {
      setMediaError(reason instanceof Error ? reason.message : "The Output could not be rejected");
    } finally {
      setRejecting(false);
    }
  }

  const resourceState = resolveResourceState(project, loading, projectError);
  if (resourceState === "loading") return <div className="archive-loading archive-loading-page" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Loading Compare…</div>;
  if (resourceState === "error") return <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Compare could not load</strong><span>{projectError}</span></div></div>;
  if (!project) return <PlaceholderSurface icon={<SlidersHorizontal size={26} />} title="Compare follows calibration" copy="Open an Album Project and validate at least one Output before listening." action="Open Process" onAction={onOpenProcess} />;
  if (!track || availableSlots.length === 0) return <PlaceholderSurface icon={<SlidersHorizontal size={26} />} title="No valid Outputs yet" copy="Run calibration or the full album queue first. Validated Outputs will appear here as they become available." action="Open Process" onAction={onOpenProcess} />;
  const candidates = project.candidates ?? [];
  const singleCandidate = candidates.length === 1 ? candidates[0] : null;
  const projectTrackIds = new Set(project.tracks.map((item) => item.trackId));
  const pendingValidCount = singleCandidate ? new Set(Object.values(project.outputs ?? {}).filter((output) => projectTrackIds.has(output.trackId) && output.candidateId === singleCandidate.candidateId && output.status === "valid" && !output.isPreview && output.semanticStatus !== "confirmed").map((output) => output.trackId)).size : 0;
  const shorter = availableSlots.some((slot) => { const output = Object.values(project.outputs ?? {}).find((item) => item.trackId === track.trackId && item.slot === slot); return output && track.durationSeconds - output.durationSeconds > Math.max(0.25, track.durationSeconds * 0.05); });
  return <div ref={compareSurfaceRef} className="archive-compare-layout" tabIndex={-1}>
    <SemanticGate output={activeOutput} slot={activeSlot} approving={approving} rejecting={rejecting} onApprove={() => void approveAndSelectCurrent()} onReject={() => void rejectCurrent()} />
    {mediaError && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Playback warning</strong><span>{mediaError}</span></div></div>}
    {shorter && <div className="archive-alert archive-alert-warning" role="status"><WarningCircle size={20} aria-hidden="true" /><div><strong>Output duration mismatch</strong><span>One Candidate is materially shorter than its peers; switching will clamp to the available duration.</span></div></div>}
    <section className="archive-panel compare-track-panel" aria-labelledby="compare-track-title"><div className="archive-panel-heading"><div><h2 id="compare-track-title">Compare at one shared timestamp</h2></div><div className="archive-panel-actions"><span className="archive-caption">{availableSlots.length} {availableSlots.length === 1 ? "Output" : "Outputs"} available</span>{singleCandidate && pendingValidCount > 0 && <Button tone="primary" size="compact" onClick={() => void approveAndSelectAllTracks()} disabled={approvingAll}>{approvingAll ? "Approving…" : `Approve ${pendingValidCount} ready ${pendingValidCount === 1 ? "Track" : "Tracks"}`}</Button>}</div></div><label className="compare-track-select">Track<select value={track.trackId} onChange={(event) => setTrackId(event.target.value)}>{project.tracks.map((item) => <option key={item.trackId} value={item.trackId}>{project.selections?.[item.trackId] ? "✓ " : ""}{String(item.sequence).padStart(2, "0")} · {item.title}</option>)}</select></label><div className="candidate-strip" role="tablist" aria-label="Candidate Outputs">{["A", "B", "C", "D"].map((slot) => { const available = availableSlots.includes(slot); return <button key={slot} type="button" role="tab" aria-selected={activeSlot === slot} disabled={!available} className={`candidate-strip-item ${activeSlot === slot ? "candidate-strip-item-active" : ""}`} onClick={() => switchCandidate(slot)}><span>{slot}</span><small>{available ? project.selections?.[track.trackId]?.slot === slot ? "Selected final" : `Candidate ${slot}` : "Not available"}</small></button>; })}</div><div className="compare-selection-row"><span>{selection ? `Final selection: Candidate ${selection.slot}` : "No final Candidate selected"}</span>{activeOutput?.semanticStatus === "confirmed" && <Button tone="secondary" size="compact" onClick={() => void selectCurrent()}>Select {activeSlot} as final</Button>}</div><details className="shortcut-details"><summary>Keyboard shortcuts</summary><div className="shortcut-help"><span>Space play/pause · A–D / 1–4 Candidates · ↑ ↓ Tracks · I/O loop bounds · L loop · Shift+L clear · Enter select</span></div></details></section>
    <section className="archive-panel compare-transport" aria-labelledby="transport-title"><div className="archive-panel-heading"><div><h2 id="transport-title">{track.title}</h2></div><span className="transport-time">Candidate {activeSlot} · {formatDuration(currentTime)} / {formatDuration(duration)}</span></div><div className="transport-controls"><Button tone="primary" onClick={togglePlayback} aria-label={playing ? "Pause" : "Play"}>{playing ? "Pause" : "Play"}</Button><input aria-label="Shared playback position" type="range" min="0" max={duration || 0} step="0.01" value={Math.min(currentTime, duration || 0)} onChange={(event) => seek(Number(event.target.value))} /></div></section>
    <section className="archive-panel compare-loop-panel" aria-labelledby="loop-title"><div className="archive-panel-heading"><div><h2 id="loop-title">Loop a difficult passage</h2></div><span className="archive-caption">{loopEnabled ? "Loop enabled" : "Loop disabled"}</span></div><div className="loop-controls"><span>In {loopIn === null ? "Not set" : formatDuration(loopIn)} · Out {loopOut === null ? "Not set" : formatDuration(loopOut)}</span><Button tone="quiet" size="compact" onClick={() => void setLoopBoundary("in")}>Set In</Button><Button tone="quiet" size="compact" onClick={() => void setLoopBoundary("out")}>Set Out</Button><Button tone={loopEnabled ? "secondary" : "quiet"} size="compact" onClick={() => void persistLoop({ inSeconds: loopIn, outSeconds: loopOut, enabled: !loopEnabled })} disabled={loopIn === null || loopOut === null}>{loopEnabled ? "Disable loop" : "Enable loop"}</Button><Button tone="quiet" size="compact" onClick={() => void clearLoop()}>Clear</Button></div></section>
  </div>;
}

function SemanticGate({ output, slot, approving, rejecting, onApprove, onReject }: { output: CalibrationOutput | undefined; slot: string; approving: boolean; rejecting: boolean; onApprove: () => void; onReject: () => void }) {
  if (!output) return null;
  if (output.isPreview) return <div className="archive-alert archive-alert-warning" role="status"><WarningCircle size={18} aria-hidden="true" /><div><strong>Candidate {slot} is a preview only</strong><span>Process the full Track before Selection or Export.</span></div></div>;
  if (output.semanticStatus !== "confirmed") return <div className="archive-alert archive-alert-warning" role="status"><WarningCircle size={18} aria-hidden="true" /><div><strong>Human confirmation required</strong><span>Listen to Candidate {slot}; approving it confirms the Instrumental and selects it as final in one atomic action.</span></div><div className="archive-panel-actions"><Button tone="secondary" size="compact" onClick={onApprove} disabled={approving || rejecting}>{approving ? "Approving…" : "Approve & Select"}</Button><Button tone="quiet" size="compact" onClick={onReject} disabled={approving || rejecting}>{rejecting ? "Rejecting…" : "Reject Output"}</Button></div></div>;
  return <div className="archive-alert archive-alert-success" role="status"><CheckCircle size={18} aria-hidden="true" /><span>Candidate {slot} passed Output validation and is ready to select.</span></div>;
}

function TailAuditionPanel({ state, savingTrackId, onDecision }: { state: VideoTailAuditionState; savingTrackId: string | null; onDecision: (trackId: string, decision: VideoTailAuditionCard["decision"]) => void }) {
  const currentAudio = useRef<Record<string, HTMLAudioElement | null>>({});
  const proposedAudio = useRef<Record<string, HTMLAudioElement | null>>({});
  const nextAudio = useRef<Record<string, HTMLAudioElement | null>>({});
  const [playing, setPlaying] = useState<string | null>(null);

  function stop(card: VideoTailAuditionCard) {
    currentAudio.current[card.trackId]?.pause();
    proposedAudio.current[card.trackId]?.pause();
    nextAudio.current[card.trackId]?.pause();
    setPlaying(null);
  }

  function playCurrent(card: VideoTailAuditionCard) {
    stop(card);
    const audio = currentAudio.current[card.trackId];
    if (!audio) return;
    audio.currentTime = card.startSeconds;
    void audio.play();
    setPlaying(`${card.trackId}:current`);
  }

  function playProposed(card: VideoTailAuditionCard) {
    stop(card);
    const audio = proposedAudio.current[card.trackId];
    if (!audio) return;
    audio.currentTime = card.startSeconds;
    void audio.play();
    setPlaying(`${card.trackId}:proposed`);
  }

  function handleCurrentTime(card: VideoTailAuditionCard, event: SyntheticEvent<HTMLAudioElement>) {
    if (event.currentTarget.currentTime >= card.currentEndSeconds - 0.04) stop(card);
  }

  function handleProposedTime(card: VideoTailAuditionCard, event: SyntheticEvent<HTMLAudioElement>) {
    if (event.currentTarget.currentTime < card.proposedEndSeconds - 0.04) return;
    event.currentTarget.pause();
    const next = nextAudio.current[card.trackId];
    if (!next || card.nextPreviewSeconds <= 0) {
      stop(card);
      return;
    }
    next.currentTime = 0;
    void next.play();
    setPlaying(`${card.trackId}:proposed-next`);
  }

  function handleNextTime(card: VideoTailAuditionCard, event: SyntheticEvent<HTMLAudioElement>) {
    if (event.currentTarget.currentTime >= card.nextPreviewSeconds - 0.04) stop(card);
  }

  return <section className="archive-panel tail-audition-panel" aria-labelledby="tail-audition-title">
    <div className="archive-panel-heading"><div><h2 id="tail-audition-title">Tail review</h2><p className="archive-caption">Aligned playback starts {state.lookbackSeconds}s before the proposed cut. Proposed playback enters the next Track without a crossfade.</p></div><span className="archive-caption">{state.cards.length} to review</span></div>
    <div className="tail-audition-grid">{state.cards.map((card) => <article className="tail-audition-card" key={card.trackId}>
      <div className="video-proof-card-heading"><h3>{String(card.sequence).padStart(2, "0")} · {card.title}</h3><span className="archive-caption">-{card.removedSeconds.toFixed(2)}s</span></div>
      <p className="archive-caption">Same start {card.startSeconds.toFixed(2)}s · current ends {card.currentEndSeconds.toFixed(2)}s · proposed ends {card.proposedEndSeconds.toFixed(2)}s{card.nextSourceUrl ? ` · next Track ${card.nextPreviewSeconds.toFixed(2)}s` : " · final Track"}</p>
      <div className="tail-audition-actions">
        <Button tone="secondary" size="compact" onClick={() => playCurrent(card)}>{playing === `${card.trackId}:current` ? "Playing current…" : "Play current tail"}</Button>
        <Button tone="quiet" size="compact" onClick={() => playProposed(card)}>{playing?.startsWith(`${card.trackId}:proposed`) ? "Playing proposed…" : "Play proposed transition"}</Button>
        <Button tone="quiet" size="compact" onClick={() => stop(card)} disabled={!playing?.startsWith(card.trackId)}>Stop</Button>
      </div>
      <div className="tail-audition-decision" role="group" aria-label={`Tail decision for ${card.title}`}>
        <span className="archive-caption">Decision: {card.decision === "pending" ? "Pending" : card.decision === "keep-current" ? "Keep current" : "Use proposed"}</span>
        <Button tone={card.decision === "keep-current" ? "secondary" : "quiet"} size="compact" disabled={savingTrackId === card.trackId} onClick={() => onDecision(card.trackId, "keep-current")}>Keep current</Button>
        <Button tone={card.decision === "use-proposed" ? "secondary" : "quiet"} size="compact" disabled={savingTrackId === card.trackId} onClick={() => onDecision(card.trackId, "use-proposed")}>Use proposed</Button>
      </div>
      <audio ref={(element) => { currentAudio.current[card.trackId] = element; }} src={card.currentSourceUrl} preload="metadata" onTimeUpdate={(event) => handleCurrentTime(card, event)} aria-label={`Current tail audio for ${card.title}`} />
      <audio ref={(element) => { proposedAudio.current[card.trackId] = element; }} src={card.currentSourceUrl} preload="metadata" onTimeUpdate={(event) => handleProposedTime(card, event)} aria-label={`Proposed tail audio for ${card.title}`} />
      {card.nextSourceUrl && <audio ref={(element) => { nextAudio.current[card.trackId] = element; }} src={card.nextSourceUrl} preload="metadata" onTimeUpdate={(event) => handleNextTime(card, event)} aria-label={`Next Track transition audio for ${card.title}`} />}
    </article>)}</div>
  </section>;
}

function AudioExportPanel({ state, proofApproved }: { state: VideoConfigState; proofApproved: boolean }) {
  const [audioPackage, setAudioPackage] = useState<AudioPackageState | null>(null);
  const [audioJob, setAudioJob] = useState<AudioPackageJob | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AudioMetadata & { coverChoice: "artwork" | "thumbnail" | "custom" | "none"; customCoverPath: string }>({
    title: state.config.album,
    artist: state.config.artist,
    album: state.config.album,
    albumArtist: state.config.artist,
    year: "",
    genre: "Instrumental",
    comment: "Full album instrumental mix",
    coverChoice: "artwork",
    customCoverPath: "",
  });

  useEffect(() => {
    fetchAudioPackage().then(setAudioPackage).catch(() => setAudioPackage(null));
  }, []);

  useEffect(() => {
    if (!audioJob || !["queued", "running", "stopping"].includes(audioJob.status)) return;
    const timer = window.setInterval(() => {
      fetchAudioPackageJob(audioJob.jobId).then((next) => {
        setAudioJob(next);
        if (next.status === "complete") fetchAudioPackage().then(setAudioPackage).catch(() => undefined);
      }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read Audio Mix Package status"));
    }, 700);
    return () => window.clearInterval(timer);
  }, [audioJob]);

  async function exportAudio() {
    setWorking(true);
    setError(null);
    try {
      const result = await startAudioPackage(draft);
      if ("jobId" in result) setAudioJob(result);
      else setAudioPackage(result);
      toast.success("Audio export started", { description: "The MP3 is assembled from the approved frame timeline." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Audio Mix Package could not start");
    } finally {
      setWorking(false);
    }
  }

  async function stopAudio() {
    if (!audioJob) return;
    try { setAudioJob(await stopAudioPackageJob(audioJob.jobId)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Audio export could not be stopped"); }
  }

  async function retryAudio() {
    if (!audioJob) return;
    try { setAudioJob(await retryAudioPackageJob(audioJob.jobId)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Audio export could not be retried"); }
  }

  async function openFolder() {
    try { await openAudioPackageFolder(); toast.message("Audio package folder opened"); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Audio package folder could not be opened"); }
  }

  const active = Boolean(audioJob && ["queued", "running", "stopping"].includes(audioJob.status));
  return <section className="archive-panel audio-export-panel" aria-labelledby="audio-export-title">
    <div className="archive-panel-heading"><div><h2 id="audio-export-title">Full album MP3</h2><p className="archive-caption">One audio mix from the approved Final Instrumentals and the shared effective timeline.</p></div><StatusMark status={audioPackage?.ready ? "ready" : "missing"} label={audioPackage?.ready ? "Package ready" : proofApproved ? "Ready to export" : "Proof approval required"} /></div>
    <div className="audio-export-summary"><strong>{state.composition.timeline.length} Tracks</strong><span>·</span><strong>{state.composition.durationSeconds.toFixed(1)}s</strong><span>·</span><span>CBR 320 kbps · stereo · 44.1 kHz</span></div>
    <div className="audio-export-grid">
      {(["title", "artist", "album", "albumArtist", "year", "genre", "comment"] as const).map((key) => <label key={key}>{key === "albumArtist" ? "Album artist" : key[0].toUpperCase() + key.slice(1)}<input value={draft[key]} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} maxLength={240} /></label>)}
      <label>Cover<select value={draft.coverChoice} onChange={(event) => setDraft((current) => ({ ...current, coverChoice: event.target.value as typeof current.coverChoice }))}><option value="artwork">Current artwork</option><option value="thumbnail">Current thumbnail</option><option value="custom">Custom local image</option><option value="none">No cover</option></select></label>
      {draft.coverChoice === "custom" && <label className="audio-export-custom-cover">Custom image path<input value={draft.customCoverPath} onChange={(event) => setDraft((current) => ({ ...current, customCoverPath: event.target.value }))} placeholder="Local PNG or JPEG path" /></label>}
    </div>
    <div className="archive-panel-actions"><Button tone="primary" onClick={() => void exportAudio()} disabled={!proofApproved || working || active}>{active ? "Exporting MP3…" : proofApproved ? "Export MP3" : "Approve Proof Pack first"}</Button>{audioPackage?.ready && <Button tone="quiet" onClick={() => void openFolder()}>Open Folder</Button>}{audioJob && ["failed", "cancelled", "interrupted"].includes(audioJob.status) && <Button tone="secondary" onClick={() => void retryAudio()}>Retry</Button>}{active && <Button tone="quiet" onClick={() => void stopAudio()}>Cancel</Button>}</div>
    {audioJob && <div className="video-render-status" role="status" aria-live="polite"><div className="video-render-status-line"><strong>{audioJob.status}</strong><span>{audioJob.stage} · {Math.round(audioJob.progress * 100)}%</span></div><div className="video-render-progress" aria-hidden="true"><span style={{ transform: `scaleX(${audioJob.progress})` }} /></div><p className="archive-caption">{audioJob.message}</p>{audioJob.error && <p className="video-render-error">{audioJob.error}</p>}</div>}
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={18} aria-hidden="true" /><span>{error}</span></div>}
    {audioPackage?.issues && audioPackage.issues.length > 0 && <ul className="video-issue-list">{audioPackage.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
    {audioPackage?.ready && <details className="video-technical-details"><summary>Advanced</summary><div className="video-path-list"><div><strong>Package</strong><span className="archive-path">{audioPackage.packageFolder}</span></div><div><strong>Manifest</strong><span className="archive-path">{audioPackage.manifestPath}</span></div><div><strong>MP3</strong><span className="archive-path">{audioPackage.artifacts?.albumMix?.sha256}</span></div></div></details>}
  </section>;
}

function VideoSurface() {
  const confirm = useConfirmDialog();
  const [state, setState] = useState<VideoConfigState | null>(null);
  const [renderJob, setRenderJob] = useState<VideoRenderJob | null>(null);
  const [proof, setProof] = useState<VideoProofState | null>(null);
  const [proofJob, setProofJob] = useState<VideoProofJob | null>(null);
  const [tailAudition, setTailAudition] = useState<VideoTailAuditionState | null>(null);
  const [savingTailTrackId, setSavingTailTrackId] = useState<string | null>(null);
  const [renderMode, setRenderMode] = useState<VideoExportMode>("fast");
  const [videoPackage, setVideoPackage] = useState<VideoPackageState | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [packageWorking, setPackageWorking] = useState(false);
  const [packageNotes, setPackageNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    artist: "",
    album: "",
    displayFontFamily: "Bevan",
    utilityFontFamily: "Atkinson Hyperlegible Next",
    colors: { primary: "#B74633", secondary: "#F7F3EA", accent: "#B74633", marker: "#D99A59", scrim: "#22201F" },
    cinematicFinish: "Textured" as "Off" | "Subtle" | "Textured",
    reducedMotion: false,
    brandEnabled: false,
    brandLibraryPath: "",
    brandThumbnailStamp: false,
    brandThumbnailCorner: "top-left",
    descriptionNotes: "",
    artworkMode: "Auto" as "Auto" | "Original",
    trackOverrides: {} as Record<string, string>,
  });

  useEffect(() => {
    fetchVideoConfig().then((next) => {
      setState(next);
      setDraft({
        artist: next.config.artist,
        album: next.config.album,
        displayFontFamily: next.config.typography.displayFontFamily,
        utilityFontFamily: next.config.typography.utilityFontFamily,
        colors: next.config.colors,
        cinematicFinish: next.config.cinematicFinish,
        reducedMotion: next.config.reducedMotion,
        brandEnabled: Boolean(next.config.brand?.enabled),
        brandLibraryPath: next.config.brand?.libraryPath ?? "",
        brandThumbnailStamp: Boolean(next.config.brand?.thumbnailStamp?.enabled),
        brandThumbnailCorner: next.config.brand?.thumbnailStamp?.corner ?? "top-left",
        descriptionNotes: next.config.descriptionNotes,
        artworkMode: next.preparation?.artworkMode ?? "Auto",
        trackOverrides: next.preparation?.trackOverrides ?? {},
      });
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read Video configuration")).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchVideoPackage().then(setVideoPackage).catch(() => setVideoPackage(null));
    fetchVideoProof().then(setProof).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read Release Proof Pack"));
    fetchVideoTailAudition().then(setTailAudition).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read tail audition"));
  }, []);

  useEffect(() => {
    const storedJobId = window.localStorage.getItem(VIDEO_RENDER_JOB_STORAGE_KEY);
    if (!storedJobId) return;
    fetchVideoRenderJob(storedJobId)
      .then(setRenderJob)
      .catch(() => window.localStorage.removeItem(VIDEO_RENDER_JOB_STORAGE_KEY));
  }, []);

  useEffect(() => {
    if (!renderJob) return;
    if (isVideoRenderActive(renderJob.status)) window.localStorage.setItem(VIDEO_RENDER_JOB_STORAGE_KEY, renderJob.jobId);
    else window.localStorage.removeItem(VIDEO_RENDER_JOB_STORAGE_KEY);
  }, [renderJob]);

  useEffect(() => {
    const storedJobId = window.localStorage.getItem(VIDEO_PROOF_JOB_STORAGE_KEY);
    if (!storedJobId) return;
    fetchVideoProofJob(storedJobId)
      .then(setProofJob)
      .catch(() => window.localStorage.removeItem(VIDEO_PROOF_JOB_STORAGE_KEY));
  }, []);

  useEffect(() => {
    if (!proofJob) return;
    if (["queued", "running", "stopping"].includes(proofJob.status)) window.localStorage.setItem(VIDEO_PROOF_JOB_STORAGE_KEY, proofJob.jobId);
    else {
      window.localStorage.removeItem(VIDEO_PROOF_JOB_STORAGE_KEY);
      fetchVideoProof().then(setProof).catch(() => undefined);
    }
  }, [proofJob]);

  useEffect(() => {
    if (!renderJob || !["queued", "running", "stopping"].includes(renderJob.status)) return;
    const timer = window.setInterval(() => {
      fetchVideoRenderJob(renderJob.jobId).then(setRenderJob).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read Video render status"));
    }, 500);
    return () => window.clearInterval(timer);
  }, [renderJob]);

  useEffect(() => {
    if (!proofJob || !["queued", "running", "stopping"].includes(proofJob.status)) return;
    const timer = window.setInterval(() => {
      fetchVideoProofJob(proofJob.jobId).then(setProofJob).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not read Proof Pack status"));
    }, 700);
    return () => window.clearInterval(timer);
  }, [proofJob]);

  async function saveVideoConfiguration(refreshBrand = false) {
    setWorking(true);
    setError(null);
    try {
      const next = await configureVideo({
        artist: draft.artist,
        album: draft.album,
        typography: { displayFontFamily: draft.displayFontFamily, utilityFontFamily: draft.utilityFontFamily },
        colors: draft.colors,
        cinematicFinish: draft.cinematicFinish,
        reducedMotion: draft.reducedMotion,
        brand: { enabled: draft.brandEnabled, refresh: refreshBrand, libraryPath: draft.brandLibraryPath || undefined, thumbnailStamp: { enabled: draft.brandThumbnailStamp, corner: draft.brandThumbnailCorner, widthFraction: 0.045 } },
        descriptionNotes: draft.descriptionNotes,
        preparation: { artworkMode: draft.artworkMode, trackOverrides: draft.trackOverrides },
      });
      setState(next);
      toast.success(next.ready ? "Album Landscape is ready" : "Video configuration saved", { description: next.ready ? "The Player uses the current validated Final Instrumentals." : "Review the readiness notes before previewing." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video configuration could not be saved");
    } finally {
      setWorking(false);
    }
  }

  async function refreshPreparation() {
    setWorking(true);
    setError(null);
    try {
      const next = await refreshVideoPreparation({ artworkMode: draft.artworkMode, trackOverrides: draft.trackOverrides });
      setState(next);
      toast.success("Video preparation refreshed", { description: "Artwork, endings and the effective timeline are current." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video preparation could not be refreshed");
    } finally {
      setWorking(false);
    }
  }

  async function saveTailDecision(trackId: string, decision: VideoTailAuditionCard["decision"]) {
    setSavingTailTrackId(trackId);
    try {
      setTailAudition(await saveVideoTailAuditionDecision(trackId, decision));
      toast.success("Tail decision saved", { description: "The production timeline remains unchanged until you apply an explicit override." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Tail audition decision could not be saved");
    } finally {
      setSavingTailTrackId(null);
    }
  }

  async function runSyntheticRender() {
    setError(null);
    try {
      const next = await startSyntheticVideoRender();
      setRenderJob(next);
      toast.success("Synthetic smoke started", { description: "A bounded synthetic renderer check is running." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Synthetic Video render could not start");
    }
  }

  async function runFastSyntheticRender() {
    setError(null);
    try {
      const next = await startFastSyntheticVideoRender();
      setRenderJob(next);
      toast.success("Fast synthetic check started", { description: "A bounded video-only export and audio mux are running." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Fast synthetic Video render could not start");
    }
  }

  async function exportVideo() {
    setError(null);
    try {
      const next = renderMode === "fast" ? await startFastRealVideoRender() : await startReferenceVideoRender();
      setRenderJob(next);
      toast.success(`${renderMode === "fast" ? "Fast" : "Reference"} Video Export started`, { description: "The current Project Folder snapshot is being rendered." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video Export could not start");
    }
  }

  async function generateProof() {
    setError(null);
    try {
      const next = await startVideoProof();
      setProofJob(next);
      toast.success("Release Proof Pack started", { description: "Opening, transitions, long title, closing and thumbnail are being validated." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Release Proof Pack could not start");
    }
  }

  async function approveProof() {
    if (!proof?.proofId) return;
    if (!(await confirm({
      title: "Approve this Release Proof Pack?",
      description: "The current proof becomes the explicit gate for sustained Video Export. Any later proof change invalidates this approval.",
      confirmLabel: "Approve proof pack",
    }))) return;
    setError(null);
    try {
      setProof(await approveVideoProof(proof.proofId));
      toast.success("Release Proof Pack approved", { description: "Fast and Reference sustained exports are now available for this fingerprint." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Proof Pack approval could not be saved");
    }
  }

  async function rejectProof() {
    if (!proof?.proofId) return;
    if (!(await confirm({
      title: "Reject this Release Proof Pack?",
      description: "Sustained Video Export will remain blocked until a new proof pack is generated and approved.",
      confirmLabel: "Reject proof pack",
      tone: "danger",
    }))) return;
    setError(null);
    try {
      setProof(await rejectVideoProof(proof.proofId));
      toast.message("Release Proof Pack rejected");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Proof Pack rejection could not be saved");
    }
  }

  async function cancelProof() {
    if (!proofJob) return;
    try { setProofJob(await stopVideoProofJob(proofJob.jobId)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Proof Pack could not be stopped"); }
  }

  async function retryProof() {
    if (!proofJob) return;
    try { setProofJob(await retryVideoProofJob(proofJob.jobId)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Proof Pack could not be retried"); }
  }

  async function cancelSyntheticRender() {
    if (!renderJob) return;
    try {
      setRenderJob(await stopVideoRender(renderJob.jobId));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video render could not be stopped");
    }
  }

  async function retryCurrentVideoRender() {
    if (!renderJob) return;
    try {
      setRenderJob(await retryVideoRender(renderJob.jobId));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video Export could not be retried");
    }
  }

  async function openRenderFolder() {
    try {
      await openProjectFolder();
      toast.message("Project Folder opened");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "The Project Folder could not be opened");
    }
  }

  async function buildSyntheticPackage() {
    setPackageWorking(true);
    setError(null);
    try {
      const next = await generateSyntheticVideoPackage(packageNotes);
      setVideoPackage(next);
      toast.success("Synthetic Video Package ready", { description: "MP4, thumbnail, chapters, description, and manifest are together." });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Synthetic Video Package could not be generated");
    } finally {
      setPackageWorking(false);
    }
  }

  async function copyPackageText(value: string, label: string) {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copied`);
  }

  async function openPackageFolder() {
    try {
      await openVideoPackageFolder();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Video Package folder could not be opened");
    }
  }

  if (loading) return <div className="archive-loading archive-loading-page" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Reading Video configuration…</div>;
  if (!state) return <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><span>{error ?? "No Album Project is open."}</span></div>;
  const playerProps = state.composition.inputProps as unknown as AlbumVideoProps;
  const proofApproved = isVideoProofApproved(Boolean(proof?.ready), proof?.approval.status);
  const proofArtifactUrl = (artifactPath: string) => {
    const filename = proofAssetFilename(artifactPath);
    return proof?.proofId ? `/api/video/proof/${encodeURIComponent(proof.proofId)}/asset/${encodeURIComponent(filename)}` : "";
  };
  return <div className="archive-project-stack video-surface">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><span>{error}</span></div>}
    <section className="archive-panel" aria-labelledby="video-config-title">
      <div className="archive-panel-heading"><div><h2 id="video-config-title">Video setup</h2><p className="archive-caption">One shared Remotion composition using the current validated Final Instrumentals. Configuration and copied assets live inside this Project Folder.</p></div><StatusMark status={state.ready ? "ready" : "missing"} label={state.ready ? "Ready for Player" : "Configuration needed"} /></div>
      <div className="video-config-grid">
        <label>Artist<input value={draft.artist} onChange={(event) => setDraft((current) => ({ ...current, artist: event.target.value }))} maxLength={120} /></label>
        <label>Album<input value={draft.album} onChange={(event) => setDraft((current) => ({ ...current, album: event.target.value }))} maxLength={120} /></label>
        <label>Display font role<select value={draft.displayFontFamily} onChange={(event) => setDraft((current) => ({ ...current, displayFontFamily: event.target.value }))}><option value="Bevan">Bevan · legacy display role</option><option value="Besley">Besley · artist, album, marker, title</option></select></label>
        <label>Utility font role<select value={draft.utilityFontFamily} onChange={(event) => setDraft((current) => ({ ...current, utilityFontFamily: event.target.value }))}><option value="Atkinson Hyperlegible Next">Atkinson Hyperlegible Next · timestamps</option></select></label>
        <label>Primary color<input type="color" value={draft.colors.primary} onChange={(event) => setDraft((current) => ({ ...current, colors: { ...current.colors, primary: event.target.value } }))} /></label>
        <label>Secondary color<input type="color" value={draft.colors.secondary} onChange={(event) => setDraft((current) => ({ ...current, colors: { ...current.colors, secondary: event.target.value } }))} /></label>
        <label>Accent color<input type="color" value={draft.colors.accent} onChange={(event) => setDraft((current) => ({ ...current, colors: { ...current.colors, accent: event.target.value } }))} /></label>
        <label>Track marker color<input type="color" value={draft.colors.marker} onChange={(event) => setDraft((current) => ({ ...current, colors: { ...current.colors, marker: event.target.value } }))} /></label>
        <label>Scrim color<input type="color" value={draft.colors.scrim} onChange={(event) => setDraft((current) => ({ ...current, colors: { ...current.colors, scrim: event.target.value } }))} /></label>
        <label>Cinematic finish<select value={draft.cinematicFinish} onChange={(event) => setDraft((current) => ({ ...current, cinematicFinish: event.target.value as "Off" | "Subtle" | "Textured" }))}><option value="Off">Off</option><option value="Subtle">Subtle</option><option value="Textured">Textured</option></select></label>
        <label className="video-motion-toggle"><input type="checkbox" checked={draft.reducedMotion} onChange={(event) => setDraft((current) => ({ ...current, reducedMotion: event.target.checked }))} />Reduced motion in Player</label>
        <label className="video-motion-toggle"><input type="checkbox" checked={draft.brandEnabled} onChange={(event) => setDraft((current) => ({ ...current, brandEnabled: event.target.checked }))} />Second Pressing branding · approved snapshot</label>
        <label>Brand source library<input value={draft.brandLibraryPath} onChange={(event) => setDraft((current) => ({ ...current, brandLibraryPath: event.target.value }))} placeholder="Configurable approved library path" /></label>
        <label className="video-motion-toggle"><input type="checkbox" checked={draft.brandThumbnailStamp} onChange={(event) => setDraft((current) => ({ ...current, brandThumbnailStamp: event.target.checked }))} disabled={!draft.brandEnabled} />Stamp watermark on thumbnail</label>
        <label>Thumbnail stamp corner<select value={draft.brandThumbnailCorner} onChange={(event) => setDraft((current) => ({ ...current, brandThumbnailCorner: event.target.value }))} disabled={!draft.brandEnabled || !draft.brandThumbnailStamp}><option value="top-left">Top left</option><option value="top-right">Top right</option><option value="bottom-left">Bottom left</option><option value="bottom-right">Bottom right</option></select></label>
        <label className="video-notes-field">Description notes<textarea value={draft.descriptionNotes} onChange={(event) => setDraft((current) => ({ ...current, descriptionNotes: event.target.value }))} maxLength={1000} rows={3} placeholder="Optional package notes" /></label>
      </div>
      <div className="archive-panel-actions"><Button tone="primary" onClick={() => void saveVideoConfiguration()} disabled={working || !draft.artist.trim() || !draft.album.trim()}>{working ? "Preparing · validating · saving…" : state.ready ? "Save video settings" : "Configure approved assets"}</Button>{draft.brandEnabled && <Button tone="quiet" onClick={() => void saveVideoConfiguration(true)} disabled={working}>Refresh approved brand snapshot</Button>}<Button tone="quiet" onClick={() => void refreshPreparation()} disabled={working}>{state.ready ? "Refresh preparation" : "Migrate and prepare"}</Button></div>
    </section>
    <section className="archive-panel video-preparation-summary" aria-labelledby="preparation-summary-title">
      <div className="archive-panel-heading"><div><h2 id="preparation-summary-title">Automatic preparation</h2><p className="archive-caption">The current artwork, Final Instrumentals and preparation settings produce one effective frame timeline.</p></div><StatusMark status={state.preparation?.status === "review" ? "missing" : state.preparation?.status === "ready" ? "ready" : "missing"} label={state.preparation?.status === "review" ? "Review suggested" : state.preparation?.status === "ready" ? "Prepared" : "Refresh needed"} /></div>
      <p className="video-preparation-line"><strong>{state.preparation?.artwork?.effective?.width ?? 1920}×{state.preparation?.artwork?.effective?.height ?? 1080} artwork</strong><span>·</span><strong>{state.preparation?.summary?.tracksAnalyzed ?? 0} endings analyzed</strong><span>·</span><strong>{(state.preparation?.summary?.secondsRemoved ?? 0).toFixed(1)} s removed</strong><span>·</span><strong>{state.preparation?.summary?.reviewCount ?? 0} to review</strong></p>
      {state.preparation?.status === "review" && <p className="archive-caption">Tracks marked for review keep their full duration until you choose an Advanced override.</p>}
    </section>
    <details className="archive-panel video-technical-details" open={state.preparation?.status === "review"}>
      <summary>Advanced preparation</summary>
      <p className="archive-caption">Windowed RMS + hysteresis: {state.preparation?.settings.windowSeconds ?? 0.25}s windows; enter {state.preparation?.settings.enterThresholdDb ?? -45} dB; exit {state.preparation?.settings.exitThresholdDb ?? -60} dB; hold {state.preparation?.settings.releaseHoldSeconds ?? 0.75}s; padding {state.preparation?.settings.tailPaddingSeconds ?? state.preparation?.settings.retainedTailSeconds ?? 1}s.</p>
      <div className="video-technical-body"><div className="video-config-grid"><label>Artwork source<select value={draft.artworkMode} onChange={(event) => setDraft((current) => ({ ...current, artworkMode: event.target.value as "Auto" | "Original" }))}><option value="Auto">Auto · deterministic 4K when needed</option><option value="Original">Original · keep uploaded artwork</option></select></label><div className="archive-caption">Subtle texture is the recommended default. The original artwork remains preserved; derived files are project-owned and cacheable.</div></div><div className="track-table-wrap"><table className="track-table"><caption className="sr-only">Automatic preparation per Track</caption><thead><tr><th scope="col">Track</th><th scope="col">Original</th><th scope="col">Tail</th><th scope="col">Effective</th><th scope="col">Override</th></tr></thead><tbody>{state.composition.timeline.map((track) => <tr key={track.trackId}><th scope="row" title={track.title}>{String(track.sequence).padStart(2, "0")} · {track.title}</th><td className="tabular-nums">{(track.originalDurationSeconds ?? track.durationSeconds).toFixed(2)}s</td><td className="tabular-nums">{(track.trailingSilenceSeconds ?? 0).toFixed(2)}s · {track.silenceStatus ?? "pending"}</td><td className="tabular-nums">{track.durationSeconds.toFixed(2)}s</td><td><select aria-label={`Preparation override for ${track.title}`} value={draft.trackOverrides[track.trackId] ?? "automatic"} onChange={(event) => setDraft((current) => ({ ...current, trackOverrides: { ...current.trackOverrides, [track.trackId]: event.target.value === "automatic" ? "" : event.target.value } }))}><option value="automatic">Automatic</option><option value="keep-full">Keep full</option><option value="trim-proposed">Trim proposed</option></select></td></tr>)}</tbody></table></div></div>
      <div className="archive-caption">Proposed removal per Track: {state.composition.timeline.map((track) => `${String(track.sequence).padStart(2, "0")} ${track.title}: ${(track.proposedRemovalSeconds ?? 0).toFixed(2)}s`).join(" · ")}</div>
    </details>
    <details className="archive-panel video-technical-details">
      <summary>Technical details and provenance</summary>
      <div className="video-technical-body"><h2 id="video-readiness-title">{state.ready ? "Ready from current Selections" : "Recovery notes"}</h2>
      <div className="video-path-list"><div><strong>Project Folder</strong><span className="archive-path">{state.projectFolder}</span></div><div><strong>Configuration</strong><span className="archive-path">{state.configPath}</span></div>{Object.entries(state.assets).map(([role, asset]) => <div key={role}><strong>{role}</strong><span className="archive-path">{asset.path} · {asset.sha256 ? asset.sha256.slice(0, 12) + "…" : "pending fingerprint"}</span></div>)}</div>
      {state.issues.length > 0 && <ul className="video-issue-list">{state.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {state.ready && <p className="archive-caption">Provenance snapshot: {state.composition.timeline.length} Tracks · {state.composition.durationSeconds / 60 > 0 ? Math.round(state.composition.durationSeconds / 60) + " min" : "—"} · no Candidate fallback.</p>}
      </div>
    </details>
    {state.ready && <section className="archive-panel video-proof-panel" aria-labelledby="video-proof-title">
      <div className="archive-panel-heading"><div><h2 id="video-proof-title">Release Proof Pack</h2><p className="archive-caption">Review the highest-risk moments before a sustained Video Export.</p></div><StatusMark status={proofApproved ? "ready" : "missing"} label={proofStatusLabel(Boolean(proof?.ready), proof?.approval.status)} /></div>
      {!proof?.proofId && !proofJob && <div className="archive-panel-actions"><Button tone="primary" onClick={() => void generateProof()}>Generate Proof Pack</Button><span className="archive-caption">Bounded clips only; no album render.</span></div>}
      {proofJob && <div className="video-render-status" role="status" aria-live="polite"><div className="video-render-status-line"><strong>{proofJob.status}</strong><span>{proofJob.stage} · {Math.round(proofJob.progress * 100)}%</span></div><div className="video-render-progress" aria-hidden="true"><span style={{ transform: `scaleX(${proofJob.progress})` }} /></div><p className="archive-caption">{proofJob.message}</p>{proofJob.error && <p className="video-render-error">{proofJob.error}</p>}{["queued", "running", "stopping"].includes(proofJob.status) && <Button tone="quiet" size="compact" onClick={() => void cancelProof()}>Cancel Proof Pack</Button>}{["failed", "cancelled", "interrupted"].includes(proofJob.status) && <Button tone="secondary" size="compact" onClick={() => void retryProof()}>Retry Proof Pack</Button>}</div>}
      {proof?.proofId && proof.artifacts && <>
        <div className="video-proof-grid">{Object.entries(proof.artifacts).map(([caseId, artifact]) => { const url = proofArtifactUrl(artifact.path); const isStill = artifact.kind === "still" || artifact.kind === "thumbnail"; return <article className="video-proof-card" key={caseId}><div className="video-proof-card-heading"><h3>{caseId === "transition-standard" ? "Transition" : caseId === "transition-risk" ? "Risk transition" : caseId === "long-title" ? "Long title" : caseId[0].toUpperCase() + caseId.slice(1)}</h3><span className="archive-caption">{String(artifact.selection?.durationSeconds ?? "")}s</span></div>{isStill ? <img src={url} alt={`${caseId} proof frame`} /> : <video controls preload="metadata" src={url} aria-label={`${caseId} Proof Pack clip`} />}{caseId === "long-title" && typeof artifact.selection?.title === "string" && <p className="archive-caption">{artifact.selection.title}</p>}<p className="archive-caption">{String(artifact.selection?.reason ?? "")}</p></article>; })}</div>
        <div className="archive-panel-actions"><span className="archive-caption">Current approval: {proof.approval.status}</span>{canApproveVideoProof(proof.approval.status) && <Button tone="primary" onClick={() => void approveProof()}>Approve proof</Button>}{proof.approval.status !== "approved" && <Button tone="quiet" onClick={() => void rejectProof()}>Reject proof</Button>}{proof.approval.status === "stale" && <Button tone="primary" onClick={() => void generateProof()}>Generate new proof</Button>}</div>
        <details className="video-technical-details"><summary>Advanced</summary><div className="video-path-list"><div><strong>Proof manifest</strong><span className="archive-path">{proof.manifestPath}</span></div><div><strong>Input fingerprint</strong><span className="archive-path">{proof.inputFingerprint}</span></div></div></details>
      </>}
      {proof?.issues && proof.issues.length > 0 && <ul className="video-issue-list">{proof.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
    </section>}
    {state.ready && tailAudition && tailAudition.cards.length > 0 && <TailAuditionPanel state={tailAudition} savingTrackId={savingTailTrackId} onDecision={(trackId, decision) => void saveTailDecision(trackId, decision)} />}
    {state.ready && <section className="archive-panel" aria-labelledby="video-preview-title">
      <div className="archive-panel-heading"><div><h2 id="video-preview-title">Preview · Album Landscape · {state.composition.timeline.length} Tracks</h2><p className="archive-caption">Use the controls to inspect the shared timeline and current Final Instrumentals.</p></div><span className="archive-caption">{state.composition.width}×{state.composition.height} · {state.composition.fps} fps</span></div>
      <div className="video-player-frame"><Player component={AlbumLandscape} durationInFrames={state.composition.durationInFrames} compositionWidth={state.composition.width} compositionHeight={state.composition.height} fps={state.composition.fps} inputProps={playerProps} controls clickToPlay loop style={{ width: "100%", aspectRatio: "16 / 9" }} /></div>
    </section>}
    {state.ready && <section className="archive-panel" aria-labelledby="video-render-title">
      <div className="archive-panel-heading"><div><h2 id="video-render-title">Export video</h2><p className="archive-caption">Fast is recommended for the prepared Album Project. Reference remains available for visual parity and recovery.</p></div><div className="archive-panel-actions"><label className="video-export-mode">Mode<select aria-label="Video Export mode" value={renderMode} onChange={(event) => setRenderMode(event.target.value as VideoExportMode)}><option value="fast">{videoExportModeLabel("fast")}</option><option value="reference">{videoExportModeLabel("reference")}</option></select></label><Button tone="primary" onClick={() => void exportVideo()} disabled={!proofApproved || isVideoRenderActive(renderJob?.status)}>{isVideoRenderActive(renderJob?.status) ? "Export in progress…" : proofApproved ? "Export video" : "Approve Proof Pack first"}</Button><Button tone="quiet" size="compact" onClick={() => void openRenderFolder()}>Open Folder</Button></div></div>
      <p className="archive-caption video-export-estimate">Fast checks browser capability before starting. Sustained Fast and Reference exports unlock only after approval of the current Proof Pack.</p>
      <div className="archive-panel-actions video-synthetic-actions"><Button tone="secondary" onClick={() => void runFastSyntheticRender()} disabled={isVideoRenderActive(renderJob?.status)}>Run bounded Fast check</Button><Button tone="quiet" onClick={() => void runSyntheticRender()} disabled={isVideoRenderActive(renderJob?.status)}>Run bounded Reference check</Button></div>
      {renderJob && <div className="video-render-status" role="status" aria-live="polite"><div className="video-render-status-line"><strong>{renderJob.status}</strong><span>{renderJob.stage} · {Math.round(renderJob.progress * 100)}%</span></div><div className="video-render-progress" aria-hidden="true"><span style={{ transform: `scaleX(${renderJob.progress})` }} /></div><p className="archive-caption">{renderJob.message}</p>{formatElapsedMs(rendererElapsedMs(renderJob.renderer)) && <p className="archive-caption">{formatElapsedMs(rendererElapsedMs(renderJob.renderer))}</p>}{renderJob.error && <p className="video-render-error">{renderJob.error}</p>}<details className="video-technical-details"><summary>Advanced diagnostics</summary><div className="video-path-list">{renderJob.promotedPath ? <div><strong>MP4</strong><span className="archive-path">{renderJob.promotedPath}</span></div> : null}{renderJob.validation?.sha256 ? <div><strong>SHA-256</strong><span className="archive-path">{renderJob.validation.sha256}</span></div> : null}{renderJob.renderManifestPath ? <div><strong>Render manifest</strong><span className="archive-path">{renderJob.renderManifestPath}</span></div> : null}{renderJob.capability ? <div><strong>Capability</strong><span className="archive-path">Web Renderer capability recorded</span></div> : null}{renderJob.telemetry?.peakRssBytes ? <div><strong>Peak RSS</strong><span className="archive-path">{Math.round(Number(renderJob.telemetry.peakRssBytes) / 1024 / 1024)} MiB</span></div> : null}</div></details></div>}
      {renderJob && isVideoRenderActive(renderJob.status) && <Button tone="quiet" onClick={() => void cancelSyntheticRender()}>Cancel</Button>}{renderJob && canRetryVideoRender(renderJob.status) && <Button tone="secondary" onClick={() => void retryCurrentVideoRender()}>Retry</Button>}
    </section>}
    {state.ready && <AudioExportPanel state={state} proofApproved={proofApproved} />}
    {state.ready && <section className="archive-panel" aria-labelledby="video-package-title">
      <div className="archive-panel-heading"><div><h2 id="video-package-title">Video Package</h2><p className="archive-caption">Generate a bounded synthetic package for inspection, or open the current validated real package below.</p></div><div className="archive-panel-actions"><Button tone="secondary" onClick={() => void buildSyntheticPackage()} disabled={packageWorking}>{packageWorking ? "Rendering thumbnail · packaging…" : "Generate synthetic package"}</Button>{videoPackage?.ready && <Button tone="quiet" onClick={() => void openPackageFolder()}>Open package folder</Button>}</div></div>
      <label className="video-notes-field video-package-notes">Editable package notes<textarea value={packageNotes} onChange={(event) => setPackageNotes(event.target.value)} maxLength={1000} rows={2} placeholder="Optional notes for the synthetic package description" /></label>
      {videoPackage?.issues && videoPackage.issues.length > 0 && <ul className="video-issue-list">{videoPackage.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {videoPackage?.ready && <div className="video-package-details"><div className="video-path-list">{Object.entries(videoPackage.artifacts ?? {}).map(([key, artifact]) => <div key={key}><strong>{key}</strong><span className="archive-path">{artifact.path} · SHA-256 {artifact.sha256}</span></div>)}</div><div className="video-package-copy"><div><strong>Chapters</strong><pre>{videoPackage.chapters}</pre><Button tone="quiet" size="compact" onClick={() => void copyPackageText(videoPackage.chapters ?? "", "Chapters")}>Copy chapters</Button></div><div><strong>Description</strong><pre>{videoPackage.description}</pre><Button tone="quiet" size="compact" onClick={() => void copyPackageText(videoPackage.description ?? "", "Description")}>Copy description</Button></div></div></div>}
    </section>}
    {state.ready && <details className="archive-panel video-technical-details">
      <summary>Track timeline and file mapping</summary>
      <div className="video-technical-body"><h2 id="video-timeline-title">Track timeline</h2>
      <div className="track-table-wrap"><table className="track-table"><caption className="sr-only">Album Landscape Track timeline</caption><thead><tr><th scope="col">#</th><th scope="col">Track</th><th scope="col">Start</th><th scope="col">Duration</th><th scope="col">Final</th></tr></thead><tbody>{state.composition.timeline.map((track) => <tr key={track.trackId}><td className="tabular-nums">{String(track.sequence).padStart(2, "0")}</td><th scope="row" title={track.title}>{track.title}</th><td className="tabular-nums">{track.startFrame}</td><td className="tabular-nums">{track.durationInFrames}</td><td>{track.finalPath}</td></tr>)}</tbody></table></div>
      </div>
    </details>}
  </div>;
}

function ExportSurface({ onOpenCompare }: { onOpenCompare: () => void }) {
  const [project, setProject] = useState<ProjectManifest | null>(null);
  const [plan, setPlan] = useState<ExportPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completePath, setCompletePath] = useState<string | null>(null);

  async function refresh() {
    const [nextProject, nextPlan] = await Promise.all([fetchProject(), fetchExportStatus()]);
    setProject(nextProject);
    setPlan(nextPlan);
  }

  useEffect(() => { refresh().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load export state")).finally(() => setLoading(false)); }, []);

  async function exportNow() {
    setWorking(true);
    setError(null);
    try {
      const result = await exportProject();
      setCompletePath(result.destinationFolder);
      await refresh();
      toast.success("Final Instrumentals exported");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Export could not complete");
    } finally {
      setWorking(false);
    }
  }

  async function openFolder() {
    try { await openExportFolder(); toast.message("Export folder opened"); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "The export folder could not open"); }
  }

  if (loading) return <div className="archive-loading archive-loading-page" role="status"><CircleNotch size={20} className="archive-spin" aria-hidden="true" />Reading Selection ledger…</div>;
  if (!project || !plan) return <PlaceholderSurface icon={<Export size={26} />} title="Export follows a complete Selection set" copy="Open an Album Project first." action="Open Compare" onAction={onOpenCompare} />;
  return <div className="archive-project-stack">
    {error && <div className="archive-alert archive-alert-danger" role="alert"><WarningCircle size={20} aria-hidden="true" /><div><strong>Export action failed</strong><span>{error}</span></div></div>}
    <section className="archive-panel" aria-labelledby="export-title"><div className="archive-panel-heading"><div><h2 id="export-title">Final Instrumentals</h2><p className="archive-path" title={plan.destinationFolder}>Destination: {plan.destinationFolder}</p></div><div className="archive-panel-actions"><Button tone="quiet" size="compact" onClick={() => void openFolder()}>Open folder</Button><Button tone="primary" onClick={() => void exportNow()} disabled={!plan.ready || working}>{working ? "Exporting…" : "Export Final Instrumentals"}</Button></div></div>{completePath && <div className="export-complete" role="status"><CheckCircle size={20} aria-hidden="true" /><span>Export current in {completePath}</span></div>}</section>
    <section className="archive-panel" aria-labelledby="export-items-title"><div className="archive-panel-heading"><div><h2 id="export-items-title">{plan.items.length - plan.missing.length} / {plan.items.length} ready</h2></div></div><div className="export-ledger"><table className="track-table"><caption className="sr-only">Final Instrumental export readiness</caption><thead><tr><th scope="col">Track</th><th scope="col">Selection</th><th scope="col">Validation</th><th scope="col">Destination</th><th scope="col">Next</th></tr></thead><tbody>{plan.items.map((item) => <tr key={item.trackId}><th scope="row">{String(item.sequence).padStart(2, "0")} · {item.trackTitle}</th><td>{item.slot ? `Candidate ${item.slot}` : "Not selected"}</td><td><span className={`task-stage task-stage-${item.status}`}>{item.status === "valid" ? "Valid" : item.status === "missing" ? "Missing" : "Invalid"}</span>{item.reason && <small className="task-error">{item.reason}</small>}</td><td title={item.destinationPath}>{item.destinationPath.split("\\").pop()}</td><td>{item.status !== "valid" && <Button tone="quiet" size="compact" onClick={onOpenCompare}>Open Compare</Button>}</td></tr>)}</tbody></table></div></section>
    <details className="archive-panel video-technical-details" aria-labelledby="mapping-title"><summary>Canonical mapping</summary><h2 id="mapping-title">Selection summary</h2><pre className="selection-summary">{plan.selectionSummary || "No selections recorded yet."}</pre></details>
  </div>;
}

function PlaceholderSurface({ icon, title, copy, action, onAction }: { icon: React.ReactNode; title: string; copy: string; action: string; onAction: () => void }) {
  return <section className="archive-placeholder" aria-labelledby="placeholder-title"><div className="archive-placeholder-icon" aria-hidden="true">{icon}</div><h2 id="placeholder-title">{title}</h2><p>{copy}</p><Button tone="secondary" onClick={onAction}>{action}<ArrowSquareOut size={18} aria-hidden="true" /></Button></section>;
}

function clsx(...values: Array<string | false | undefined>) { return values.filter(Boolean).join(" "); }

export default App;
