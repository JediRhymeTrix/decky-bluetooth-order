import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";
import * as React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { initialSnapshot } from "../../src/model.ts";
import type { Snapshot, Surface } from "../../src/model.ts";
import { afterPatch as sharedAfterPatch } from "@decky/ui/dist/utils/patcher.js";

// Real React reconciliation renders fixtures only; no Steam globals or factories.
let exports: unknown[] = [];
let discoveries = 0;
let routePatch: ((route: any) => any) | undefined;
let mountedRoot: unknown;
const patches = new Set<any>();
const mockUi = {
  QuickAccessTab: { Settings: 4 },
  getReactRoot: () => mountedRoot,
  findModuleByExport(predicate: (value: unknown) => boolean) {
    discoveries++;
    const value = exports.find(predicate);
    return value ? { cached: value } : undefined;
  },
  findInReactTree(tree: any, predicate: (value: any) => boolean): any {
    if (!tree || typeof tree !== "object") return;
    if (predicate(tree)) return tree;
    for (const child of Array.isArray(tree) ? tree : [tree.props, tree.children, tree.child, tree.sibling]) {
      const found = mockUi.findInReactTree(child, predicate);
      if (found) return found;
    }
  },
  afterPatch(object: any, property: string, callback: (args: any[], result: any) => any) {
    const original = object[property];
    object[property] = (...args: any[]) => callback(args, original(...args));
    object[property].toString = () => original.toString();
    const patch = { unpatch() { object[property] = original; patches.delete(patch); } };
    patches.add(patch);
    return patch;
  },
};
const mockApi = { routerHook: {
  addPatch(_path: string, patch: (route: any) => any) { routePatch = patch; return patch; },
  removePatch(_path: string, patch: (route: any) => any) { assert.equal(patch, routePatch); routePatch = undefined; },
} };
(globalThis as any).__nativeTest = { ui: mockUi, api: mockApi };
const dataModule = (name: string, names: string[]) => "data:text/javascript," + encodeURIComponent(
  `const value=globalThis.__nativeTest.${name};` + names.map(key => `export const ${key}=value.${key};`).join(""));
const loader = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.endsWith("/src/runtime.ts") && specifier === "./model.js") return nextResolve("./model.ts", context);
    if (context.parentURL?.endsWith("/src/native.tsx")) {
      if (specifier === "@decky/ui") return { url: dataModule("ui", ["afterPatch", "findInReactTree", "findModuleByExport", "getReactRoot", "QuickAccessTab"]), shortCircuit: true };
      if (specifier === "@decky/api") return { url: dataModule("api", ["routerHook"]), shortCircuit: true };
      if (specifier === "./tree.js") return nextResolve("./tree.ts", context);
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith("/src/native.tsx")) return {
      format: "module", shortCircuit: true,
      source: transpileModule(readFileSync(new URL(url), "utf8"), { compilerOptions: { module: ModuleKind.ESNext, target: ScriptTarget.ES2022 } }).outputText,
    };
    return nextLoad(url, context);
  },
});
const { installNative } = await import("../../src/native.tsx");
const { Runtime } = await import("../../src/runtime.ts");
loader.deregister();

const h = React.createElement;
let nativeRows = [2, 1];
let mountSequence = 0;
let unmounts = 0;
let unknownLayout = false;
let misplacedTabs = false;
let sourceTabs: readonly any[];
let headerTabs: readonly any[];
let contentTabs: readonly any[];
function DeviceRow(props: any) {
  const [instance] = React.useState(() => ++mountSequence);
  React.useEffect(() => () => { unmounts++; }, []);
  return h("row", { deviceId: props.nDeviceId ?? props.device.id, instance, nativeAction: props.onActivate });
}
function SettingsRow(props: any) {
  void "#Settings_Bluetooth_XboxIssue_Title";
  const { nDeviceId } = props;
  return h(DeviceRow, { nDeviceId });
}
function BluetoothGroup(props: any) {
  void "#QuickAccess_Tab_Bluetooth_AddDevice";
  if (unknownLayout || props.unknownLayout) return h("unknown-native-group");
  return h("group", {}, [h("toggle", { key: "toggle" }), nativeRows.map(id => h("wrapper", { key: id },
    h(DeviceRow, { device: { id, is_paired: true, is_connected: false, operation_in_progress: props.pending === true }, onActivate: () => id }))),
  h("add", { key: "add" }), h("separator", { key: "separator" })]);
}
function SettingsBluetooth() {
  if (unknownLayout) return h("unknown-native-settings");
  return h("settings-bt", {}, [h("toggle", { key: "toggle" }), h("paired", { label: "Paired", key: "paired" },
    nativeRows.map(id => h(SettingsRow, { key: id, nDeviceId: id }))),
  h("available", { key: "available" }, [h("header", { key: "header" }), [9, 8].map(id => h(SettingsRow, { key: id, nDeviceId: id }))])]);
}
const PagesView = (props: any) => props.pages[0].content;
const RoutedPages = (props: any) => h(PagesView, { pages: props.pages.map((page: any) => ({ ...page, identifier: page.identifier ?? page.route })) });
function NativeSettings() {
  void "#Settings_Title"; void "Settings";
  return h(RoutedPages, { pages: [{ route: "/settings/bluetooth", content: h(SettingsBluetooth) }] });
}
const SettingsHolder = () => h(NativeSettings);
const QamPanel = (props: any) => h(BluetoothGroup, props);
const TabsHeader = (props: any) => { headerTabs = props.tabs; return h("tab-header"); };
const TabsContent = (props: any) => { contentTabs = props.tabs; return props.tabs[0].panel; };
function QamMenu(props: any) {
  sourceTabs = Object.freeze([Object.freeze({ key: 4, panel: h(QamPanel, { unknownLayout: props.unknownLayout, pending: props.pending }) })]);
  if (misplacedTabs) return h("menu", { child: h(TabsContent, { tabs: sourceTabs }) });
  return h("menu", {}, [h(TabsHeader, { key: "header", tabs: sourceTabs }), h(TabsContent, { key: "content", tabs: sourceTabs })]);
}
const qamMemo = React.memo(function Browser(props: any) {
  void "QuickAccessMenuBrowserView";
  return h(QamMenu, { ...props, onFocusNavDeactivated: () => {} });
});
const embeddedMemo = React.memo(function Embedded(props: any) {
  void "QuickAccessMenuEmbedded";
  return h(QamMenu, { ...props, onFocusNavDeactivated: () => {} });
});

class MockRuntime {
  snapshot: Snapshot = initialSnapshot();
  listeners = new Set<() => void>();
  statuses = { qam: "off", settings: "off" };
  faults = new Set<Surface>();
  throwSort = false;
  mapped = true;
  pending = -1;
  client: unknown;
  getSnapshot = () => this.snapshot;
  subscribe = (callback: () => void) => { this.listeners.add(callback); return () => this.listeners.delete(callback); };
  status(surface: Surface, value: string) { this.statuses[surface] = value; }
  log() {}
  onFault(surface: Surface) { this.faults.add(surface); }
  shouldSort(surface: Surface) { return this.snapshot.settings[surface] && !this.faults.has(surface) && this.snapshot.settings.mode !== "native"; }
  resolveDevice(id: number) { return this.mapped ? { id: String(id), operationPending: id === this.pending } : undefined; }
  sortRows(rows: readonly any[], identify: (row: any) => number) {
    if (this.throwSort) throw Error("fixture fault");
    const direction = this.snapshot.settings.mode === "za" ? -1 : 1;
    return [...rows].sort((a, b) => direction * (identify(a) - identify(b)));
  }
  setQueryClient(client: unknown) { this.client = client; }
  update(settings: any = {}) {
    this.snapshot = { ...this.snapshot, settings: { ...this.snapshot.settings, ...settings } };
    for (const callback of this.listeners) callback();
  }
}

function setup() {
  nativeRows = [2, 1]; discoveries = 0; mountSequence = 0; unmounts = 0; unknownLayout = false; misplacedTabs = false;
  exports = [BluetoothGroup, NativeSettings, SettingsRow, qamMemo, embeddedMemo,
    { getQueryData() {}, getQueryCache() {}, invalidateQueries() {} }];
  const runtime = new MockRuntime();
  const stop = installNative(runtime as any);
  return { runtime, stop };
}
const rows = (root: TestRenderer.ReactTestRenderer) => root.root.findAllByType("row").map(row => row.props.deviceId);
const instances = (root: TestRenderer.ReactTestRenderer) => Object.fromEntries(root.root.findAllByType("row").map(row => [row.props.deviceId, row.props.instance]));
function finish(root: TestRenderer.ReactTestRenderer, stop: () => void) {
  act(() => { stop(); root.unmount(); });
  assert.equal(routePatch, undefined); assert.equal(patches.size, 0);
}

class FocusNode {
  ownerDocument: any;
  callbacks = new Map<string, (event: any) => void>();
  contains(node: unknown) { return node === this; }
  addEventListener(event: string, callback: (event: any) => void) { this.callbacks.set(event, callback); }
  removeEventListener(event: string) { this.callbacks.delete(event); }
}

test("numeric Settings tab replaces both shared native consumers without changing original tabs", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(h(qamMemo)); });
  assert.deepEqual(rows(root!), [1, 2]);
  assert.equal(headerTabs!, contentTabs!); assert.notEqual(contentTabs!, sourceTabs!);
  assert.equal(sourceTabs![0].panel.type, QamPanel); assert.notEqual(contentTabs![0].panel.type, QamPanel);
  assert.equal(discoveries, 6); assert.ok(runtime.client); finish(root!, stop);
});

test("real React retains row mount state through off/native/cache loss/fault/teardown and ancestor rerenders", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  const route = routePatch!({ children: h(SettingsHolder) });
  act(() => { root = TestRenderer.create(route.children); });
  const mounted = instances(root!); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
  act(() => runtime.update({ settings: true, mode: "az" })); assert.deepEqual(rows(root!), [1, 2, 9, 8]);
  act(() => runtime.update({ settings: false })); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
  act(() => runtime.update({ settings: true, mode: "native" }));
  act(() => { runtime.mapped = false; runtime.update({ mode: "az" }); });
  assert.match(runtime.statuses.settings, /waiting/); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
  act(() => { runtime.mapped = true; runtime.throwSort = true; runtime.update(); });
  assert.ok(runtime.faults.has("settings")); assert.equal(runtime.faults.has("qam"), false);
  act(() => { stop(); root!.update(React.cloneElement(route.children, { ancestorUpdate: true })); });
  assert.deepEqual(instances(root!), mounted); assert.equal(unmounts, 0); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
  act(() => root!.unmount()); assert.equal(runtime.listeners.size, 0); assert.equal(patches.size, 0);
});

test("Settings accepts a preceding shared renderer patch while preserving its output and native controls", t => {
  t.mock.method(console, "debug", () => {});
  const { runtime, stop } = setup();
  let priorCalls = 0;
  const previous = sharedAfterPatch({ type: NativeSettings }, "type", (_args, tree: any) => {
    priorCalls++;
    return React.cloneElement(tree, { previousPatchMarker: true });
  });
  const outer = sharedAfterPatch(previous.object, "type", (_args, tree: any) => React.cloneElement(tree, { outerPatchMarker: true }));
  // Decky retains the first patched holder when it composes subsequent route patches.
  const PrecedingHolder = () => {
    const value = h(NativeSettings);
    const changed = { ...value, type: outer.patchedFunction };
    return changed;
  };
  const route = routePatch!({ children: h(PrecedingHolder) });
  let root: TestRenderer.ReactTestRenderer;
  act(() => { root = TestRenderer.create(route.children); });
  const mounted = instances(root!);
  try {
    act(() => runtime.update({ settings: true, mode: "az" }));
    assert.deepEqual(rows(root!), [1, 2, 9, 8]);
    assert.ok(root!.root.findAll(node => node.props.previousPatchMarker === true).length > 0);
    assert.ok(root!.root.findAll(node => node.props.outerPatchMarker === true).length > 0);
    assert.equal(priorCalls, 1);
    act(() => runtime.update({ mode: "za" })); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
    assert.deepEqual(instances(root!), mounted);
    act(() => runtime.update({ settings: false })); assert.deepEqual(rows(root!), [2, 1, 9, 8]);
    assert.deepEqual(instances(root!), mounted); assert.equal(runtime.faults.size, 0);
    act(() => { stop(); root!.update(React.cloneElement(route.children, { tick: 1 })); });
    assert.deepEqual(instances(root!), mounted);
    assert.ok(root!.root.findAll(node => node.props.previousPatchMarker === true).length > 0);
    assert.ok(priorCalls > 1);
  } finally {
    act(() => { stop(); root?.unmount(); }); outer.unpatch(); previous.unpatch();
  }
  assert.equal(patches.size, 0);
});

test("Settings rejects lookalike, forged and cyclic renderer chains", () => {
  for (const shape of ["lookalike", "forged", "cyclic"]) {
    const { runtime, stop } = setup();
    const unrelated = () => h(RoutedPages, { pages: [{ identifier: "/settings/bluetooth", content: h(SettingsBluetooth) }] });
    unrelated.toString = () => NativeSettings.toString();
    if (shape === "forged") (unrelated as any).__deckyPatch = { original: NativeSettings, patchedFunction: () => {}, property: "type" };
    if (shape === "cyclic") (unrelated as any).__deckyPatch = { original: unrelated, patchedFunction: unrelated, property: "type", object: { type: unrelated } };
    const Holder = () => h(unrelated);
    const route = routePatch!({ children: h(Holder) }); let root: TestRenderer.ReactTestRenderer;
    act(() => { runtime.update({ settings: true, mode: "az" }); root = TestRenderer.create(route.children); });
    assert.deepEqual(rows(root!), [2, 1, 9, 8]); assert.equal(runtime.faults.size, 0);
    finish(root!, stop);
  }
});

test("real QAM rows keep state on disable/fault/stop even when original menu rerenders", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(h(embeddedMemo, { active: true })); });
  const mounted = instances(root!);
  act(() => runtime.update({ qam: false })); assert.deepEqual(rows(root!), [2, 1]);
  act(() => { runtime.throwSort = true; runtime.update({ qam: true }); });
  assert.ok(runtime.faults.has("qam"));
  act(() => { stop(); root!.update(h(embeddedMemo, { active: false })); });
  assert.deepEqual(instances(root!), mounted); assert.equal(unmounts, 0);
  act(() => root!.unmount()); assert.equal(patches.size, 0);
});

test("already mounted memo fiber is not activated by reopening; status requires genuine natural remount", () => {
  let old: TestRenderer.ReactTestRenderer;
  act(() => { old = TestRenderer.create(h(qamMemo, { active: false })); });
  const { runtime, stop } = setup();
  act(() => { runtime.update({ qam: true, mode: "az" }); old!.update(h(qamMemo, { active: true })); });
  assert.deepEqual(rows(old!), [2, 1]); assert.match(runtime.statuses.qam, /pending native mount/);
  act(() => old!.update(h(qamMemo, { active: false })));
  act(() => old!.update(h(qamMemo, { active: true })));
  assert.deepEqual(rows(old!), [2, 1]);
  act(() => old!.unmount()); let fresh: TestRenderer.ReactTestRenderer;
  act(() => { fresh = TestRenderer.create(h(qamMemo, { active: true })); });
  assert.deepEqual(rows(fresh!), [1, 2]); finish(fresh!, stop);
});

test("Decky scoped attachment activates the existing QAM render reference without a root reset", () => {
  let root: TestRenderer.ReactTestRenderer;
  act(() => { root = TestRenderer.create(h(qamMemo, { active: false })); });
  const fiber = (root!.root as any)._fiber;
  const original = fiber.type;
  assert.equal(fiber.alternate, null);
  mountedRoot = fiber;
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { configurable: true, value: { getElementById: () => ({}) } });
  let stop: (() => void) | undefined;
  try {
    const fixture = setup(); stop = fixture.stop;
    assert.notEqual(fiber.type, original);
    assert.equal(fiber.type, qamMemo.type);
    act(() => {
      fixture.runtime.update({ qam: true, mode: "az" });
      root!.update(h(qamMemo, { active: true }));
    });
    assert.deepEqual(rows(root!), [1, 2]);
    assert.doesNotMatch(fixture.runtime.statuses.qam, /pending native mount/);
    const alternate = fiber.alternate;
    assert.ok(alternate);
    assert.equal(alternate.type, qamMemo.type);
    act(() => stop!());
    assert.equal(qamMemo.type, original);
    assert.equal(fiber.type, original);
    assert.equal(alternate.type, original);
    act(() => root!.update(h(qamMemo, { active: false })));
    assert.equal(fiber.type, original); assert.equal(alternate.type, original);
    assert.deepEqual(rows(root!), [2, 1]);
  } finally {
    act(() => { stop?.(); root!.unmount(); });
    mountedRoot = undefined;
    if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument);
    else delete (globalThis as any).document;
  }
  assert.equal(patches.size, 0);
});

test("pending operations hold QAM order until next active session", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(h(qamMemo, { active: true })); });
  const mounted = instances(root!);
  act(() => { runtime.pending = 1; runtime.update({ mode: "za" }); }); assert.deepEqual(rows(root!), [1, 2]);
  act(() => { runtime.pending = -1; root!.update(h(qamMemo, { active: false })); });
  act(() => root!.update(h(qamMemo, { active: true })));
  assert.deepEqual(rows(root!), [2, 1]); assert.deepEqual(instances(root!), mounted); finish(root!, stop);
});

test("real renderer observes paired focus without consuming controls or resetting row state", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  const document = { activeElement: null as FocusNode | null, defaultView: { Node: FocusNode } };
  const nodes: FocusNode[] = [];
  act(() => {
    runtime.update({ qam: true, mode: "az" });
    root = TestRenderer.create(h(qamMemo, { active: true }), { createNodeMock: () => {
      const node = new FocusNode(); node.ownerDocument = document; nodes.push(node); return node;
    } });
  });
  const outer = nodes.find(node => node.callbacks.has("gpfocus"))!;
  const paired = nodes.find(node => node !== outer)!;
  const control = new FocusNode(); control.ownerDocument = document;
  outer.callbacks.get("gpfocus")!({ target: control });
  act(() => runtime.update({ mode: "za" })); assert.deepEqual(rows(root!), [2, 1]);
  const mounted = instances(root!); document.activeElement = paired;
  outer.callbacks.get("gpfocus")!({ target: paired });
  act(() => runtime.update({ mode: "az" })); assert.deepEqual(rows(root!), [2, 1]);
  assert.match(runtime.statuses.qam, /held/); assert.deepEqual(instances(root!), mounted);
  finish(root!, stop); assert.equal(outer.callbacks.size, 0);
});

test("inactive QAM siblings do not reset another mounted entry's focused session", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  const document = { activeElement: null as FocusNode | null, defaultView: { Node: FocusNode } };
  const nodes: FocusNode[] = [];
  const tree = (active: boolean, tick: number) => h(React.Fragment, {},
    h(qamMemo, { key: "browser", active, tick }), h(embeddedMemo, { key: "embedded", active: false, tick }));
  act(() => {
    runtime.update({ qam: true, mode: "az" });
    root = TestRenderer.create(tree(true, 0), { createNodeMock: () => {
      const node = new FocusNode(); node.ownerDocument = document; nodes.push(node); return node;
    } });
  });
  try {
    const outer = nodes.find(node => node.callbacks.has("gpfocus"))!;
    const paired = nodes.find(node => node !== outer)!;
    const mounted = instances(root!); const mounts = mountSequence;
    document.activeElement = paired; outer.callbacks.get("gpfocus")!({ target: paired });
    act(() => runtime.update({ mode: "za" }));
    assert.deepEqual(rows(root!).slice(0, 2), [1, 2]);
    act(() => root!.update(tree(true, 1)));
    assert.deepEqual(rows(root!).slice(0, 2), [1, 2]);
    document.activeElement = null;
    act(() => root!.update(tree(false, 2)));
    act(() => root!.update(tree(true, 3)));
    assert.deepEqual(rows(root!).slice(0, 2), [2, 1]);
    assert.equal(mountSequence, mounts); assert.deepEqual(instances(root!), mounted);
  } finally { finish(root!, stop); }
});

test("each instance of the same QAM renderer owns its reopen generation", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  const tree = (active: boolean, tick: number) => h(React.Fragment, {},
    h(qamMemo, { key: "visible", active, tick }), h(qamMemo, { key: "hidden", active: false, tick }));
  act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(tree(true, 0)); });
  try {
    const mounts = mountSequence;
    act(() => { runtime.pending = 1; runtime.update({ mode: "za" }); });
    assert.deepEqual(rows(root!).slice(0, 2), [1, 2]);
    act(() => { runtime.pending = -1; root!.update(tree(false, 1)); });
    act(() => root!.update(tree(true, 2)));
    assert.deepEqual(rows(root!).slice(0, 2), [2, 1]); assert.equal(mountSequence, mounts);
  } finally { finish(root!, stop); }
});

test("QAM applies the chosen order when a pending operation finishes after the closing render", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(h(qamMemo, { active: true })); });
  try {
    const mounted = instances(root!);
    act(() => { runtime.pending = 1; runtime.update({ mode: "za" }); }); assert.deepEqual(rows(root!), [1, 2]);
    act(() => root!.update(h(qamMemo, { active: false })));
    act(() => { runtime.pending = -1; runtime.update(); });
    act(() => root!.update(h(qamMemo, { active: true })));
    assert.deepEqual(rows(root!), [2, 1]); assert.deepEqual(instances(root!), mounted);
    assert.equal(runtime.statuses.qam, "active");
  } finally { finish(root!, stop); }
});

test("QAM reopening clears a hold when paired focus was removed after closing", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  const document = { activeElement: null as FocusNode | null, defaultView: { Node: FocusNode } };
  const nodes: FocusNode[] = [];
  act(() => {
    runtime.update({ qam: true, mode: "az" });
    root = TestRenderer.create(h(qamMemo, { active: true }), { createNodeMock: () => {
      const node = new FocusNode(); node.ownerDocument = document; nodes.push(node); return node;
    } });
  });
  try {
    const outer = nodes.find(node => node.callbacks.has("gpfocus"))!;
    const paired = nodes.find(node => node !== outer)!;
    document.activeElement = paired; outer.callbacks.get("gpfocus")!({ target: paired });
    act(() => runtime.update({ mode: "za" })); assert.deepEqual(rows(root!), [1, 2]);
    act(() => root!.update(h(qamMemo, { active: false })));
    document.activeElement = null;
    act(() => root!.update(h(qamMemo, { active: true })));
    assert.deepEqual(rows(root!), [2, 1]); assert.equal(runtime.statuses.qam, "active");
  } finally { finish(root!, stop); }
});

test("a native pending operation establishes a hold during an unmapped-cache gap", () => {
  const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
  act(() => {
    runtime.mapped = false; runtime.update({ qam: true, mode: "az" });
    root = TestRenderer.create(h(qamMemo, { active: true, pending: true }));
  });
  try {
    assert.deepEqual(rows(root!), [2, 1]);
    act(() => { runtime.mapped = true; root!.update(h(qamMemo, { active: true, pending: false })); runtime.update(); });
    assert.deepEqual(rows(root!), [2, 1]); assert.match(runtime.statuses.qam, /held/);
    act(() => root!.update(h(qamMemo, { active: false })));
    act(() => root!.update(h(qamMemo, { active: true })));
    assert.deepEqual(rows(root!), [1, 2]); assert.equal(runtime.statuses.qam, "active");
  } finally { finish(root!, stop); }
});

test("focused cache fallback retains the displayed native order when data returns", () => {
  for (const initiallyMapped of [true, false]) {
    const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
    const document = { activeElement: null as FocusNode | null, defaultView: { Node: FocusNode } };
    const nodes: FocusNode[] = [];
    act(() => {
      runtime.mapped = initiallyMapped; runtime.update({ qam: true, mode: "az" });
      root = TestRenderer.create(h(qamMemo, { active: true }), { createNodeMock: () => {
        const node = new FocusNode(); node.ownerDocument = document; nodes.push(node); return node;
      } });
    });
    try {
      const outer = nodes.find(node => node.callbacks.has("gpfocus"))!;
      const paired = nodes.find(node => node !== outer)!;
      document.activeElement = paired; outer.callbacks.get("gpfocus")!({ target: paired });
      const mounts = mountSequence;
      act(() => { runtime.mapped = false; runtime.update(); }); assert.deepEqual(rows(root!), [2, 1]);
      act(() => { runtime.mapped = true; runtime.update(); }); assert.deepEqual(rows(root!), [2, 1]);
      assert.equal(mountSequence, mounts);
    } finally { finish(root!, stop); }
  }
});

test("native and disabled modes replace an obsolete hold with the displayed native order", () => {
  for (const settings of [{ mode: "native" }, { qam: false }]) {
    const { runtime, stop } = setup(); let root: TestRenderer.ReactTestRenderer;
    act(() => { runtime.update({ qam: true, mode: "az" }); root = TestRenderer.create(h(qamMemo, { active: true })); });
    try {
      act(() => { runtime.pending = 1; runtime.update({ mode: "za" }); }); assert.deepEqual(rows(root!), [1, 2]);
      act(() => runtime.update(settings)); assert.deepEqual(rows(root!), [2, 1]);
      act(() => { runtime.pending = -1; runtime.update({ qam: true, mode: "za" }); });
      assert.deepEqual(rows(root!), [2, 1]);
    } finally { finish(root!, stop); }
  }
});

test("actual Runtime applies each confirmed sort mode to mounted QAM rows without remounting", async () => {
  const fixture = setup(); fixture.stop();
  exports[exports.length - 1] = {
    getQueryData: (key: any[]) => ({ mac: `00:00:00:00:00:0${key[3]}`, name: key[3] === 1 ? "Alpha" : "Zulu", is_connected: false }),
    invalidateQueries() {}, getQueryCache: () => ({ subscribe: () => () => {} }),
  };
  let snapshot = initialSnapshot();
  snapshot.settings = { ...snapshot.settings, qam: true, connectedFirst: false };
  snapshot.history = {
    "00:00:00:00:00:01": { timestamp: 100, sequence: 2 },
    "00:00:00:00:00:02": { timestamp: 99, sequence: 1 },
  };
  const runtime = new Runtime({
    getSnapshot: async () => snapshot,
    setSettings: async change => {
      snapshot = { ...snapshot, revision: snapshot.revision + 1, settings: { ...snapshot.settings, ...change } };
      return snapshot;
    },
    resetHistory: async () => snapshot, logDiagnostics: async () => {}, exportDiagnostics: async () => "",
  });
  runtime.accept(snapshot); const stop = installNative(runtime); let root: TestRenderer.ReactTestRenderer;
  act(() => { root = TestRenderer.create(h(qamMemo, { active: true })); });
  const mounted = instances(root!);
  try {
    for (const [mode, expected] of [["az", [1, 2]], ["za", [2, 1]], ["native", [2, 1]], ["recent", [1, 2]]] as const) {
      await act(async () => runtime.updateSettings({ mode }));
      assert.equal(runtime.getSnapshot().settings.mode, mode);
      assert.deepEqual(rows(root!), expected); assert.deepEqual(instances(root!), mounted);
    }
  } finally { act(() => { stop(); root?.unmount(); runtime.dispose(); }); }
  assert.equal(patches.size, 0);
});

test("missing exports and unknown route fail open without retries or fabricated readiness", () => {
  exports = []; discoveries = 0;
  const runtime = new MockRuntime(); const stop = installNative(runtime as any);
  runtime.update({ qam: true, settings: true });
  assert.match(runtime.statuses.qam, /unavailable/); assert.match(runtime.statuses.settings, /unavailable/);
  assert.equal(discoveries, 6); assert.equal(routePatch, undefined); stop();
  const ready = setup(); const route = { children: "unknown" };
  assert.equal(routePatch!(route), route); assert.match(ready.runtime.statuses.settings, /unsupported/);
  ready.stop(); assert.equal(patches.size, 0);
});

test("actual Runtime observable statuses settle for disabled and enabled unknown native layouts", async () => {
  const fixture = setup(); fixture.stop(); unknownLayout = true;
  exports[exports.length - 1] = {
    getQueryData() {}, invalidateQueries() {},
    getQueryCache: () => ({ subscribe: () => () => {} }),
  };
  const runtime = new Runtime({
    getSnapshot: async () => initialSnapshot(), setSettings: async () => initialSnapshot(),
    resetHistory: async () => initialSnapshot(), logDiagnostics: async () => {}, exportDiagnostics: async () => "",
  });
  let publications = 0;
  let tripped = false;
  const unsubscribe = runtime.subscribe(() => {
    publications++;
    // Bound the old off/unsupported feedback loop so a regression fails, not hangs.
    if (publications > 8) { tripped = true; runtime.dispose(); }
  });
  const stop = installNative(runtime);
  let root: TestRenderer.ReactTestRenderer;
  const route = routePatch!({ children: h(SettingsHolder) });
  const settle = async () => { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); }); };
  try {
    act(() => { root = TestRenderer.create(h(React.Fragment, {}, h(qamMemo), route.children)); });
    await settle();
    assert.equal(runtime.statuses.qam, "off"); assert.equal(runtime.statuses.settings, "off");
    const disabledCount = publications; await settle(); assert.equal(publications, disabledCount);
    act(() => runtime.accept({ ...runtime.getSnapshot(), settings: { ...runtime.getSnapshot().settings, qam: true, settings: true } }));
    await settle();
    assert.match(runtime.statuses.qam, /unsupported layout/); assert.match(runtime.statuses.settings, /unsupported layout/);
    const enabledCount = publications; await settle(); assert.equal(publications, enabledCount);
    act(() => runtime.accept({ ...runtime.getSnapshot(), settings: { ...runtime.getSnapshot().settings, qam: false, settings: false } }));
    await settle();
    assert.equal(runtime.statuses.qam, "off"); assert.equal(runtime.statuses.settings, "off");
    const offCount = publications; await settle(); assert.equal(publications, offCount);
    assert.equal(tripped, false); assert.ok(publications <= 6);
  } finally {
    act(() => { stop(); root?.unmount(); runtime.dispose(); });
    unsubscribe(); unknownLayout = false;
  }
  assert.equal(patches.size, 0);
});

test("actual Runtime settles when mounted QAM groups report competing enabled statuses", async () => {
  for (const other of [{ unknownLayout: true }, { pending: true }]) {
    const fixture = setup(); fixture.stop();
    exports[exports.length - 1] = {
      getQueryData: (key: any[]) => ({ mac: `00:00:00:00:00:0${key[3]}`, name: `Fixture ${key[3]}`, is_connected: false }),
      invalidateQueries() {}, getQueryCache: () => ({ subscribe: () => () => {} }),
    };
    const runtime = new Runtime({
      getSnapshot: async () => initialSnapshot(), setSettings: async () => initialSnapshot(),
      resetHistory: async () => initialSnapshot(), logDiagnostics: async () => {}, exportDiagnostics: async () => "",
    });
    let publications = 0; let tripped = false;
    const unsubscribe = runtime.subscribe(() => {
      publications++;
      if (publications > 8) { tripped = true; runtime.dispose(); }
    });
    const stop = installNative(runtime);
    let root: TestRenderer.ReactTestRenderer;
    const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
    try {
      act(() => { root = TestRenderer.create(h(React.Fragment, {}, h(qamMemo, { active: true }), h(embeddedMemo, { active: false, ...other }))); });
      await settle();
      const disabledCount = publications; await settle(); assert.equal(publications, disabledCount);
      act(() => runtime.accept({ ...runtime.getSnapshot(), settings: { ...runtime.getSnapshot().settings, qam: true, mode: "az" } }));
      await settle();
      assert.equal(tripped, false, "competing QAM status reports must not sustain snapshot publications");
      assert.match(runtime.statuses.qam, other.unknownLayout ? /unsupported layout/ : /held/);
      assert.deepEqual(rows(root!).slice(0, 2), [1, 2]);
      const mounted = mountSequence; const enabledCount = publications;
      await settle(); assert.equal(publications, enabledCount); assert.equal(mountSequence, mounted);
      act(() => root!.update(h(React.Fragment, {}, h(qamMemo, { active: true, tick: 1 }), h(embeddedMemo, { active: false, tick: 1, ...other }))));
      await settle(); assert.equal(publications, enabledCount); assert.equal(mountSequence, mounted);
      act(() => runtime.accept({ ...runtime.getSnapshot(), settings: { ...runtime.getSnapshot().settings, qam: false } }));
      await settle(); assert.equal(runtime.statuses.qam, "off");
      assert.deepEqual(rows(root!).slice(0, 2), [2, 1]);
    } finally {
      act(() => { stop(); root?.unmount(); runtime.dispose(); }); unsubscribe();
    }
    assert.equal(patches.size, 0);
  }
});

test("a tabs consumer outside replaceable children fails open without repeating the search", t => {
  const fixture = setup(); misplacedTabs = true;
  const find = mockUi.findInReactTree;
  let searches = 0;
  t.mock.method(mockUi, "findInReactTree", (tree: any, predicate: (value: any) => boolean) => {
    if (tree?.type === "menu" && ++searches > 8) throw new Error("bounded non-progressing tabs search");
    return find(tree, predicate);
  });
  let root: TestRenderer.ReactTestRenderer;
  try {
    act(() => { root = TestRenderer.create(h(qamMemo)); });
    assert.equal(fixture.runtime.faults.size, 0);
    assert.ok(searches <= 3);
    assert.match(fixture.runtime.statuses.qam, /unsupported tabs/);
    assert.equal(root!.root.findByType("menu").props.child.props.tabs, sourceTabs);
  } finally {
    act(() => { fixture.stop(); root?.unmount(); }); misplacedTabs = false;
  }
  assert.equal(patches.size, 0);
});