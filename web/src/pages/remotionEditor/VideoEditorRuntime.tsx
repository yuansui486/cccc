import React from "react";
import * as ReactJsxRuntime from "react/jsx-runtime";
import { Gif } from "@remotion/gif";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile as remotionStaticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type Clip = {
  id: string;
  trackId: string;
  component: string;
  props?: Record<string, unknown>;
  from: number;
  durationInFrames: number;
};

export type TimelineSpec = {
  composition?: {
    id?: string;
    fps?: number;
    width?: number;
    height?: number;
    durationInFrames?: number;
  };
  tracks?: Array<{
    id: string;
    name?: string;
    type?: string;
    enabled?: boolean;
  }>;
  clips?: Clip[];
  audio?: {
    tracks?: unknown[];
  };
};

type VideoEditorComponentProps = {
  clip: Clip;
  assetBaseUrl?: string;
};

export type ComponentRegistry = Record<string, React.ComponentType<VideoEditorComponentProps>>;

const emptyTimelineSpec: TimelineSpec = {
  clips: [],
};

const viteStaticBase = String(import.meta.env.BASE_URL || "/").replace(/\/$/, "");

if (typeof window !== "undefined" && viteStaticBase) {
  window.remotion_staticBase = viteStaticBase;
}

declare global {
  interface Window {
    __OC_VIDEO_EDITOR_HOST__?: VideoEditorHost;
    __OC_VIDEO_EDITOR_COMPONENTS__?: Record<string, ComponentRegistry>;
  }
}

type VideoEditorHost = {
  React: typeof React;
  jsxRuntime: typeof ReactJsxRuntime;
  remotion: {
    AbsoluteFill: typeof AbsoluteFill;
    Audio: typeof Audio;
    Easing: typeof Easing;
    Img: typeof Img;
    OffthreadVideo: typeof OffthreadVideo;
    Sequence: typeof Sequence;
    interpolate: typeof interpolate;
    spring: typeof spring;
    staticFile: typeof remotionHostStaticFile;
    useCurrentFrame: typeof useCurrentFrame;
    useVideoConfig: typeof useVideoConfig;
  };
  gif: {
    Gif: typeof Gif;
  };
  jsonRenderRemotion: {
    ClipWrapper: React.FC<{ clip: Clip; children: React.ReactNode }>;
    standardComponents: ComponentRegistry;
  };
  assetBaseUrl: string;
  resolveAsset: (src: string) => string;
};

function resolveHostAsset(src: string): string {
  const raw = String(src || "").trim();
  if (!raw) return "";
  if (/^(?:https?:|data:|blob:|\/)/i.test(raw)) return raw;
  const clean = raw.replace(/^\/+/, "");
  const assetBaseUrl = typeof window !== "undefined" ? String(window.__OC_VIDEO_EDITOR_HOST__?.assetBaseUrl || "") : "";
  if (assetBaseUrl) return `${assetBaseUrl}${encodeURIComponent(clean)}`;
  return remotionStaticFile(clean);
}

function remotionHostStaticFile(src: string): string {
  return resolveHostAsset(src);
}

const HostClipWrapper: VideoEditorHost["jsonRenderRemotion"]["ClipWrapper"] = ({ children }) => {
  return <AbsoluteFill>{children}</AbsoluteFill>;
};

export function ensureVideoEditorHost(assetBaseUrl = ""): void {
  if (typeof window === "undefined") return;
  const previous = window.__OC_VIDEO_EDITOR_HOST__;
  window.__OC_VIDEO_EDITOR_HOST__ = {
    React,
    jsxRuntime: ReactJsxRuntime,
    remotion: {
      AbsoluteFill,
      Audio,
      Easing,
      Img,
      OffthreadVideo,
      Sequence,
      interpolate,
      spring,
      staticFile: remotionHostStaticFile,
      useCurrentFrame,
      useVideoConfig,
    },
    gif: { Gif },
    jsonRenderRemotion: {
      ClipWrapper: HostClipWrapper,
      standardComponents: previous?.jsonRenderRemotion.standardComponents || {},
    },
    assetBaseUrl,
    resolveAsset: resolveHostAsset,
  };
  window.__OC_VIDEO_EDITOR_COMPONENTS__ = window.__OC_VIDEO_EDITOR_COMPONENTS__ || {};
}

const TimelineRenderer: React.FC<{
  spec: TimelineSpec;
  components?: ComponentRegistry;
  assetBaseUrl?: string;
}> = ({ spec, components = {}, assetBaseUrl }) => {
  const clips = Array.isArray(spec.clips) ? spec.clips : [];

  return (
    <>
      {clips.map((clip) => {
        const Component = components[clip.component];
        if (!Component || clip.durationInFrames <= 0) return null;
        return (
          <Sequence
            key={clip.id}
            from={clip.from}
            durationInFrames={clip.durationInFrames}
            name={`${clip.trackId}:${clip.id}:${clip.component}`}
          >
            <Component clip={clip} assetBaseUrl={assetBaseUrl} />
          </Sequence>
        );
      })}
    </>
  );
};

export const VideoEditorRuntime: React.FC<{
  spec?: TimelineSpec;
  assetBaseUrl?: string;
  externalComponents?: ComponentRegistry;
}> = ({ spec = emptyTimelineSpec, assetBaseUrl, externalComponents }) => {
  ensureVideoEditorHost(assetBaseUrl);
  return (
    <AbsoluteFill style={{ background: "#050505", color: "#fff", overflow: "hidden" }}>
      <TimelineRenderer spec={spec} components={externalComponents} assetBaseUrl={assetBaseUrl} />
    </AbsoluteFill>
  );
};
