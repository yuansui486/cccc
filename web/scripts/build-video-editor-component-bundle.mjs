import esbuild from "esbuild";
import {existsSync, mkdirSync, readFileSync, readdirSync} from "node:fs";
import {dirname, isAbsolute, join, relative, resolve, sep} from "node:path";

const specPathArg = process.argv[2];
const outPathArg = process.argv[3];

if (!specPathArg || !outPathArg) {
  console.error("Usage: node scripts/build-video-editor-component-bundle.mjs <spec.json|spec-dir> <out.js>");
  process.exit(1);
}

const root = process.cwd();
const specPathOrDir = isAbsolute(specPathArg) ? specPathArg : resolve(root, specPathArg);
const specDir = specPathOrDir.endsWith(".json") ? dirname(specPathOrDir) : specPathOrDir;
const componentsDir = resolve(specDir, "components");
const outPath = isAbsolute(outPathArg) ? outPathArg : resolve(root, outPathArg);

const walk = (dir) => {
  const entries = [];
  for (const entry of readdirSync(dir, {withFileTypes: true})) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      entries.push(...walk(path));
    } else if (entry.isFile() && entry.name.endsWith(".tsx")) {
      entries.push(path);
    }
  }
  return entries;
};

const isComponentFile = (file) => {
  const componentName = file.split(sep).pop().replace(/\.tsx$/, "");
  const source = readFileSync(file, "utf8");
  const namedExport = new RegExp(`export\\s+(?:const|function)\\s+${componentName}\\b`);
  const defaultExport = new RegExp(`export\\s+default\\s+function\\s+${componentName}\\b`);
  return namedExport.test(source) || defaultExport.test(source);
};

const componentFiles = existsSync(componentsDir) ? walk(componentsDir).filter(isComponentFile) : [];
const styleFiles = existsSync(componentsDir) ? walk(componentsDir).filter((file) => file.split(sep).pop() === "styles.ts") : [];

if (componentFiles.length === 0) {
  throw new Error(`No component files found under ${componentsDir}`);
}

const imports = [];
const registrations = [];
const styleModules = [];

componentFiles.forEach((file, index) => {
  const componentName = file.split(sep).pop().replace(/\.tsx$/, "");
  const source = readFileSync(file, "utf8");
  const namespace = `ComponentModule${index}`;
  imports.push(`import * as ${namespace} from ${JSON.stringify(file)};`);
  const hasDefault = /export\s+default\b/.test(source);
  const hasAliases = /export\s+const\s+componentAliases\b/.test(source);
  const hasAliasMeta = /export\s+const\s+componentAliasMeta\b/.test(source);
  const hasComponentMeta = /export\s+const\s+componentMeta\b/.test(source);
  const hasMetadata = /export\s+const\s+metadata\b/.test(source);
  const componentExpression = hasDefault ? `${namespace}.default || ${namespace}.${componentName}` : `${namespace}.${componentName}`;
  const aliasesExpression = hasAliases ? `${namespace}.componentAliases` : "{}";
  const aliasMetaExpression = hasAliasMeta ? `${namespace}.componentAliasMeta` : "{}";
  const metaExpression = [
    hasComponentMeta ? `${namespace}.componentMeta` : "",
    hasMetadata ? `${namespace}.metadata` : "",
    "{}",
  ].filter(Boolean).join(" || ");
  registrations.push(`
{
  const component = ${componentExpression};
  if (component) {
    components[${JSON.stringify(componentName)}] = component;
    pushManifest(${JSON.stringify(componentName)}, ${metaExpression});
  }
  const aliases = ${aliasesExpression};
  const aliasMeta = ${aliasMetaExpression};
  for (const [aliasName, aliasComponent] of Object.entries(aliases)) {
    if (aliasName && aliasComponent) {
      components[aliasName] = aliasComponent;
      pushManifest(aliasName, aliasMeta[aliasName]);
    }
  }
}
`);
});

styleFiles.forEach((stylesPath, index) => {
  const stylesSource = readFileSync(stylesPath, "utf8");
  const styleCandidates = [
    ["componentStyles", /export\s+const\s+componentStyles\b/.test(stylesSource)],
    ["officeLinkStyles", /export\s+const\s+officeLinkStyles\b/.test(stylesSource)],
    ["styles", /export\s+const\s+styles\b/.test(stylesSource)],
    ["default", /export\s+default\b/.test(stylesSource)],
  ].filter(([, exists]) => exists).map(([name]) => name);
  const moduleName = `StylesModule${index}`;
  imports.push(`import * as ${moduleName} from ${JSON.stringify(stylesPath)};`);
  styleModules.push({moduleName, styleCandidates});
});

const virtualEntry = `
import {standardComponents} from "@json-render/remotion";
${imports.join("\n")}

const components = {...standardComponents};
const manifest = [];
const manifestNames = new Set();
const styleModules = [
${styleModules.map(({moduleName, styleCandidates}) => `  {module: ${moduleName}, candidates: ${JSON.stringify(styleCandidates)}}`).join(",\n")}
];
const pushManifest = (name, runtimeMeta = {}) => {
  if (!name || manifestNames.has(name)) return;
  const meta = runtimeMeta && typeof runtimeMeta === "object" ? runtimeMeta : {};
  manifest.push({name, ...meta});
  manifestNames.add(name);
};

${registrations.join("\n")}

const styles =
  styleModules
    .flatMap((item) => item.candidates.map((key) => item.module[key]))
    .filter((value) => typeof value === "string")
    .join("\\n");

if (typeof window !== "undefined") {
  window.__OC_VIDEO_EDITOR_COMPONENTS__ = window.__OC_VIDEO_EDITOR_COMPONENTS__ || {};
  window.__OC_VIDEO_EDITOR_COMPONENTS__.default = components;
  window.__OC_VIDEO_EDITOR_COMPONENT_METADATA__ = window.__OC_VIDEO_EDITOR_COMPONENT_METADATA__ || {};
  window.__OC_VIDEO_EDITOR_COMPONENT_METADATA__.default = {
    rootClassName: "canvas",
    styles,
    manifest,
    componentFiles: ${JSON.stringify(componentFiles.map((file) => relative(specDir, file).replaceAll(sep, "/")))},
  };
  window.dispatchEvent(new CustomEvent("oc-video-editor-registry-ready", {
    detail: {
      name: "default",
      components: Object.keys(components),
      metadata: window.__OC_VIDEO_EDITOR_COMPONENT_METADATA__.default,
    },
  }));
}
`;

const virtualModules = {
  react: `
const host = window.__OC_VIDEO_EDITOR_HOST__;
if (!host) throw new Error("OneColleague video editor host is not initialized");
const React = host.React;
export default React;
export const Fragment = React.Fragment;
export const createElement = React.createElement.bind(React);
export const memo = React.memo.bind(React);
export const useCallback = React.useCallback.bind(React);
export const useMemo = React.useMemo.bind(React);
export const useEffect = React.useEffect.bind(React);
export const useRef = React.useRef.bind(React);
export const useState = React.useState.bind(React);
`,
  "react/jsx-runtime": `
const runtime = window.__OC_VIDEO_EDITOR_HOST__.jsxRuntime;
export const Fragment = runtime.Fragment;
export const jsx = runtime.jsx;
export const jsxs = runtime.jsxs;
export const jsxDEV = runtime.jsxDEV || runtime.jsx;
`,
  remotion: `
const remotion = window.__OC_VIDEO_EDITOR_HOST__.remotion;
export const AbsoluteFill = remotion.AbsoluteFill;
export const Audio = remotion.Audio;
export const Easing = remotion.Easing;
export const Img = remotion.Img;
export const OffthreadVideo = remotion.OffthreadVideo;
export const Sequence = remotion.Sequence;
export const interpolate = remotion.interpolate;
export const spring = remotion.spring;
export const staticFile = remotion.staticFile;
export const useCurrentFrame = remotion.useCurrentFrame;
export const useVideoConfig = remotion.useVideoConfig;
export const Composition = () => null;
export const registerRoot = () => undefined;
`,
  "@remotion/gif": `
export const Gif = window.__OC_VIDEO_EDITOR_HOST__.gif.Gif;
`,
  "@json-render/remotion": `
const jsonRenderRemotion = window.__OC_VIDEO_EDITOR_HOST__.jsonRenderRemotion;
export const ClipWrapper = jsonRenderRemotion.ClipWrapper;
export const standardComponents = jsonRenderRemotion.standardComponents;
`,
};

const hostShimPlugin = {
  name: "onecolleague-video-editor-host-shims",
  setup(build) {
    const filter = /^(react|react\/jsx-runtime|remotion|@remotion\/gif|@json-render\/remotion)$/;
    build.onResolve({filter}, (args) => ({
      path: args.path,
      namespace: "onecolleague-host-shim",
    }));
    build.onLoad({filter: /.*/, namespace: "onecolleague-host-shim"}, (args) => ({
      contents: virtualModules[args.path],
      loader: "js",
    }));
  },
};

const virtualEntryPlugin = {
  name: "dynamic-video-components-entry",
  setup(build) {
    build.onResolve({filter: /^dynamic-video-components-entry$/}, () => ({
      path: "dynamic-video-components-entry",
      namespace: "dynamic-video-components",
    }));
    build.onLoad({filter: /.*/, namespace: "dynamic-video-components"}, () => ({
      contents: virtualEntry,
      loader: "tsx",
      resolveDir: componentsDir,
    }));
  },
};

mkdirSync(dirname(outPath), {recursive: true});

await esbuild.build({
  entryPoints: ["dynamic-video-components-entry"],
  outfile: outPath,
  bundle: true,
  banner: {js: "/* eslint-disable */"},
  format: "iife",
  platform: "browser",
  target: "es2020",
  jsx: "automatic",
  plugins: [virtualEntryPlugin, hostShimPlugin],
  logLevel: "silent",
});
