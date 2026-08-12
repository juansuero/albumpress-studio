export type ProofApprovalStatus = "missing" | "pending" | "approved" | "rejected" | "stale";

export function isVideoProofApproved(ready: boolean, status: ProofApprovalStatus | undefined): boolean {
  return ready && status === "approved";
}

export function canApproveVideoProof(status: ProofApprovalStatus | undefined): boolean {
  return status !== "approved" && status !== "stale";
}

export function proofStatusLabel(ready: boolean, status: ProofApprovalStatus | undefined): string {
  if (isVideoProofApproved(ready, status)) return "Approved";
  if (status === "stale") return "Stale";
  if (status === "pending") return "Needs review";
  return "Not generated";
}

export function proofAssetFilename(assetPath: string): string {
  const parts = assetPath.split(/[\\/]/);
  return parts.at(-1) || assetPath;
}
