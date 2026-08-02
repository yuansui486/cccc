import type { MermaidConfig } from "mermaid";

export const MERMAID_MAX_SOURCE_LENGTH = 20_000;
export const MERMAID_MAX_EDGES = 500;

let renderSequence = 0;
let renderQueue: Promise<void> = Promise.resolve();

export type MermaidColorTheme = "default" | "dark";

export function isMermaidFenceLanguage(language: string): boolean {
  return language.trim().toLowerCase() === "mermaid";
}

export function canRenderMermaidSource(source: string): boolean {
  return source.length <= MERMAID_MAX_SOURCE_LENGTH;
}

export function containsMermaidImageShape(source: string): boolean {
  return /@\{[^{}]*\bimg\s*:/is.test(source);
}

export function decodeMermaidSource(encodedSource: string): string | null {
  try {
    return decodeURIComponent(encodedSource);
  } catch {
    return null;
  }
}

export function buildMermaidConfig(theme: MermaidColorTheme): MermaidConfig {
  return {
    startOnLoad: false,
    securityLevel: "strict",
    suppressErrorRendering: true,
    theme,
    maxTextSize: MERMAID_MAX_SOURCE_LENGTH,
    maxEdges: MERMAID_MAX_EDGES,
  };
}

async function renderMermaidNow(source: string, theme: MermaidColorTheme): Promise<string> {
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize(buildMermaidConfig(theme));
  renderSequence += 1;
  const { svg } = await mermaid.render(`onecolleague-mermaid-${renderSequence}`, source);
  return svg;
}

function renderMermaidDiagram(
  source: string,
  theme: MermaidColorTheme,
  isActive: () => boolean,
): Promise<string | null> {
  const render = renderQueue.then(() => (isActive() ? renderMermaidNow(source, theme) : null));
  renderQueue = render.then(
    () => undefined,
    () => undefined,
  );
  return render;
}

function showSourceFallback(block: HTMLElement, message: string): void {
  const target = block.querySelector<HTMLElement>("[data-mermaid-target]");
  const source = block.querySelector<HTMLElement>("[data-mermaid-source]");
  const error = block.querySelector<HTMLElement>("[data-mermaid-error]");
  if (target) target.hidden = true;
  if (source) source.hidden = false;
  if (error) {
    error.textContent = message;
    error.hidden = false;
  }
  block.dataset.mermaidState = "failed";
}

export function activateMermaidBlocks(container: HTMLElement, theme: MermaidColorTheme): () => void {
  let cancelled = false;

  for (const block of container.querySelectorAll<HTMLElement>("[data-mermaid-block]")) {
    const error = block.querySelector<HTMLElement>("[data-mermaid-error]");
    const sourceNode = block.querySelector<HTMLElement>("[data-mermaid-source]");
    const encodedSource = sourceNode?.dataset.source || "";
    const source = decodeMermaidSource(encodedSource);
    const renderFailed = error?.dataset.renderFailed || "Unable to render diagram; showing source.";
    const tooLarge = error?.dataset.tooLarge || "Diagram is too large; showing source.";

    if (source === null) {
      showSourceFallback(block, renderFailed);
      continue;
    }
    if (!canRenderMermaidSource(source) || containsMermaidImageShape(source)) {
      showSourceFallback(block, !canRenderMermaidSource(source) ? tooLarge : renderFailed);
      continue;
    }

    void renderMermaidDiagram(source, theme, () => !cancelled && block.isConnected)
      .then((svg) => {
        if (!svg || cancelled || !block.isConnected) return;
        const target = block.querySelector<HTMLElement>("[data-mermaid-target]");
        if (!target) return;
        target.innerHTML = svg;
        target.hidden = false;
        if (sourceNode) sourceNode.hidden = true;
        block.dataset.mermaidState = "rendered";
      })
      .catch(() => {
        if (!cancelled && block.isConnected) showSourceFallback(block, renderFailed);
      });
  }

  return () => {
    cancelled = true;
  };
}
