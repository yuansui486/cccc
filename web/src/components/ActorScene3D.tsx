import { Suspense, useMemo, useRef, useState, useEffect, useCallback, type ComponentProps } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useTranslation } from "react-i18next";
import { ActorCharacter } from "./ActorCharacter";
import { SemanticMap } from "./SemanticMap";
import { PanoramaRoom } from "./PanoramaRoom";
import { MCGround, InstancedBeds, type BedInstance } from "./MCFurniture";
import { useCharacterAnimation } from "../hooks/useCharacterAnimation";
import { useFirstPersonFollow } from "../hooks/useFirstPersonFollow";
import type { AgentState, Actor, Task, GroupContext } from "../types";
import { buildSemanticMapState, SEMANTIC_MAP_SCENE_EXTENT } from "../utils/panoramaSemanticMap";
import * as THREE from "three";

interface ActorScene3DProps {
  agents: AgentState[];
  actors?: Actor[];
  tasks?: Task[];
  tasksSummary?: GroupContext["tasks_summary"];
  projectStatus?: string | null;
  isDark: boolean;
  isSmallScreen?: boolean;
  groupId?: string;
  className?: string;
}

function agentActiveTaskId(agent: AgentState): string {
  return String(agent.hot?.active_task_id || "").trim();
}

function agentFocus(agent: AgentState): string {
  return String(agent.hot?.focus || "").trim();
}

function agentBlocker(agent: AgentState): string | undefined {
  const blockers = Array.isArray(agent.hot?.blockers) ? agent.hot.blockers : [];
  return blockers.length > 0 ? String(blockers[0] || "").trim() || undefined : undefined;
}

function taskLabel(task: Task): string {
  return String(task.title || task.id || "").trim();
}

// ── Scene ──

interface SceneProps {
  agents: AgentState[];
  actors?: Actor[];
  tasks?: Task[];
  tasksSummary?: GroupContext["tasks_summary"];
  projectStatus?: string | null;
  isDark: boolean;
  isSmallScreen?: boolean;
  groupId?: string;
  camZ: number;
  followTarget: string | null;
  onCharacterClick: (agentId: string) => void;
}

function Scene({ agents, actors, tasks, tasksSummary: _tasksSummary, projectStatus, isDark, isSmallScreen = false, groupId: _groupId, camZ, followTarget, onCharacterClick }: SceneProps) {
  const characterRefs = useRef<Map<string, THREE.Group>>(new Map());

  const actorMap = useMemo(() => {
    const m = new Map<string, Actor>();
    for (const a of actors || []) m.set(a.id, a);
    return m;
  }, [actors]);

  const taskMap = useMemo(() => {
    const m = new Map<string, { idx: number; name: string; status?: string | null }>();
    if (!tasks || tasks.length === 0) return m;
    for (let i = 0; i < tasks.length; i++) {
      m.set(tasks[i].id, { idx: i, name: taskLabel(tasks[i]), status: tasks[i].status });
    }
    return m;
  }, [tasks]);

  const semanticMap = useMemo(
    () => buildSemanticMapState(agents, actorMap, tasks),
    [agents, actorMap, tasks],
  );
  const layout = semanticMap.layout;
  const buildTargetMap = semanticMap.buildTargetMap;

  const taskStatusMap = useMemo(() => {
    const m = new Map<string, string>();
    if (!tasks) return m;
    for (const t of tasks) {
      if (t.status) m.set(t.id, t.status);
    }
    return m;
  }, [tasks]);

  useCharacterAnimation({
    agents, actorMap, layout, buildTargetMap, characterRefs, taskStatusMap,
    staticMode: true,
  });

  useFirstPersonFollow({ targetId: followTarget, characterRefs });

  const bedInstances: BedInstance[] = semanticMap.bedInstances;

  return (
    <>
      <ambientLight intensity={isDark ? 0.42 : 0.72} />
      <directionalLight
        position={[4.5, 7.5, 4]}
        intensity={isDark ? 0.85 : 1}
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
      />
      <directionalLight
        position={[-4, 4.5, -2.5]}
        intensity={isDark ? 0.2 : 0.3}
      />
      <pointLight
        position={[0, 4.25, -2.8]}
        intensity={isDark ? 1 : 0.8}
        distance={18}
        decay={2}
        color={isDark ? "#67e8f9" : "#60a5fa"}
      />

      <MCGround />

      <PanoramaRoom zones={semanticMap.zones} />

      <SemanticMap
        zones={semanticMap.zones}
        isDark={isDark}
        compact={isSmallScreen}
        projectStatus={projectStatus}
      />

      <InstancedBeds beds={bedInstances} />

      {agents.map((agent) => {
        const actor = actorMap.get(agent.id);
        const running = actor?.running !== false && actor?.enabled !== false;
        const item = layout.get(agent.id);
        const activeTaskId = agentActiveTaskId(agent);
        const taskEntry = activeTaskId ? taskMap.get(activeTaskId) : undefined;
        return (
          <ActorCharacter
            key={agent.id}
            ref={(el: THREE.Group | null) => {
              if (el) characterRefs.current.set(agent.id, el);
              else characterRefs.current.delete(agent.id);
            }}
            agent={agent}
            position={item?.charPos || [0, 0, 0]}
            rotationY={item?.charRotY}
            isDark={isDark}
            role={actor?.role}
            runtime={actor?.runtime}
            title={actor?.title}
            isRunning={running}
            idleSeconds={actor?.idle_seconds}
            activeTaskName={taskEntry?.name.replace(/^T\d+:\s*/, "")}
            taskStatus={taskEntry?.status || undefined}
            focus={agentFocus(agent) || undefined}
            blockerText={agentBlocker(agent)}
            onCharacterClick={() => onCharacterClick(agent.id)}
          />
        );
      })}

      <OrbitControls
        makeDefault
        enableDamping={false}
        enabled={followTarget === null}
        enablePan={true}
        enableZoom={true}
        enableRotate={true}
        minDistance={2}
        maxDistance={camZ * 3}
        maxPolarAngle={Math.PI / 2.05}
        target={[0, 0.5, 0]}
        mouseButtons={{ LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }}
        screenSpacePanning={true}
      />
    </>
  );
}

// ── WebGPU / WebGL renderer selection ──

type RenderMode = "loading" | "webgpu" | "webgl";

type WebGPUCanvas = HTMLCanvasElement | OffscreenCanvas;

type WebGPURendererLike = THREE.WebGLRenderer & {
  init: () => Promise<void>;
};

interface WebGPUModuleLike {
  WebGPURenderer: new (params: {
    canvas: HTMLCanvasElement;
    antialias: boolean;
    alpha: boolean;
  }) => WebGPURendererLike;
}

type CanvasGlFactory = (canvas: WebGPUCanvas) => THREE.WebGLRenderer;
type HudStatusKey = "blocked" | "active" | "thinking" | "waiting";

function buildHudSummary(agents: AgentState[], actors?: Actor[]) {
  const actorMap = new Map((actors || []).map((actor) => [actor.id, actor]));
  const totalAgents = agents.length;
  let activeAgents = 0;
  let blockedAgents = 0;
  let waitingAgents = 0;
  const items: Array<{
    id: string;
    title: string;
    statusKey: HudStatusKey;
    focus: string;
    next: string;
    blocked: boolean;
    active: boolean;
  }> = [];

  for (const agent of agents) {
    const actor = actorMap.get(agent.id);
    const hasTask = !!String(agent.hot?.active_task_id || "").trim();
    const blockers = Array.isArray(agent.hot?.blockers) ? agent.hot.blockers : [];
    const focus = String(agent.hot?.focus || "").trim();
    const next = String(agent.hot?.next_action || "").trim();
    const statusKey: HudStatusKey =
      blockers.length > 0 ? "blocked" : hasTask ? "active" : focus || next ? "thinking" : "waiting";

    if (hasTask) activeAgents += 1;
    if (blockers.length > 0) blockedAgents += 1;
    if (!hasTask && blockers.length === 0 && !focus && !next) waitingAgents += 1;

    items.push({
      id: agent.id,
      title: actor?.title || agent.id,
      statusKey,
      focus: focus.slice(0, 28),
      next: next.slice(0, 28),
      blocked: blockers.length > 0,
      active: hasTask,
    });
  }

  items.sort((a, b) => Number(b.blocked) - Number(a.blocked) || Number(b.active) - Number(a.active) || a.title.localeCompare(b.title));
  return { totalAgents, activeAgents, blockedAgents, waitingAgents, items: items.slice(0, 6) };
}


export function ActorScene3D({ agents, actors, tasks, tasksSummary, projectStatus, isDark, isSmallScreen = false, groupId, className }: ActorScene3DProps) {
  const { t } = useTranslation("layout");
  const [followTarget, setFollowTarget] = useState<string | null>(null);
  const [hudExpanded, setHudExpanded] = useState(false);

  const handleCharacterClick = useCallback((agentId: string) => {
    setFollowTarget((prev) => (prev === agentId ? null : agentId));
  }, []);

  useEffect(() => {
    if (isSmallScreen) {
      setHudExpanded(false);
    }
  }, [isSmallScreen]);

  const camZ = useMemo(() => {
    return Math.max(5, SEMANTIC_MAP_SCENE_EXTENT * 2 + 1);
  }, []);

  const hudSummary = useMemo(() => buildHudSummary(agents, actors), [agents, actors]);
  const hudBadges = useMemo(
    () => [
      { key: "active", label: t("panoramaHudActive", "Active"), value: hudSummary.activeAgents, tint: "text-sky-600 bg-sky-500/10 border-sky-500/20 dark:text-sky-300" },
      { key: "blocked", label: t("panoramaHudBlocked", "Blocked"), value: hudSummary.blockedAgents, tint: "text-rose-600 bg-rose-500/10 border-rose-500/20 dark:text-rose-300" },
      { key: "waiting", label: t("panoramaHudWaiting", "Waiting"), value: hudSummary.waitingAgents, tint: "text-emerald-700 bg-emerald-500/10 border-emerald-500/20 dark:text-emerald-300" },
    ],
    [hudSummary.activeAgents, hudSummary.blockedAgents, hudSummary.waitingAgents, t]
  );
  const visibleHudItems = hudSummary.items.slice(0, isSmallScreen ? 4 : 5);

  // Phase 1: detect WebGPU + dynamically load three/webgpu module
  const [renderMode, setRenderMode] = useState<RenderMode>("loading");
  const gpuModRef = useRef<WebGPUModuleLike | null>(null);

  useEffect(() => {
    // WebGPU is opt-in via ?webgpu URL param (R3F v8 has limited WebGPU compat)
    const wantGPU =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).has("webgpu");

    if (!wantGPU || !navigator.gpu) {
      setRenderMode("webgl");
      return;
    }

    let cancelled = false;
    import("three/webgpu")
      .then((mod) => {
        if (cancelled) return;
        gpuModRef.current = mod as unknown as WebGPUModuleLike;
        setRenderMode("webgpu");
      })
      .catch(() => {
        if (cancelled) return;
        setRenderMode("webgl");
      });
    return () => { cancelled = true; };
  }, []);

  // Stable gl factory for WebGPU -- avoids re-creation on every render.
  // Uses a render-guard so R3F can keep frameloop="always" (required for
  // OrbitControls) while WebGPURenderer.init() resolves asynchronously.
  const createWebGPURenderer = useCallback<CanvasGlFactory>((canvas) => {
    const GPU = gpuModRef.current;
    if (!GPU) {
      throw new Error("WebGPU module not loaded");
    }
    const renderer = new GPU.WebGPURenderer({
      canvas: canvas as HTMLCanvasElement,
      antialias: true,
      alpha: false,
    });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;

    // XR stub -- prevents R3F v8 "xr.addEventListener is not a function" error
    if (!renderer.xr) {
      const xrStub = {
        addEventListener: () => {},
        removeEventListener: () => {},
        getSession: () => null,
        setSession: () => Promise.resolve(),
        enabled: false,
        isPresenting: false,
        setReferenceSpaceType: () => {},
        getReferenceSpace: () => null,
        getCamera: () => new THREE.PerspectiveCamera(),
        setAnimationLoop: () => {},
        dispose: () => {},
      } as unknown as THREE.WebXRManager;
      Object.assign(renderer, { xr: xrStub });
    }

    // Guard: R3F calls gl.render() every frame via its rAF loop.
    // WebGPURenderer.render() throws if called before init() resolves,
    // so we patch it with a no-op until the backend is ready.
    const nativeRender = renderer.render.bind(renderer);
    let ready = false;
    renderer.render = (scene: THREE.Scene, camera: THREE.Camera) => {
      if (ready) nativeRender(scene, camera);
    };

    renderer.init()
      .then(() => {
        console.warn("[ActorScene3D] WebGPU renderer initialized");
        ready = true;
      })
      .catch((err: unknown) => {
        console.warn("[ActorScene3D] WebGPU init failed, reloading with WebGL:", err);
        setRenderMode("webgl");
      });

    return renderer;
  }, []);

  // Placeholder while detecting WebGPU
  if (renderMode === "loading") {
    return (
      <div
        className={className}
        style={{
          minHeight: 280,
          borderRadius: 12,
          background: isDark ? "#0f172a" : "#dbe4ee",
        }}
      />
    );
  }

  const cameraConfig = {
    position: [camZ * 0.55, camZ * 0.42, camZ * 0.88] as [number, number, number],
    fov: 42,
    near: 0.1,
    far: 100,
  };

  const canvasStyle = {
    borderRadius: 12,
    background: isDark ? "#0f172a" : "#dbe4ee",
  };

  const glProp: ComponentProps<typeof Canvas>["gl"] = renderMode === "webgpu"
    ? createWebGPURenderer
    : { antialias: true, alpha: false };

  return (
    <div className={className} style={{ minHeight: 280, position: "relative" }}>
      <div
        className={`pointer-events-none absolute z-[2] ${
          isSmallScreen
            ? "inset-x-3 bottom-3"
            : "right-4 top-4 w-[24rem] max-w-[calc(100%-2rem)]"
        }`}
      >
        <section
          aria-label={t("panoramaHudTitle", "Agent State")}
          className={`pointer-events-auto w-full rounded-2xl border px-3 py-3 shadow-xl backdrop-blur-md sm:px-4 ${
            isDark
              ? "border-slate-700/60 bg-slate-950/82 text-slate-100 shadow-black/40"
              : "border-slate-300/70 bg-white/90 text-slate-900 shadow-slate-300/40"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold sm:text-base">
                {t("panoramaHudTitle", "Agent State")}
              </div>
              {hudExpanded ? (
                <div className={`mt-1 text-[11px] sm:text-xs ${isDark ? "text-slate-100" : "text-slate-700"}`}>
                  {t("panoramaHudHint", "Quick snapshot of who is active, blocked, or waiting in the panorama scene.")}
                </div>
              ) : null}
            </div>
            <button
              type="button"
              aria-expanded={hudExpanded}
              onClick={() => setHudExpanded((prev) => !prev)}
              className={`inline-flex shrink-0 items-center rounded-full border px-3 py-1 text-[11px] font-semibold transition-colors sm:text-xs ${
                isDark
                  ? "border-slate-600/80 bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]"
                  : "border-slate-300 bg-white/80 text-slate-700 hover:bg-white"
              }`}
            >
              {hudExpanded
                ? t("panoramaHudCollapse", "Hide details")
                : t("panoramaHudExpand", "Show details")}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {hudBadges.map((badge) => (
              <span
                key={badge.key}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold sm:text-xs ${badge.tint}`}
              >
                <span>{badge.label}</span>
                <span>{badge.value}</span>
              </span>
            ))}
          </div>

          {hudExpanded ? (
            <div className="mt-3 max-h-[min(48vh,24rem)] overflow-y-auto pr-1">
              <div className="grid grid-cols-1 gap-2">
                {visibleHudItems.map((item) => {
                  const statusTone =
                    item.statusKey === "blocked"
                      ? "text-rose-600 bg-rose-500/10 border-rose-500/20 dark:text-rose-300"
                      : item.statusKey === "active"
                        ? "text-sky-600 bg-sky-500/10 border-sky-500/20 dark:text-sky-300"
                        : item.statusKey === "thinking"
                          ? "text-violet-600 bg-violet-500/10 border-violet-500/20 dark:text-violet-300"
                          : "text-emerald-700 bg-emerald-500/10 border-emerald-500/20 dark:text-emerald-300";
                  const statusLabel =
                    item.statusKey === "blocked"
                      ? t("panoramaHudBlocked", "Blocked")
                      : item.statusKey === "active"
                        ? t("panoramaHudActive", "Active")
                        : item.statusKey === "thinking"
                          ? t("panoramaHudThinking", "Thinking")
                          : t("panoramaHudWaiting", "Waiting");

                  return (
                    <article
                      key={item.id}
                      className={`rounded-xl border px-3 py-2.5 ${
                        isDark ? "border-slate-800/80 bg-white/[0.06]" : "border-slate-200 bg-slate-50/80"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold">{item.title}</div>
                          <div className={`mt-1 text-[11px] ${isDark ? "text-slate-200" : "text-slate-600"}`}>ID: {item.id}</div>
                        </div>
                        <span className={`inline-flex shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold sm:text-[11px] ${statusTone}`}>
                          {statusLabel}
                        </span>
                      </div>
                      <div className={`mt-3 space-y-1.5 text-[11px] sm:text-xs ${isDark ? "text-slate-100" : "text-slate-800"}`}>
                        <div className="line-clamp-2 break-words">
                          <span className={isDark ? "text-slate-300" : "text-slate-600"}>{t("panoramaHudFocus", "Focus")}:</span>{" "}
                          {item.focus || t("panoramaHudNoFocus", "None yet")}
                        </div>
                        <div className="line-clamp-2 break-words">
                          <span className={isDark ? "text-slate-300" : "text-slate-600"}>{t("panoramaHudNext", "Next")}:</span>{" "}
                          {item.next || t("panoramaHudNoNextAction", "None yet")}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}
        </section>
      </div>
      <Canvas
        key={renderMode}
        frameloop="always"
        shadows={{ type: THREE.PCFShadowMap }}
        camera={cameraConfig}
        style={canvasStyle}
        gl={glProp}
        onPointerMissed={() => setFollowTarget(null)}
      >
        <Suspense fallback={null}>
          <Scene agents={agents} actors={actors} tasks={tasks} tasksSummary={tasksSummary} projectStatus={projectStatus} isDark={isDark} isSmallScreen={isSmallScreen} groupId={groupId} camZ={camZ} followTarget={followTarget} onCharacterClick={handleCharacterClick} />
        </Suspense>
      </Canvas>
    </div>
  );
}
