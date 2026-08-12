import { describe, expect, it } from "vitest";
import { canApproveVideoProof, isVideoProofApproved, proofAssetFilename, proofStatusLabel } from "./videoProof";

describe("Release Proof Pack UI state", () => {
  it("unlocks sustained export only for a ready approved proof", () => {
    expect(isVideoProofApproved(false, "approved")).toBe(false);
    expect(isVideoProofApproved(true, "pending")).toBe(false);
    expect(isVideoProofApproved(true, "stale")).toBe(false);
    expect(isVideoProofApproved(true, "approved")).toBe(true);
  });

  it("keeps stale proofs out of the approval action", () => {
    expect(canApproveVideoProof("pending")).toBe(true);
    expect(canApproveVideoProof("rejected")).toBe(true);
    expect(canApproveVideoProof("stale")).toBe(false);
    expect(proofStatusLabel(false, "stale")).toBe("Stale");
    expect(proofStatusLabel(true, "approved")).toBe("Approved");
  });

  it("serves durable artifact paths through their safe filename", () => {
    expect(proofAssetFilename("video/proofs/abc/opening.mp4")).toBe("opening.mp4");
    expect(proofAssetFilename("video\\proofs\\abc\\thumbnail.png")).toBe("thumbnail.png");
  });
});
