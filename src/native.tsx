import { routerHook } from "@decky/api";
import { afterPatch, findInReactTree, findModuleByExport, getReactRoot, QuickAccessTab } from "@decky/ui";
import { cloneElement, createContext, createElement, useContext, useEffect, useRef, useSyncExternalStore } from "react";
import type { ComponentType, ReactElement } from "react";
import type { Runtime, QueryClient } from "./runtime.js";
import type { Snapshot, Surface } from "./model.js";
import { element, qamRows, replaceTree, retainOrder, settingsRows } from "./tree.js";
import type { Clone, NativeElement, RowGroup } from "./tree.js";
import { attachMountedRenderer } from "./attachment.js";

type NativeType = ComponentType<Record<string, unknown>>;
type Transform = (tree: unknown) => unknown;
const clone: Clone = (node, props) => cloneElement(node as ReactElement, props) as NativeElement;
const source = (value: unknown): string => typeof value === "function" ? String(value) : "";
const withType = (node: NativeElement, type: NativeType): NativeElement => ({ ...clone(node, {}), type });

/** The shared Decky cache is already initialized. Never load a native module here. */
function cachedExport(predicate: (value: unknown) => boolean): unknown {
  const module: unknown = findModuleByExport(predicate);
  if (!module || typeof module !== "object") return;
  return Object.values(module).find(predicate);
}

function functionType(value: unknown): value is NativeType {
  return typeof value === "function";
}

/** Recognize only Decky's recorded type-patch chain, not matching source text. */
function rendererMatches(value: unknown, original: unknown): boolean {
  const seen = new Set<unknown>();
  while (typeof value === "function" && !seen.has(value)) {
    if (value === original) return true;
    seen.add(value);
    const patch = Object.getOwnPropertyDescriptor(value, "__deckyPatch")?.value;
    if (!patch || patch.patchedFunction !== value || patch.property !== "type" ||
        !patch.object || typeof patch.object !== "object" || typeof patch.original !== "function") return false;
    value = patch.original;
  }
  return false;
}

/** Install bounded entry adapters; normal React rendering reveals each child layer. */
export function installNative(runtime: Runtime): () => void {
  let active = true;
  let stoppedSnapshot: Snapshot | undefined;
  const getSnapshot = () => active ? runtime.getSnapshot() : stoppedSnapshot!;
  const handles = new Set<() => void>();
  const wake = new Set<() => void>();
  const wrappers = new Map<unknown, Map<string, NativeType>>();
  const attached = new Set<Surface>();
  const failed = new Set<Surface>();
  const available = new Set<Surface>();
  const mountedEntries = new Set<string>();
  const QamSession = createContext<{ open: boolean; generation: number } | null>(null);
  const enabled = { ...runtime.getSnapshot().settings };
  let bluetoothGroup: unknown;
  let settingsType: unknown;
  let settingsRowType: unknown;

  const fault = (surface: Surface, error: unknown) => {
    if (!active || failed.has(surface)) return;
    failed.add(surface);
    runtime.onFault(surface, "native-adapter-fault", error);
  };
  const guard = (surface: Surface, tree: unknown, transform: Transform): unknown => {
    // Retained renderers still clone the established ancestry after disabling/unloading.
    // Dropping a wrapper here would remount native rows and lose their hook state.
    try { return transform(tree); }
    catch (error) { fault(surface, error); return tree; }
  };
  const subscribe = (listener: () => void) => {
    wake.add(listener);
    const unsubscribe = runtime.subscribe(listener);
    return () => { wake.delete(listener); unsubscribe(); };
  };

  // Owned holders, not export namespaces, are patched. React calls their render functions.
  const wrap = (node: NativeElement, surface: Surface, stage: string, transform: Transform): NativeElement => {
    if (!functionType(node.type)) return node;
    let stages = wrappers.get(node.type);
    if (!stages) wrappers.set(node.type, stages = new Map());
    let Wrapped = stages.get(stage);
    if (!Wrapped) {
      if (!active) return node;
      const holder = { render: node.type };
      const patch = afterPatch(holder, "render", (_args, tree) => guard(surface, tree, transform));
      handles.add(() => patch.unpatch());
      const Render = holder.render;
      if (stage === "qam-tabs") {
        Wrapped = props => {
          const session = useRef({ open: false, generation: 0 });
          const open = props.active === true;
          if (session.current.open !== open) session.current.generation++;
          session.current.open = open;
          return createElement(QamSession.Provider, { value: session.current }, createElement(Render, props));
        };
      } else Wrapped = props => createElement(Render, props);
      stages.set(stage, Wrapped);
    }
    return withType(node, Wrapped);
  };

  const replace = (tree: unknown, node: NativeElement, replacement: NativeElement) =>
    replacement === node ? tree : replaceTree(tree, node, replacement, clone);
  const child = (tree: unknown, predicate: (node: NativeElement) => boolean): NativeElement | undefined =>
    findInReactTree(tree, (node: unknown) => element(node) && predicate(node));

  /** A stable hook-owning wrapper gives mounted native groups snapshot updates. */
  const rowsWrapper = (node: NativeElement, surface: Surface): NativeElement => {
    if (!functionType(node.type)) return node;
    let stages = wrappers.get(node.type);
    if (!stages) wrappers.set(node.type, stages = new Map());
    let Wrapped = stages.get(`${surface}-rows`);
    if (!Wrapped) {
      if (!active) return node;
      const Original = node.type;
      Wrapped = props => {
        useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
        const session = useContext(QamSession);
        const host = useRef<HTMLDivElement>(null);
        const state = useRef({ frozen: false, order: [] as string[], generation: session?.generation ?? 0, pairedHost: null as HTMLDivElement | null });
        const pairedRef = useRef((host: HTMLDivElement | null) => { state.current.pairedHost = host; });
        const mount = useRef<{ render: NativeType; unpatch: () => void }>();
        if (!mount.current) {
          const holder = { render: Original };
          const patch = afterPatch(holder, "render", (_args, value) => {
            if (surface === "qam" && session && state.current.generation !== session.generation) {
              state.current.frozen = false;
              state.current.generation = session.generation;
            }
            const group = surface === "qam" ? qamRows(value) : settingsRows(value, settingsRowType);
            if (!group) {
              if (active && runtime.shouldSort(surface)) runtime.status(surface, "unsupported layout; native order");
              return value;
            }
            if (active) attached.add(surface);
            if (!active || failed.has(surface) || !runtime.shouldSort(surface)) {
              state.current.frozen = false;
              state.current.order = group.rows.map(row => String(group.identify(row)));
              return renderPaired(group, group.rows, surface, pairedRef.current);
            }
            try {
              let pending = false;
              let mapped = 0;
              for (const row of group.rows) {
                const id = group.identify(row);
                const device = id === undefined ? undefined : runtime.resolveDevice(id);
                if (device) mapped++;
                if (device?.operationPending) pending = true;
                if (surface === "qam" && element(row.props.children)) {
                  const native = row.props.children.props.device as Record<string, unknown>;
                  if (native.operation_in_progress === true || native.is_pairing === true) pending = true;
                }
              }
              const focused = state.current.pairedHost;
              if (focused?.contains(focused.ownerDocument.activeElement) || pending) state.current.frozen = true;
              if (group.rows.length && !mapped) {
                runtime.status(surface, "waiting for native device cache");
                state.current.order = group.rows.map(row => String(group.identify(row)));
                return renderPaired(group, group.rows, surface, pairedRef.current);
              }
              const ordered = state.current.frozen ?
                state.current.order.length ? retainOrder(group.rows, group.identify, state.current.order) : group.rows :
                runtime.sortRows(group.rows, group.identify);
              state.current.order = ordered.map(row => String(group.identify(row)));
              runtime.status(surface, state.current.frozen ? "active; order held until reopen" : "active");
              return renderPaired(group, ordered, surface, pairedRef.current);
            } catch (error) {
              fault(surface, error);
              return renderPaired(group, group.rows, surface, pairedRef.current);
            }
          });
          const unpatch = () => patch.unpatch();
          mount.current = { render: holder.render, unpatch };
        }
        useEffect(() => {
          const unpatch = mount.current!.unpatch;
          handles.add(unpatch);
          // The holder is private to this mount; dropping the handle lets it be collected.
          return () => { handles.delete(unpatch); };
        }, []);
        useEffect(() => {
          const outer = host.current;
          if (!outer) return;
          const freeze = (event: Event) => {
            const paired = state.current.pairedHost;
            const Node = paired?.ownerDocument.defaultView?.Node;
            if (paired && Node && event.target instanceof Node && paired.contains(event.target)) {
              state.current.frozen = true;
            }
          };
          outer.addEventListener("gpfocus", freeze, true);
          outer.addEventListener("focusin", freeze, true);
          outer.addEventListener("pointerdown", freeze, true);
          return () => {
            outer.removeEventListener("gpfocus", freeze, true);
            outer.removeEventListener("focusin", freeze, true);
            outer.removeEventListener("pointerdown", freeze, true);
          };
        }, []);
        return createElement("div", { ref: host, style: { display: "contents" } }, createElement(mount.current.render, props));
      };
      stages.set(`${surface}-rows`, Wrapped);
    }
    return withType(node, Wrapped);
  };

  function renderPaired(group: RowGroup, ordered: readonly NativeElement[], surface: Surface,
    ref: (host: HTMLDivElement | null) => void): unknown {
    // The host adds no focus target or handlers to native controls; only observes bubbling focus.
    const children = createElement("div", {
      key: "bluetooth-order-paired",
      style: { display: "contents" },
      ref,
    }, ordered as unknown as ReactElement[]);
    const sorted = group.replace(ordered, clone);
    if (!element(sorted) || !Array.isArray(sorted.props.children) || !group.rows.length) return sorted;
    const items = [...sorted.props.children];
    if (surface === "qam") items[1] = children;
    else if (element(items[1])) items[1] = clone(items[1], { children });
    return clone(sorted, { children: items });
  }

  const qamPanel: Transform = tree => {
    const group = child(tree, node => node.type === bluetoothGroup);
    return group ? replace(tree, group, rowsWrapper(group, "qam")) : tree;
  };
  const qamTabs: Transform = tree => {
    const tabs = child(tree, node => Array.isArray(node.props.tabs));
    if (!tabs) return tree;
    const values = tabs.props.tabs as unknown[];
    const matches = values.filter(tab => !!tab && typeof tab === "object" && (tab as { key?: unknown }).key === QuickAccessTab.Settings);
    if (matches.length !== 1) {
      if (active) runtime.status("qam", "unsupported tabs; native order");
      return tree;
    }
    const tab = matches[0] as { panel?: unknown };
    if (!element(tab.panel)) return tree;
    const panel = wrap(tab.panel, "qam", "qam-panel", qamPanel);
    const changed = values.map(value => value === tab ? { ...tab, panel } : value);
    // Steam passes one native array to both its tab header and content renderer.
    let result = tree;
    for (;;) {
      const consumer = child(result, node => node.props.tabs === values);
      if (!consumer) return result;
      const next = replace(result, consumer, clone(consumer, { tabs: changed }));
      // The finder can reach props that replaceTree intentionally does not clone.
      if (next === result) {
        if (active) runtime.status("qam", "unsupported tabs; native order");
        return tree;
      }
      result = next;
    }
  };
  const qamEntry: Transform = tree => {
    const entry = child(tree, node => typeof node.props.onFocusNavDeactivated === "function");
    return entry ? replace(tree, entry, wrap(entry, "qam", "qam-tabs", qamTabs)) : tree;
  };

  const routedPages: Transform = tree => {
    const pages = child(tree, node => Array.isArray(node.props.pages));
    if (!pages) return tree;
    const values = pages.props.pages as unknown[];
    const matches = values.filter(page => !!page && typeof page === "object" &&
      (page as { identifier?: unknown }).identifier === "/settings/bluetooth");
    if (matches.length !== 1) {
      if (active) runtime.status("settings", "unsupported pages; native order");
      return tree;
    }
    const page = matches[0] as { content?: unknown };
    if (!element(page.content)) return tree;
    const content = rowsWrapper(page.content, "settings");
    return replace(tree, pages, clone(pages, { pages: values.map(value => value === page ? { ...page, content } : value) }));
  };
  const settingsPages: Transform = tree => {
    const pages = child(tree, node => Array.isArray(node.props.pages));
    return pages ? replace(tree, pages, wrap(pages, "settings", "settings-routed", routedPages)) : tree;
  };
  const settingsEntry: Transform = tree => {
    const settings = child(tree, node => rendererMatches(node.type, settingsType));
    return settings ? replace(tree, settings, wrap(settings, "settings", "settings-pages", settingsPages)) : tree;
  };

  try {
    const client = cachedExport(value => !!value && typeof value === "object" &&
      typeof (value as QueryClient).getQueryData === "function" &&
      typeof (value as QueryClient).getQueryCache === "function" &&
      typeof (value as { invalidateQueries?: unknown }).invalidateQueries === "function");
    if (client) runtime.setQueryClient(client as QueryClient);
    bluetoothGroup = cachedExport(value => source(value).includes("#QuickAccess_Tab_Bluetooth_AddDevice"));
    settingsType = cachedExport(value => source(value).includes("#Settings_Title") &&
      source(value).includes('"Settings"') && source(value).includes("pages:"));
    settingsRowType = cachedExport(value => source(value).includes("#Settings_Bluetooth_XboxIssue_Title") && source(value).includes("nDeviceId"));
  } catch { runtime.log("warn", "native-adapters-unavailable"); }

  for (const signature of ["QuickAccessMenuBrowserView", "QuickAccessMenuEmbedded"]) {
    try {
      const memo = cachedExport(value => !!value && typeof value === "object" &&
        source((value as { type?: unknown }).type).includes(signature)) as { type?: NativeType } | undefined;
      if (!bluetoothGroup || !memo?.type) { runtime.status("qam", "adapter unavailable; native order"); continue; }
      const original = memo.type;
      const rootElement = typeof document !== "undefined" ? document.getElementById("root") : null;
      const root = rootElement && getReactRoot(rootElement);
      const current = root && findInReactTree(root, (node: unknown) => !!node && typeof node === "object" &&
        (node as { elementType?: unknown }).elementType === memo);
      const patch = afterPatch(memo, "type", (_args, tree) => guard("qam", tree, qamEntry));
      const attachment = attachMountedRenderer(current, memo, original, memo.type);
      handles.add(() => {
        // Unpatch the shared chain first; restore only references we still own.
        try { patch.unpatch(); } finally { attachment.restore(memo.type); }
      });
      available.add("qam");
      if (attachment.count) {
        mountedEntries.add(signature);
        runtime.log("info", "qam-renderer-attached", { count: attachment.count });
      }
      runtime.status("qam", mountedEntries.size ? "ready; reopen Quick Access" : "pending native mount; hardware gate");
    } catch (error) { fault("qam", error); }
  }
  try {
    if (settingsType && settingsRowType) {
      const patch = routerHook.addPatch("/settings", route => {
        if (!active || failed.has("settings")) return route;
        try {
          const children = route.children;
          if (!element(children)) { runtime.status("settings", "unsupported route; native order"); return route; }
          return { ...route, children: wrap(children, "settings", "settings-entry", settingsEntry) as ReactElement };
        } catch (error) { fault("settings", error); return route; }
      });
      handles.add(() => routerHook.removePatch("/settings", patch));
      available.add("settings");
      runtime.status("settings", "ready; reopen Bluetooth Settings");
    } else runtime.status("settings", "adapter unavailable; native order");
  } catch (error) { fault("settings", error); }

  const unsubscribe = runtime.subscribe(() => {
    for (const surface of ["qam", "settings"] as const) {
      const value = runtime.getSnapshot().settings[surface];
      if (value === enabled[surface]) continue;
      enabled[surface] = value;
      if (!value) {
        failed.delete(surface);
        runtime.status(surface, "off");
      } else runtime.status(surface, !available.has(surface) ? "adapter unavailable; native order" :
        attached.has(surface) ? "ready" : surface === "qam" ?
          mountedEntries.size ? "ready; reopen Quick Access" : "pending native mount; hardware gate" : "ready; reopen native menu");
    }
  });
  for (const surface of ["qam", "settings"] as const) if (!enabled[surface]) runtime.status(surface, "off");
  return () => {
    if (!active) return;
    stoppedSnapshot = { ...runtime.getSnapshot() };
    active = false;
    unsubscribe();
    // Retained mounted wrappers become passthrough; do not force a React root reset.
    for (const listener of wake) listener();
    for (const unpatch of [...handles].reverse()) {
      try { unpatch(); } catch { runtime.log("warn", "native-unpatch-failed"); }
    }
    handles.clear();
  };
}