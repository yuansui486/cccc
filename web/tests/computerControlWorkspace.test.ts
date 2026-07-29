import { describe, expect, it } from "vitest";
import { computerControlGroupIdFromPath, isComputerControlPath } from "../src/utils/appTabs";
import { isValidNodePosition, normalizeDefinition } from "../src/pages/computerControl/types";
import { setupErrorMessage } from "../src/pages/computerControl/statusPresentation";
import { formatPickerDiagnostics, pickerCapabilitySummary } from "../src/pages/computerControl/pickerPresentation";

describe("computer control workspace routing", () => {
  it("explains native picker readiness and keeps snapshot fallback explicit", () => {
    const unavailable = {
      session_id: "pick_1",
      status: "active",
      native_available: false,
      overlay_available: false,
      diagnostics: { code: "native_uia_bindings_missing", message: "缺少 UIA 绑定" },
    };
    expect(pickerCapabilitySummary(unavailable)).toContain("读取快照");
    expect(formatPickerDiagnostics(unavailable)).toContain("native_uia_bindings_missing");
    expect(pickerCapabilitySummary({ ...unavailable, native_available: true, overlay_available: true })).toContain("均可用");
  });

  it("turns Python 9009 setup failures into an actionable message", () => {
    expect(setupErrorMessage("python.EXE exited with code 9009", "failed")).toContain("Windows 错误 9009");
    expect(setupErrorMessage("", "ready")).toBe("");
  });

  it("recognizes legacy paths and decodes the group id", () => {
    expect(isComputerControlPath("/computer-control/g%201")).toBe(true);
    expect(computerControlGroupIdFromPath("/ui/computer-control/g%201")).toBe("g 1");
    expect(computerControlGroupIdFromPath("/ui/")).toBe("");
  });
});

describe("workflow graph normalization", () => {
  it("rejects empty and partial positions", () => {
    expect(isValidNodePosition({})).toBe(false);
    expect(isValidNodePosition({ x: 10 })).toBe(false);
    expect(isValidNodePosition({ x: 10, y: 20 })).toBe(true);
  });

  it("lays out AI nodes with empty positions while preserving edges", () => {
    const definition = normalizeDefinition({
      nodes: [
        { id: "start", type: "start", title: "开始", position: {} },
        { id: "click", type: "action", title: "点击", position: {} },
        { id: "end", type: "end", title: "结束", position: {} },
      ],
      edges: [
        { id: "a", source: "start", target: "click" },
        { id: "b", source: "click", target: "end" },
      ],
    });
    expect(definition.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["start", "click"],
      ["click", "end"],
    ]);
    expect(new Set(definition.nodes.map((node) => `${node.position?.x}:${node.position?.y}`)).size).toBe(3);
    expect(definition.nodes.every((node) => isValidNodePosition(node.position))).toBe(true);
  });

  it("places branch siblings in separate rows", () => {
    const definition = normalizeDefinition({
      nodes: ["start", "left", "right", "end"].map((id) => ({ id, type: "action", title: id, position: {} })),
      edges: [
        { id: "a", source: "start", target: "left" },
        { id: "b", source: "start", target: "right" },
        { id: "c", source: "left", target: "end" },
        { id: "d", source: "right", target: "end" },
      ],
    });
    const left = definition.nodes.find((node) => node.id === "left")!;
    const right = definition.nodes.find((node) => node.id === "right")!;
    expect(left.position?.x).toBe(right.position?.x);
    expect(left.position?.y).not.toBe(right.position?.y);
  });
});
