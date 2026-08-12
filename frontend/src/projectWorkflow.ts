export type ProjectView = "loading" | "creating" | "current" | "empty";

export function resolveProjectView(projectLoading: boolean, pendingSource: string | null, hasProject: boolean): ProjectView {
  if (projectLoading) return "loading";
  if (pendingSource) return "creating";
  if (hasProject) return "current";
  return "empty";
}

export function hasPickedFolder(sourcePath: string | null): sourcePath is string {
  return Boolean(sourcePath?.trim());
}

export type ResourceState = "loading" | "ready" | "empty" | "error";

export function resolveResourceState<T>(value: T | null, loading: boolean, error: unknown): ResourceState {
  if (loading) return "loading";
  if (error) return "error";
  return value === null ? "empty" : "ready";
}
