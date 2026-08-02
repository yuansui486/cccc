import { describe, expect, it } from "vitest";
import {
  MERMAID_MAX_EDGES,
  MERMAID_MAX_SOURCE_LENGTH,
  buildMermaidConfig,
  canRenderMermaidSource,
  containsMermaidImageShape,
  decodeMermaidSource,
  isMermaidFenceLanguage,
} from "./mermaid";

describe("mermaid safety boundary", () => {
  it("recognizes only the mermaid fence", () => {
    expect(isMermaidFenceLanguage(" mermaid ")).toBe(true);
    expect(isMermaidFenceLanguage("javascript")).toBe(false);
  });

  it("bounds source and rejects image shapes", () => {
    expect(canRenderMermaidSource("a".repeat(MERMAID_MAX_SOURCE_LENGTH))).toBe(true);
    expect(canRenderMermaidSource("a".repeat(MERMAID_MAX_SOURCE_LENGTH + 1))).toBe(false);
    expect(containsMermaidImageShape("flowchart LR\na@{img: url}")).toBe(true);
    expect(containsMermaidImageShape("flowchart LR\na --> b")).toBe(false);
  });

  it("decodes the fenced source before rendering", () => {
    expect(decodeMermaidSource(encodeURIComponent("flowchart LR\na --> b\n"))).toBe("flowchart LR\na --> b\n");
    expect(decodeMermaidSource("%E0%A4%A")).toBeNull();
  });

  it("uses strict rendering with bounded graph size", () => {
    expect(buildMermaidConfig("dark")).toMatchObject({
      securityLevel: "strict",
      maxTextSize: MERMAID_MAX_SOURCE_LENGTH,
      maxEdges: MERMAID_MAX_EDGES,
      theme: "dark",
    });
  });
});
