import test from "node:test";
import assert from "node:assert/strict";
import { Runtime } from "../../src/runtime.ts";
import type { Backend, Diagnostic } from "../../src/runtime.ts";
import { initialSnapshot } from "../../src/model.ts";

function fakeBackend(): Backend {
  return {
    getSnapshot: async () => initialSnapshot(),
    setSettings: async change => ({ ...initialSnapshot(), settings: { ...initialSnapshot().settings, ...change } }),
    resetHistory: async () => initialSnapshot(),
    logDiagnostics: async () => undefined,
    exportDiagnostics: async () => "{}",
  };
}

test("cached native details are read without modifying cache or making requests", () => {
  const runtime = new Runtime(fakeBackend());
  const detail = Object.freeze({ id: 1, mac: "aa:bb:cc:dd:ee:ff", name: "Device", is_connected: false, strength_raw: 15 });
  let readKey: readonly unknown[] = [];
  let disposed = 0;
  runtime.setQueryClient({
    getQueryData: key => { readKey = key; return detail; },
    getQueryCache: () => ({ subscribe: () => () => { disposed++; } }),
  });
  assert.equal(runtime.resolveDevice(1)?.mac, "AA:BB:CC:DD:EE:FF");
  assert.deepEqual(readKey, ["BluetoothManagerService", "State", "Device", 1]);
  assert.equal(detail.strength_raw, 15);
  runtime.dispose(); runtime.dispose(); assert.equal(disposed, 1);
});

test("disposal publishes disabled immutable state and ignores late snapshots", () => {
  const runtime = new Runtime(fakeBackend());
  const value = initialSnapshot(); value.settings.qam = true; runtime.accept(value);
  let updates = 0; runtime.subscribe(() => updates++);
  const before = runtime.getSnapshot(); runtime.dispose();
  assert.equal(updates, 1); assert.notEqual(runtime.getSnapshot(), before);
  assert.equal(runtime.shouldSort("qam"), false);
  runtime.accept(value); assert.equal(updates, 1);
});

test("stale revisions cannot replace newer snapshot", () => {
  const runtime = new Runtime(fakeBackend());
  const newer = { ...initialSnapshot(), revision: 4 }; runtime.accept(newer);
  runtime.accept({ ...initialSnapshot(), revision: 3 });
  assert.equal(runtime.getSnapshot().revision, 4); runtime.dispose();
});

test("native mode always bypasses enabled adapter", () => {
  const runtime = new Runtime(fakeBackend());
  const value = initialSnapshot(); value.settings.qam = true; value.settings.mode = "native";
  runtime.accept(value); assert.equal(runtime.shouldSort("qam"), false); runtime.dispose();
});

test("diagnostics filter identifiers and send one bounded batch", async () => {
  const backend = fakeBackend(); const entries: Diagnostic[] = [];
  backend.logDiagnostics = async batch => { entries.push(...batch); };
  const runtime = new Runtime(backend);
  runtime.log("info", "shape-checked", { mac: "AA:BB:CC:DD:EE:FF", name: "Private", count: 2, unsafe: "AA:BB:CC:DD:EE:FF" });
  runtime.log("info", "shape-checked", { count: 3 });
  await runtime.exportDiagnostics();
  assert.equal(entries.length, 1); assert.deepEqual(entries[0].fields, { count: 2 });
  runtime.dispose();
});

test("queued settings cannot write after dispose", async () => {
  const backend = fakeBackend(); let writes = 0;
  backend.setSettings = async () => { writes++; return initialSnapshot(); };
  const runtime = new Runtime(backend);
  const pending = runtime.updateSettings({ qam: true }); runtime.dispose();
  await pending; assert.equal(writes, 0);
});

test("an unresponsive settings call times out without enabling QAM or blocking later commands", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const backend = fakeBackend(); let calls = 0; let release!: (snapshot: unknown) => void;
  backend.setSettings = async () => {
    if (++calls === 1) return new Promise(resolve => { release = resolve; });
    return initialSnapshot();
  };
  const runtime = new Runtime(backend);
  const pending = assert.rejects(runtime.updateSettings({ qam: true }), /Backend response timed out/);
  for (let i = 0; i < 8 && calls === 0; i++) await Promise.resolve();
  assert.equal(calls, 1); t.mock.timers.tick(5000); await pending;
  assert.equal(runtime.getSnapshot().settings.qam, false);
  const late = initialSnapshot(); late.settings.qam = true; release(late);
  await Promise.resolve(); assert.equal(runtime.getSnapshot().settings.qam, false);
  await runtime.updateSettings({ qam: false }); assert.equal(calls, 2);
  runtime.dispose();
});

test("an unresponsive startup backend settles with disabled defaults", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const backend = fakeBackend(); backend.getSnapshot = () => new Promise(() => {});
  const runtime = new Runtime(backend); const starting = runtime.start();
  t.mock.timers.tick(5000); await starting;
  assert.equal(runtime.shouldSort("qam"), false); assert.equal(runtime.shouldSort("settings"), false);
  runtime.dispose();
});

test("command responses reject malformed snapshots without false confirmation", async () => {
  const backend = fakeBackend(); backend.resetHistory = async () => ({});
  backend.setSettings = async () => ({});
  const runtime = new Runtime(backend);
  await assert.rejects(runtime.resetHistory());
  await assert.rejects(runtime.updateSettings({ qam: false }));
  assert.equal(runtime.getSnapshot().settings.qam, false); runtime.dispose();
});

test("status-only changes notify diagnostics without publishing native data snapshots", async () => {
  const runtime = new Runtime(fakeBackend()); const before = runtime.getSnapshot();
  let updates = 0; let statusUpdates = 0;
  runtime.subscribe(() => updates++);
  const unsubscribe = runtime.subscribeStatus(() => statusUpdates++);
  runtime.status("qam", "ready"); runtime.status("qam", "ready");
  assert.equal(updates, 0); assert.equal(statusUpdates, 0);
  await Promise.resolve(); assert.equal(updates, 0); assert.equal(statusUpdates, 1);
  assert.equal(runtime.getSnapshot(), before);
  assert.equal(runtime.getStatuses().qam, "ready");
  const statuses = runtime.getStatuses(); runtime.status("qam", "ready");
  assert.equal(runtime.getStatuses(), statuses);
  runtime.status("qam", "active"); runtime.status("qam", "ready");
  await Promise.resolve(); assert.equal(updates, 0); assert.equal(statusUpdates, 2);
  unsubscribe(); runtime.status("qam", "off"); runtime.dispose();
  await Promise.resolve(); assert.equal(statusUpdates, 2);
});

test("diagnostic export waits for an in-flight batch", async () => {
  const backend = fakeBackend(); let release!: () => void; let exported = false;
  backend.logDiagnostics = () => new Promise<void>(resolve => { release = resolve; });
  backend.exportDiagnostics = async () => { exported = true; return "{}"; };
  const runtime = new Runtime(backend); runtime.log("info", "test-export");
  const first = runtime.exportDiagnostics();
  await Promise.resolve();
  const second = runtime.exportDiagnostics();
  await Promise.resolve(); assert.equal(exported, false);
  release(); await Promise.all([first, second]); assert.equal(exported, true);
  backend.logDiagnostics = async () => undefined; runtime.dispose();
});

test("cache refresh coalesces and disposal cancels pending delivery", async () => {
  const runtime = new Runtime(fakeBackend());
  let callback!: (event: { type?: string; query?: { queryKey?: readonly unknown[] } }) => void;
  let updates = 0; runtime.subscribe(() => updates++);
  runtime.setQueryClient({ getQueryData: () => undefined, getQueryCache: () => ({ subscribe: fn => { callback = fn; return () => {}; } }) });
  callback({ type: "updated", query: { queryKey: ["BluetoothManagerService", "State"] } });
  callback({ type: "updated", query: { queryKey: ["BluetoothManagerService", "State"] } });
  await Promise.resolve(); assert.equal(updates, 1);
  callback({ type: "removed", query: { queryKey: ["BluetoothManagerService", "State"] } });
  runtime.dispose(); await Promise.resolve(); assert.equal(updates, 2);
});

test("observer bookkeeping cannot sustain a native render loop", async () => {
  const runtime = new Runtime(fakeBackend());
  let callback!: (event: { type?: string; query?: { queryKey?: readonly unknown[] } }) => void;
  let updates = 0;
  runtime.setQueryClient({ getQueryData: () => undefined, getQueryCache: () => ({ subscribe: fn => { callback = fn; return () => {}; } }) });
  runtime.subscribe(() => {
    updates++;
    callback({ type: "observerOptionsUpdated", query: { queryKey: ["BluetoothManagerService", "State"] } });
  });
  for (const type of ["observerAdded", "observerRemoved", "observerResultsUpdated", "observerOptionsUpdated"]) {
    callback({ type, query: { queryKey: ["BluetoothManagerService", "State"] } });
  }
  await Promise.resolve(); assert.equal(updates, 0);
  callback({ type: "updated", query: { queryKey: ["BluetoothManagerService", "State"] } });
  await Promise.resolve(); await Promise.resolve(); assert.equal(updates, 1);
  runtime.dispose();
});