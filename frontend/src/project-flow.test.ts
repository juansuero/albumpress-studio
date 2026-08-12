import { describe, expect, it } from "vitest";
import { hasPickedFolder, resolveProjectView, resolveResourceState } from "./projectWorkflow";

describe("project and resource state contracts", () => {
  it("keeps an existing project visible until a confirmed new project is chosen", () => {
    expect(resolveProjectView(false, null, true)).toBe("current");
    expect(resolveProjectView(false, "C:/Albums/Little Songs", true)).toBe("creating");
  });

  it("treats a cancelled native folder picker as no-op", () => {
    expect(hasPickedFolder(null)).toBe(false);
    expect(hasPickedFolder("   ")).toBe(false);
    expect(hasPickedFolder("C:/Albums/Little Songs")).toBe(true);
  });

  it("returns to the correct surface after creation cancellation or success", () => {
    expect(resolveProjectView(false, null, true)).toBe("current");
    expect(resolveProjectView(false, null, false)).toBe("empty");
  });

  it("never reports empty while a project request is unresolved", () => {
    expect(resolveResourceState(null, true, null)).toBe("loading");
    expect(resolveResourceState(null, false, null)).toBe("empty");
    expect(resolveResourceState({ id: "project" }, false, null)).toBe("ready");
    expect(resolveResourceState(null, false, new Error("offline"))).toBe("error");
  });
});
