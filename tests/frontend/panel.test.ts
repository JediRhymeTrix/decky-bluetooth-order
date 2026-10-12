import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import { transpileModule, ModuleKind, ScriptTarget, JsxEmit } from "typescript";
import { createElement } from "react";
import TestRenderer, { act } from "react-test-renderer";
import { initialSnapshot } from "../../src/model.ts";
import type { Settings, Snapshot } from "../../src/model.ts";

// Render the actual plugin panel/runtime, with inert controls and no native installer.
let snapshot: Snapshot = initialSnapshot();
let writes: Partial<Settings>[] = [];
let rejectSettings = false;
const listeners = new Set<(value: Snapshot) => void>();
const control = (name: string) => (props: any) => createElement(name, props);
const mockUi = {
  ButtonItem: control("button"), DropdownItem: control("dropdown"), PanelSection: control("section"),
  PanelSectionRow: control("section-row"), ToggleField: control("toggle"), staticClasses: { Title: "title" },
};
const mockApi = {
  definePlugin: (factory: () => unknown) => factory,
  addEventListener: (_event: string, listener: (value: Snapshot) => void) => { listeners.add(listener); return listener; },
  removeEventListener: (_event: string, listener: (value: Snapshot) => void) => { listeners.delete(listener); },
  callable: (name: string) => async (...args: any[]) => {
    if (name === "get_snapshot") return snapshot;
    if (name === "set_settings") {
      if (rejectSettings) throw new Error("fixture backend rejected settings");
      writes.push(args[0]);
      snapshot = { ...snapshot, revision: snapshot.revision + 1, settings: { ...snapshot.settings, ...args[0] } };
      for (const listener of listeners) listener(snapshot);
      return snapshot;
    }
    if (name === "reset_history") return snapshot;
    if (name === "export_diagnostics") return "{}";
  },
};
(globalThis as any).__panelTest = { ui: mockUi, api: mockApi };
const moduleSource = (key: string, names: string[]) => "data:text/javascript," + encodeURIComponent(
  `const value=globalThis.__panelTest.${key};` + names.map(name => `export const ${name}=value.${name};`).join(""));
const loader = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.endsWith("/src/index.tsx")) {
      if (specifier === "@decky/ui") return { url: moduleSource("ui", Object.keys(mockUi)), shortCircuit: true };
      if (specifier === "@decky/api") return { url: moduleSource("api", Object.keys(mockApi)), shortCircuit: true };
      if (specifier === "./runtime") return nextResolve("./runtime.ts", context);
      if (specifier === "./native") return { url: "data:text/javascript,export const installNative=()=>()=>{}", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith("/src/index.tsx")) return {
      format: "module", shortCircuit: true,
      source: transpileModule(readFileSync(new URL(url), "utf8"), {
        fileName: url, compilerOptions: { module: ModuleKind.ESNext, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX },
      }).outputText,
    };
    return nextLoad(url, context);
  },
});
const { default: pluginFactory } = await import("../../src/index.tsx");
loader.deregister();
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };

test("panel selection follows confirmed snapshots for every sort mode and recovers after a rejected request", async () => {
  snapshot = initialSnapshot(); writes = []; rejectSettings = false;
  const plugin = pluginFactory(); let root: TestRenderer.ReactTestRenderer;
  act(() => { root = TestRenderer.create(plugin.content); }); await settle();
  try {
    for (const mode of ["az", "za", "native", "recent"] as const) {
      const dropdown = root!.root.findByType("dropdown");
      const option = dropdown.props.rgOptions.find((option: any) => option.data === mode);
      act(() => dropdown.props.onChange(option)); await settle();
      assert.equal(root!.root.findByType("dropdown").props.selectedOption, mode);
      assert.equal(plugin.content.props.runtime.getSnapshot().settings.mode, mode);
      assert.equal(root!.root.findByType("dropdown").props.disabled, false);
    }
    assert.deepEqual(writes, [{ mode: "az" }, { mode: "za" }, { mode: "native" }, { mode: "recent" }]);
    rejectSettings = true;
    act(() => root!.root.findByType("dropdown").props.onChange({ data: "az" })); await settle();
    assert.equal(root!.root.findByType("dropdown").props.selectedOption, "recent");
    assert.equal(root!.root.findByType("dropdown").props.disabled, false);
    assert.match(JSON.stringify(root!.toJSON()), /request failed/);
    rejectSettings = false;
    act(() => root!.root.findByType("dropdown").props.onChange({ data: "za" })); await settle();
    assert.equal(root!.root.findByType("dropdown").props.selectedOption, "za");
    assert.doesNotMatch(JSON.stringify(root!.toJSON()), /request failed/);
  } finally { act(() => { plugin.onDismount(); root?.unmount(); }); }
  assert.equal(listeners.size, 0);
});

test("a retained panel is inert after unload and reopening uses the new runtime", async () => {
  snapshot = initialSnapshot(); writes = []; rejectSettings = false;
  const old = pluginFactory(); let root: TestRenderer.ReactTestRenderer;
  act(() => { root = TestRenderer.create(old.content); }); await settle();
  act(() => old.onDismount());
  assert.match(JSON.stringify(root!.toJSON()), /old panel is inactive/);
  const controls = root!.root.findAll(node => ["dropdown", "toggle", "button"].includes(String(node.type)));
  assert.ok(controls.length > 0); assert.ok(controls.every(node => node.props.disabled === true));
  act(() => root!.root.findByType("dropdown").props.onChange({ data: "az" })); await settle();
  assert.equal(writes.length, 0);
  assert.equal(old.content.props.runtime.isStopped(), true);
  snapshot = { ...snapshot, settings: { ...snapshot.settings, mode: "za" } };
  const current = pluginFactory();
  try {
    act(() => root!.update(current.content)); await settle();
    assert.equal(root!.root.findByType("dropdown").props.selectedOption, "za");
    assert.equal(root!.root.findByType("dropdown").props.disabled, false);
    assert.doesNotMatch(JSON.stringify(root!.toJSON()), /old panel is inactive/);
    assert.notEqual(current.content.props.runtime, old.content.props.runtime);
  } finally { act(() => { current.onDismount(); root?.unmount(); }); }
  assert.equal(listeners.size, 0);
});