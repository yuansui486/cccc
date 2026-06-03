import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Player, type PlayerRef } from "@remotion/player";
import { classNames } from "../../utils/classNames";
import { CheckIcon, CopyIcon, MutedIcon, PauseIcon, PlayIcon, RestoreIcon, VolumeIcon } from "../../components/Icons";
import { apiJson, withAuthToken } from "../../services/api";
import { VideoEditorRuntime, ensureVideoEditorHost, type ComponentRegistry } from "./VideoEditorRuntime";

type ClipProps = Record<string, unknown>;

type RemotionTrack = {
  id: string;
  name?: string;
  type?: string;
  enabled?: boolean;
};

type RemotionClip = {
  id: string;
  trackId: string;
  component: string;
  props?: ClipProps;
  from: number;
  durationInFrames: number;
};

type RemotionTimelineSpec = {
  composition: {
    id: string;
    fps: number;
    width: number;
    height: number;
    durationInFrames: number;
  };
  tracks: RemotionTrack[];
  clips: RemotionClip[];
  componentRegistry?: unknown;
  componentBundle?: unknown;
};

type BatchScope = "component" | "track" | "selection";
type NumericKey = "x" | "y" | "size" | "width" | "height";

const TRANSFORM_KEYS: NumericKey[] = ["x", "y", "size", "width", "height"];
const emptySpec: RemotionTimelineSpec = {
  composition: {
    id: "RemotionWorkspace",
    fps: 30,
    width: 480,
    height: 1040,
    durationInFrames: 1,
  },
  tracks: [],
  clips: [],
};

const componentScriptLoads = new Map<string, Promise<void>>();

declare global {
  interface Window {
    __OC_VIDEO_EDITOR_COMPONENTS__?: Record<string, ComponentRegistry>;
  }
}

function asNumber(value: unknown, fallback = 0): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

function getClipDefaults(clip: RemotionClip): Partial<Record<NumericKey, number>> {
  const props = clip.props || {};
  const defaults: Partial<Record<NumericKey, number>> = {};
  for (const key of TRANSFORM_KEYS) {
    if (Number.isFinite(Number(props[key]))) defaults[key] = Number(props[key]);
  }
  return defaults;
}

function getClipNumber(clip: RemotionClip, key: NumericKey): number {
  const props = clip.props || {};
  return asNumber(props[key], getClipDefaults(clip)[key] ?? 0);
}

function frameToTime(frame: number, fps: number): string {
  const seconds = Math.max(0, frame / Math.max(1, fps));
  const mm = Math.floor(seconds / 60);
  const ss = Math.floor(seconds % 60);
  const ff = Math.floor((seconds - Math.floor(seconds)) * fps);
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(ff).padStart(2, "0")}`;
}

function cloneSpec(spec: RemotionTimelineSpec): RemotionTimelineSpec {
  return JSON.parse(JSON.stringify(spec)) as RemotionTimelineSpec;
}

function normalizeSpec(raw: unknown): RemotionTimelineSpec {
  const source = (raw && typeof raw === "object" ? raw : {}) as Partial<RemotionTimelineSpec>;
  const composition = (source.composition && typeof source.composition === "object" ? source.composition : {}) as Partial<RemotionTimelineSpec["composition"]>;
  return {
    composition: {
      id: String(composition.id || "RemotionWorkspace"),
      fps: Number.isFinite(Number(composition.fps)) ? Number(composition.fps) : 30,
      width: Number.isFinite(Number(composition.width)) ? Number(composition.width) : 480,
      height: Number.isFinite(Number(composition.height)) ? Number(composition.height) : 1040,
      durationInFrames: Math.max(1, Number.isFinite(Number(composition.durationInFrames)) ? Number(composition.durationInFrames) : 1),
    },
    tracks: Array.isArray(source.tracks) ? source.tracks : [],
    clips: Array.isArray(source.clips) ? source.clips : [],
    componentRegistry: source.componentRegistry,
    componentBundle: source.componentBundle,
  };
}

function buildAssetBaseUrl(specPath: string): string {
  const base = withAuthToken(`/api/v1/video-editor/assets?spec=${encodeURIComponent(specPath)}`);
  return `${base}${base.includes("?") ? "&" : "?"}path=`;
}

function getUrlRegistryRef(): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return String(params.get("registry") || params.get("components") || "").trim();
}

function registryRefFromSpec(spec: RemotionTimelineSpec): { path: string; name: string } {
  const ref = spec.componentRegistry ?? spec.componentBundle;
  if (typeof ref === "string") return { path: ref.trim(), name: "default" };
  if (ref && typeof ref === "object") {
    const obj = ref as Record<string, unknown>;
    return {
      path: String(obj.path || obj.src || obj.url || obj.href || "").trim(),
      name: String(obj.name || obj.registry || "default").trim() || "default",
    };
  }
  return { path: "", name: "default" };
}

function buildRegistryUrl(specPath: string, assetBaseUrl: string, spec: RemotionTimelineSpec): { url: string; name: string } {
  const urlRegistryRef = getUrlRegistryRef();
  const specRegistryRef = registryRefFromSpec(spec);
  const path = urlRegistryRef || specRegistryRef.path;
  const name = urlRegistryRef ? "default" : specRegistryRef.name;
  if (!path) return { url: "", name };
  if (/^(?:https?:|blob:|data:|\/api\/)/i.test(path)) return { url: path, name };
  if (path.startsWith("/")) {
    return {
      url: withAuthToken(`/api/v1/video-editor/assets?spec=${encodeURIComponent(specPath)}&path=${encodeURIComponent(path.replace(/^\/+/, ""))}`),
      name,
    };
  }
  return { url: `${assetBaseUrl}${encodeURIComponent(path.replace(/^\/+/, ""))}`, name };
}

async function loadComponentRegistryScript(url: string): Promise<void> {
  const cleanUrl = String(url || "").trim();
  if (!cleanUrl || typeof document === "undefined") return;
  const existing = componentScriptLoads.get(cleanUrl);
  if (existing) return existing;
  const promise = new Promise<void>((resolve, reject) => {
    const previous = document.querySelector<HTMLScriptElement>(`script[data-video-editor-components="${CSS.escape(cleanUrl)}"]`);
    if (previous?.dataset.loaded === "true") {
      resolve();
      return;
    }
    const script = previous || document.createElement("script");
    script.dataset.videoEditorComponents = cleanUrl;
    script.async = true;
    script.src = cleanUrl;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => {
      componentScriptLoads.delete(cleanUrl);
      reject(new Error(`无法加载 Remotion 组件包：${cleanUrl}`));
    };
    if (!previous) document.head.appendChild(script);
  });
  componentScriptLoads.set(cleanUrl, promise);
  return promise;
}

function remotionSpecUrl(specPath: string): string {
  return `/api/v1/video-editor/spec?spec=${encodeURIComponent(specPath)}`;
}

function formatLoadError(code: string, message: string, requestUrl: string): string {
  if (code === "video_editor_spec_not_found") {
    return `找不到 Remotion spec 文件：${message.replace(/^video editor spec not found:\s*/i, "")}`;
  }
  if (code === "invalid_video_editor_spec") {
    return `Remotion spec 无效：${message}`;
  }
  const text = String(message || "").trim();
  if (/not found/i.test(text)) {
    return `Remotion spec 接口未找到：${requestUrl}。如果刚更新过代码，请重启一号同事后端/应用。`;
  }
  return text ? `${text}：${requestUrl}` : `无法加载 Remotion spec：${requestUrl}`;
}

function componentTone(component: string): string {
  if (component.includes("Caption") || component.includes("Title") || component.includes("Quality")) return "bg-sky-500";
  if (component.includes("Image") || component.includes("Window")) return "bg-emerald-500";
  if (component.includes("Transition") || component.includes("Firework")) return "bg-amber-500";
  if (component.includes("Audio")) return "bg-fuchsia-500";
  return "bg-slate-500";
}

function numberInputClass() {
  return "h-9 w-full rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-2 text-sm text-[var(--color-text-primary)] outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-400/20";
}

function panelClass(extra = "") {
  return classNames("min-w-0 rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]", extra);
}

export function RemotionEditorPage({
  isDark,
  specPath,
  headerExtra,
}: {
  isDark: boolean;
  specPath: string;
  headerExtra?: ReactNode;
}) {
  const [baseSpec, setBaseSpec] = useState<RemotionTimelineSpec | null>(null);
  const [spec, setSpec] = useState<RemotionTimelineSpec>(() => cloneSpec(emptySpec));
  const [loadStatus, setLoadStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [scope, setScope] = useState<BatchScope>("component");
  const [selectedComponent, setSelectedComponent] = useState("");
  const [selectedTrackId, setSelectedTrackId] = useState("");
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([]);
  const [frame, setFrame] = useState(0);
  const [player, setPlayer] = useState<PlayerRef | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(true);
  const [batchValues, setBatchValues] = useState<Record<NumericKey, string>>({ x: "", y: "", size: "", width: "", height: "" });
  const [copied, setCopied] = useState(false);
  const [externalComponents, setExternalComponents] = useState<ComponentRegistry>({});

  const composition = spec.composition;
  const normalizedSpecPath = String(specPath || "").trim();
  const assetBaseUrl = useMemo(() => (normalizedSpecPath ? buildAssetBaseUrl(normalizedSpecPath) : ""), [normalizedSpecPath]);
  const componentCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const clip of spec.clips) map.set(clip.component, (map.get(clip.component) || 0) + 1);
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [spec.clips]);

  const clipById = useMemo(() => new Map(spec.clips.map((clip) => [clip.id, clip])), [spec.clips]);
  const selectedClips = useMemo(() => selectedClipIds.map((id) => clipById.get(id)).filter(Boolean) as RemotionClip[], [clipById, selectedClipIds]);
  const targetClips = useMemo(() => {
    if (scope === "component") return spec.clips.filter((clip) => clip.component === selectedComponent);
    if (scope === "track") return spec.clips.filter((clip) => clip.trackId === selectedTrackId);
    return selectedClips;
  }, [scope, selectedClips, selectedComponent, selectedTrackId, spec.clips]);
  const targetIds = useMemo(() => new Set(targetClips.map((clip) => clip.id)), [targetClips]);
  const visibleClips = useMemo(() => {
    if (scope === "selection") return spec.clips;
    return targetClips;
  }, [scope, spec.clips, targetClips]);
  const activeClips = useMemo(() => spec.clips.filter((clip) => frame >= clip.from && frame < clip.from + clip.durationInFrames), [frame, spec.clips]);
  const activeClipIds = useMemo(() => new Set(activeClips.map((clip) => clip.id)), [activeClips]);
  const selectedSet = useMemo(() => new Set(selectedClipIds), [selectedClipIds]);

  useEffect(() => {
    let cancelled = false;
    const loadSpec = async () => {
      const targetSpecPath = String(specPath || "").trim();
      if (!targetSpecPath) {
        setBaseSpec(null);
        setSpec(cloneSpec(emptySpec));
        setExternalComponents({});
        setLoadStatus("error");
        setLoadError("URL 缺少 spec 参数");
        return;
      }
      setLoadStatus("loading");
      setLoadError("");
      const requestUrl = remotionSpecUrl(targetSpecPath);
      const response = await apiJson<RemotionTimelineSpec>(requestUrl);
      if (cancelled) return;
      if (!response.ok) {
        setBaseSpec(null);
        setSpec(cloneSpec(emptySpec));
        setSelectedClipIds([]);
        setExternalComponents({});
        setLoadStatus("error");
        setLoadError(formatLoadError(response.error.code, response.error.message, requestUrl));
        return;
      }
      const nextBase = normalizeSpec(response.result);
      const nextAssetBaseUrl = buildAssetBaseUrl(targetSpecPath);
      ensureVideoEditorHost(nextAssetBaseUrl);
      const registryRef = buildRegistryUrl(targetSpecPath, nextAssetBaseUrl, nextBase);
      let nextExternalComponents: ComponentRegistry = {};
      if (registryRef.url) {
        try {
          await loadComponentRegistryScript(registryRef.url);
        } catch (error) {
          if (cancelled) return;
          setBaseSpec(null);
          setSpec(cloneSpec(emptySpec));
          setSelectedClipIds([]);
          setExternalComponents({});
          setLoadStatus("error");
          setLoadError(error instanceof Error ? error.message : `无法加载 Remotion 组件包：${registryRef.url}`);
          return;
        }
        if (cancelled) return;
        nextExternalComponents = window.__OC_VIDEO_EDITOR_COMPONENTS__?.[registryRef.name] || window.__OC_VIDEO_EDITOR_COMPONENTS__?.default || {};
        if (Object.keys(nextExternalComponents).length === 0) {
          setBaseSpec(null);
          setSpec(cloneSpec(emptySpec));
          setSelectedClipIds([]);
          setExternalComponents({});
          setLoadStatus("error");
          setLoadError(`Remotion 组件包没有注册组件：${registryRef.url}`);
          return;
        }
      }
      const firstComponent = nextBase.clips[0]?.component || "";
      setBaseSpec(cloneSpec(nextBase));
      setSpec(cloneSpec(nextBase));
      setExternalComponents(nextExternalComponents);
      setSelectedComponent(firstComponent);
      setSelectedTrackId(nextBase.tracks[0]?.id || "");
      setSelectedClipIds(firstComponent ? nextBase.clips.filter((clip) => clip.component === firstComponent).slice(0, 1).map((clip) => clip.id) : []);
      setFrame(0);
      setLoadStatus("ready");
    };

    void loadSpec();
    return () => {
      cancelled = true;
    };
  }, [specPath]);

  useEffect(() => {
    if (componentCounts.length === 0) return;
    if (componentCounts.some(([component]) => component === selectedComponent)) return;
    setSelectedComponent(componentCounts[0][0]);
  }, [componentCounts, selectedComponent]);

  useEffect(() => {
    if (spec.tracks.length === 0) return;
    if (spec.tracks.some((track) => track.id === selectedTrackId)) return;
    setSelectedTrackId(spec.tracks[0].id);
  }, [selectedTrackId, spec.tracks]);

  useEffect(() => {
    if (!player) return undefined;
    setIsPlaying(player.isPlaying());
    setIsMuted(player.isMuted());
    const frameListener = (event: { detail: { frame: number } }) => setFrame(event.detail.frame);
    const playListener = () => setIsPlaying(true);
    const pauseListener = () => setIsPlaying(false);
    const endedListener = () => setIsPlaying(false);
    const muteListener = (event: { detail: { isMuted: boolean } }) => setIsMuted(event.detail.isMuted);
    player.addEventListener("frameupdate", frameListener);
    player.addEventListener("play", playListener);
    player.addEventListener("pause", pauseListener);
    player.addEventListener("ended", endedListener);
    player.addEventListener("mutechange", muteListener);
    return () => {
      player.removeEventListener("frameupdate", frameListener);
      player.removeEventListener("play", playListener);
      player.removeEventListener("pause", pauseListener);
      player.removeEventListener("ended", endedListener);
      player.removeEventListener("mutechange", muteListener);
    };
  }, [player]);

  const seekToFrame = (nextFrame: number) => {
    const clamped = Math.max(0, Math.min(composition.durationInFrames - 1, Math.floor(nextFrame)));
    setFrame(clamped);
    player?.seekTo(clamped);
  };

  const togglePlayback = () => {
    if (!player || loadStatus !== "ready") return;
    if (player.isPlaying()) {
      player.pause();
      setIsPlaying(false);
      return;
    }
    player.play();
    setIsPlaying(true);
  };

  const toggleMute = () => {
    if (!player) return;
    if (player.isMuted()) {
      player.unmute();
      setIsMuted(false);
      return;
    }
    player.mute();
    setIsMuted(true);
  };

  const updateClipProps = (ids: Set<string>, updater: (clip: RemotionClip, props: ClipProps) => ClipProps) => {
    setSpec((prev) => ({
      ...prev,
      clips: prev.clips.map((clip) => ids.has(clip.id) ? { ...clip, props: updater(clip, { ...(clip.props || {}) }) } : clip),
    }));
  };

  const applyBatch = (mode: "relative" | "absolute") => {
    if (targetIds.size === 0) return;
    const parsed = TRANSFORM_KEYS
      .map((key) => [key, batchValues[key].trim()] as const)
      .filter(([, value]) => value !== "")
      .map(([key, value]) => [key, Number(value)] as const)
      .filter(([, value]) => Number.isFinite(value));
    if (parsed.length === 0) return;

    updateClipProps(targetIds, (clip, props) => {
      const next = { ...props };
      for (const [key, value] of parsed) {
        next[key] = mode === "relative" ? getClipNumber(clip, key) + value : value;
      }
      return next;
    });
  };

  const setFocusedClipValue = (clipId: string, key: NumericKey, value: string) => {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return;
    updateClipProps(new Set([clipId]), (_clip, props) => ({ ...props, [key]: numberValue }));
  };

  const toggleClip = (clipId: string) => {
    setSelectedClipIds((prev) => prev.includes(clipId) ? prev.filter((id) => id !== clipId) : [...prev, clipId]);
  };

  const selectFirstClipInRange = (clips: RemotionClip[]) => {
    const first = clips[0];
    setSelectedClipIds(first ? [first.id] : []);
    if (first) seekToFrame(first.from);
  };

  const changeComponent = (component: string) => {
    setSelectedComponent(component);
    selectFirstClipInRange(spec.clips.filter((clip) => clip.component === component));
  };

  const changeTrack = (trackId: string) => {
    setSelectedTrackId(trackId);
    selectFirstClipInRange(spec.clips.filter((clip) => clip.trackId === trackId));
  };

  const resetSpec = () => {
    if (!baseSpec) return;
    setSpec(cloneSpec(baseSpec));
    setSelectedClipIds(baseSpec.clips.filter((clip) => clip.component === selectedComponent).slice(0, 1).map((clip) => clip.id));
    setBatchValues({ x: "", y: "", size: "", width: "", height: "" });
  };

  const copyJson = async () => {
    await navigator.clipboard?.writeText(JSON.stringify(spec, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className={classNames("flex h-full min-h-0 flex-col overflow-hidden", isDark ? "bg-[#101113]" : "bg-[#f5f6f8]")}>
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--glass-border-subtle)] bg-[var(--glass-bg)] px-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text-primary)]">Remotion 剪辑编辑器</div>
          <div className="text-xs text-[var(--color-text-muted)]">
            {composition.id} · {composition.width}x{composition.height} · {composition.durationInFrames} frames · {normalizedSpecPath || "未指定 spec"}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {headerExtra}
          <button type="button" onClick={copyJson} className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--glass-border-subtle)] px-3 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-black/[0.04] dark:hover:bg-white/[0.07]">
            {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
            {copied ? "已复制" : "复制 JSON"}
          </button>
          <button type="button" onClick={resetSpec} className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--glass-border-subtle)] px-3 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-black/[0.04] dark:hover:bg-white/[0.07]">
            <RestoreIcon size={15} />
            重置
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_320px] gap-3 overflow-hidden p-3 max-xl:grid-cols-[260px_minmax(0,1fr)] max-lg:grid-cols-1">
        <aside className={panelClass("flex min-h-0 flex-col overflow-hidden")}>
          <div className="border-b border-[var(--glass-border-subtle)] px-3 py-3">
            <div className="text-xs font-semibold uppercase text-[var(--color-text-muted)]">调整范围</div>
            <div className="mt-2 grid grid-cols-3 rounded-lg bg-black/[0.04] p-1 text-xs dark:bg-white/[0.06]">
              {(["component", "track", "selection"] as BatchScope[]).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setScope(item)}
                  className={classNames("rounded-md px-2 py-1.5 transition", scope === item ? "bg-[var(--color-bg-primary)] text-[var(--color-text-primary)] shadow-sm" : "text-[var(--color-text-muted)]")}
                >
                  {item === "component" ? "组件" : item === "track" ? "轨道" : "选中"}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-3">
            {scope === "component" ? (
              <label className="block text-xs font-medium text-[var(--color-text-secondary)]">
                Component
                <select value={selectedComponent} onChange={(event) => changeComponent(event.target.value)} className={classNames(numberInputClass(), "mt-1")}>
                  {componentCounts.map(([component, count]) => (
                    <option key={component} value={component}>{component} ({count})</option>
                  ))}
                </select>
              </label>
            ) : null}

            {scope === "track" ? (
              <label className="block text-xs font-medium text-[var(--color-text-secondary)]">
                Track
                <select value={selectedTrackId} onChange={(event) => changeTrack(event.target.value)} className={classNames(numberInputClass(), "mt-1")}>
                  {spec.tracks.map((track) => (
                    <option key={track.id} value={track.id}>{track.name || track.id}</option>
                  ))}
                </select>
              </label>
            ) : null}

            <div className="mt-4 flex items-center justify-between">
              <div className="text-xs font-semibold uppercase text-[var(--color-text-muted)]">Clips</div>
              <div className="rounded-full bg-sky-500/12 px-2 py-0.5 text-[10px] font-bold text-sky-600 dark:text-sky-300">{targetClips.length} 目标</div>
            </div>
            <div className="mt-2 space-y-1.5">
              {visibleClips.length === 0 ? (
                <div className="rounded-lg border border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-muted)]">当前范围没有 clip。</div>
              ) : visibleClips.map((clip) => {
                const selected = selectedSet.has(clip.id);
                const inTarget = targetIds.has(clip.id);
                return (
                  <button
                    key={clip.id}
                    type="button"
                    onClick={() => toggleClip(clip.id)}
                    className={classNames(
                      "flex w-full min-w-0 items-center gap-2 rounded-lg border px-2 py-2 text-left transition",
                      selected ? "border-sky-400 bg-sky-500/10" : inTarget ? "border-[var(--glass-border-subtle)] bg-black/[0.035] dark:bg-white/[0.045]" : "border-transparent hover:bg-black/[0.035] dark:hover:bg-white/[0.045]"
                    )}
                  >
                    <span className={classNames("h-2.5 w-2.5 shrink-0 rounded-full", componentTone(clip.component))} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-[var(--color-text-primary)]">{clip.id}</span>
                      <span className="block truncate text-[10px] text-[var(--color-text-muted)]">{clip.component}</span>
                    </span>
                    {activeClipIds.has(clip.id) ? <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-600 dark:text-emerald-300">LIVE</span> : null}
                  </button>
                );
              })}
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col gap-3">
          <section className={panelClass("flex min-h-0 flex-1 items-center justify-center overflow-hidden p-3")}>
            <div className="relative aspect-[480/1040] h-full max-h-full max-w-full overflow-hidden rounded-[18px] bg-black shadow-2xl">
              <Player
                ref={setPlayer}
                component={VideoEditorRuntime}
                inputProps={{ spec, assetBaseUrl, externalComponents }}
                durationInFrames={composition.durationInFrames}
                compositionWidth={composition.width}
                compositionHeight={composition.height}
                fps={composition.fps}
                controls={false}
                loop
                initiallyMuted={isMuted}
                initialFrame={frame}
                renderLoading={() => <div className="flex h-full items-center justify-center text-white/70">Loading preview</div>}
                style={{ width: "100%", height: "100%" }}
              />
              {loadStatus !== "ready" ? (
                <div className="absolute inset-0 flex items-center justify-center bg-black/72 px-8 text-center text-sm text-white/80">
                  {loadStatus === "loading" ? "正在加载团队剪辑文件..." : loadError}
                </div>
              ) : null}
            </div>
          </section>

          <section className={panelClass("shrink-0 overflow-hidden")}>
            <div className="flex items-center gap-3 border-b border-[var(--glass-border-subtle)] px-3 py-2">
              <button
                type="button"
                onClick={togglePlayback}
                disabled={loadStatus !== "ready"}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] transition hover:bg-black/[0.04] disabled:cursor-not-allowed disabled:opacity-45 dark:hover:bg-white/[0.07]"
                aria-label={isPlaying ? "暂停预览" : "播放预览"}
                title={isPlaying ? "暂停" : "播放"}
              >
                {isPlaying ? <PauseIcon size={16} /> : <PlayIcon size={16} />}
              </button>
              <button
                type="button"
                onClick={toggleMute}
                disabled={!player}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] transition hover:bg-black/[0.04] disabled:cursor-not-allowed disabled:opacity-45 dark:hover:bg-white/[0.07]"
                aria-label={isMuted ? "取消静音" : "静音预览"}
                title={isMuted ? "取消静音" : "静音"}
              >
                {isMuted ? <MutedIcon size={16} /> : <VolumeIcon size={16} />}
              </button>
              <input
                type="range"
                min={0}
                max={composition.durationInFrames - 1}
                value={frame}
                onChange={(event) => seekToFrame(Number(event.target.value))}
                className="min-w-0 flex-1"
              />
              <span className="w-24 text-right text-xs tabular-nums text-[var(--color-text-muted)]">{frameToTime(frame, composition.fps)}</span>
            </div>
            <div className="max-h-[210px] overflow-auto px-3 py-3">
              {spec.tracks.map((track) => (
                <div key={track.id} className="mb-3 grid grid-cols-[112px_minmax(0,1fr)] gap-3">
                  <div className="truncate text-xs font-medium text-[var(--color-text-secondary)]">{track.name || track.id}</div>
                  <div className="relative h-9 rounded-lg bg-black/[0.045] dark:bg-white/[0.06]">
                    <div className="absolute bottom-0 top-0 w-px bg-sky-400" style={{ left: `${(frame / composition.durationInFrames) * 100}%` }} />
                    {spec.clips.filter((clip) => clip.trackId === track.id).map((clip) => {
                      const left = (clip.from / composition.durationInFrames) * 100;
                      const width = (clip.durationInFrames / composition.durationInFrames) * 100;
                      return (
                        <button
                          key={clip.id}
                          type="button"
                          onClick={() => {
                            setSelectedClipIds([clip.id]);
                            seekToFrame(clip.from);
                          }}
                          className={classNames(
                            "absolute top-1 h-7 min-w-[8px] overflow-hidden rounded-md px-1 text-left text-[10px] font-semibold text-white shadow-sm",
                            componentTone(clip.component),
                            selectedSet.has(clip.id) && "ring-2 ring-white"
                          )}
                          style={{ left: `${left}%`, width: `${Math.max(width, 1.2)}%` }}
                          title={`${clip.id} · ${clip.component}`}
                        >
                          <span className="block truncate">{clip.id}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </main>

        <aside className={panelClass("flex min-h-0 flex-col overflow-hidden max-xl:col-span-2 max-lg:col-span-1")}>
          <div className="border-b border-[var(--glass-border-subtle)] px-3 py-3">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">批量参数</div>
            <div className="mt-1 text-xs text-[var(--color-text-muted)]">
              当前目标：{targetClips.length} 个 clip
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <div className="grid grid-cols-2 gap-2">
              {TRANSFORM_KEYS.map((key) => (
                <label key={key} className="text-xs font-medium uppercase text-[var(--color-text-muted)]">
                  {key}
                  <input
                    value={batchValues[key]}
                    onChange={(event) => setBatchValues((prev) => ({ ...prev, [key]: event.target.value }))}
                    inputMode="decimal"
                    placeholder={key === "size" ? "字号" : key}
                    className={classNames(numberInputClass(), "mt-1")}
                  />
                </label>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button type="button" onClick={() => applyBatch("relative")} className="h-10 rounded-lg bg-sky-500 px-3 text-sm font-semibold text-white transition hover:bg-sky-600">
                相对调整
              </button>
              <button type="button" onClick={() => applyBatch("absolute")} className="h-10 rounded-lg bg-slate-900 px-3 text-sm font-semibold text-white transition hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200">
                设为固定值
              </button>
            </div>

            <div className="mt-5 text-xs font-semibold uppercase text-[var(--color-text-muted)]">选中 Clip 参数</div>
            <div className="mt-2 space-y-2">
              {selectedClips.length === 0 ? (
                <div className="rounded-lg border border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-muted)]">从左侧或时间线选中 clip。</div>
              ) : selectedClips.map((clip) => (
                <div key={clip.id} className="rounded-lg border border-[var(--glass-border-subtle)] bg-black/[0.025] p-3 dark:bg-white/[0.035]">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{clip.id}</div>
                    <div className="truncate text-xs text-[var(--color-text-muted)]">{clip.component} · {clip.from}-{clip.from + clip.durationInFrames}</div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {TRANSFORM_KEYS.map((key) => (
                      <label key={key} className="text-[10px] font-medium uppercase text-[var(--color-text-muted)]">
                        {key}
                        <input
                          value={String(getClipNumber(clip, key))}
                          onChange={(event) => setFocusedClipValue(clip.id, key, event.target.value)}
                          className={classNames(numberInputClass(), "mt-1 h-8")}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-5 text-xs font-semibold uppercase text-[var(--color-text-muted)]">当前帧可见</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {activeClips.map((clip) => (
                <button
                  key={clip.id}
                  type="button"
                  onClick={() => setSelectedClipIds([clip.id])}
                  className={classNames("rounded-full px-2 py-1 text-[10px] font-semibold text-white", componentTone(clip.component))}
                >
                  {clip.id}
                </button>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
