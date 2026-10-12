import test from "node:test";
import assert from "node:assert/strict";
import { initialSnapshot, normalizeMac, orderRows, validateSnapshot } from "../../src/model.ts";
import type { DeviceInfo } from "../../src/model.ts";

const devices = new Map<string, DeviceInfo>([
  ["a", { id: "a", mac: "00:00:00:00:00:01", name: "Headset 2", connected: false, operationPending: false }],
  ["b", { id: "b", mac: "00:00:00:00:00:02", name: "Headset 10", connected: false, operationPending: false }],
  ["c", { id: "c", mac: "00:00:00:00:00:03", name: "Controller", connected: true, operationPending: false }],
]);
const resolve = (id: string | number) => devices.get(String(id));

test("recent sorting leaves unmapped slots in place and does not mutate input", () => {
  const rows = Object.freeze(["a", "unknown", "c", "b"]);
  const snapshot = initialSnapshot();
  snapshot.settings.connectedFirst = false;
  snapshot.history[devices.get("a")!.mac] = { timestamp: 100, sequence: 1 };
  snapshot.history[devices.get("b")!.mac] = { timestamp: 99, sequence: 2 };
  assert.deepEqual(orderRows(rows, x => x, resolve, snapshot), ["b", "unknown", "a", "c"]);
  assert.deepEqual(rows, ["a", "unknown", "c", "b"]);
});

test("native bypasses connected-first and returns original array", () => {
  const rows = ["a", "b", "c"];
  const snapshot = initialSnapshot(); snapshot.settings.mode = "native";
  assert.equal(orderRows(rows, x => x, resolve, snapshot), rows);
});

test("connected-first is optional and unknown/tied history stays stable", () => {
  const rows = ["a", "b", "c"];
  const snapshot = initialSnapshot();
  assert.deepEqual(orderRows(rows, x => x, resolve, snapshot), ["c", "a", "b"]);
  snapshot.settings.connectedFirst = false;
  assert.equal(orderRows(rows, x => x, resolve, snapshot), rows);
});

test("disconnect retains recorded recency while connected-first remains an explicit priority", () => {
  const snapshot = initialSnapshot(); snapshot.settings.connectedFirst = false;
  const local = new Map([...devices].map(([id, device]) => [id, { ...device }]));
  const resolveLocal = (id: string | number) => local.get(String(id));
  const rows = ["a", "b", "c"];
  snapshot.history[local.get("b")!.mac] = { timestamp: 100, sequence: 1 };
  local.get("b")!.connected = true;
  assert.deepEqual(orderRows(rows, x => x, resolveLocal, snapshot), ["b", "a", "c"]);
  local.get("b")!.connected = false;
  assert.deepEqual(orderRows(rows, x => x, resolveLocal, snapshot), ["b", "a", "c"]);
  snapshot.settings.connectedFirst = true;
  assert.deepEqual(orderRows(rows, x => x, resolveLocal, snapshot), ["c", "b", "a"]);
});

test("name sorting is numeric and reversible", () => {
  const snapshot = initialSnapshot(); snapshot.settings.mode = "az";
  snapshot.settings.connectedFirst = false;
  assert.deepEqual(orderRows(["b", "a", "c"], x => x, resolve, snapshot), ["c", "a", "b"]);
  snapshot.settings.mode = "za";
  assert.deepEqual(orderRows(["b", "a", "c"], x => x, resolve, snapshot), ["b", "a", "c"]);
});

test("empty and single lists retain references", () => {
  for (const rows of [[], ["a"]]) assert.equal(orderRows(rows, x => x, resolve, initialSnapshot()), rows);
});

test("backend trust boundary validates and copies history", () => {
  const original = initialSnapshot(); const result = validateSnapshot(original);
  assert.deepEqual(result, original); assert.notEqual(result.settings, original.settings);
  assert.throws(() => validateSnapshot({ ...original, revision: -1 }));
  assert.throws(() => validateSnapshot({ ...original, settings: { ...original.settings, mode: "anything" } }));
  assert.throws(() => validateSnapshot({ ...original, history: { bad: { timestamp: 1, sequence: 1 } } }));
  assert.equal(normalizeMac("aa:bb:cc:dd:ee:ff"), "AA:BB:CC:DD:EE:FF");
  assert.equal(normalizeMac("not-a-device"), undefined);
});

test("duplicate normalized addresses are rejected", () => {
  const snapshot = initialSnapshot();
  for (const field of ["history", "devices"] as const) {
    const entry = field === "history" ? { timestamp: 1, sequence: 1 } : { connected: false, paired: true };
    for (const keys of [["aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF"], ["AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"]]) {
      assert.throws(() => validateSnapshot({ ...snapshot, [field]: { [keys[0]]: entry, [keys[1]]: entry } }));
    }
  }
});

test("populated backend data is copied and invalid numbers rejected", () => {
  const snapshot = initialSnapshot(); const key = devices.get("a")!.mac;
  snapshot.history[key] = { timestamp: 4, sequence: 2 };
  snapshot.devices[key] = { connected: false, paired: true };
  const result = validateSnapshot(snapshot);
  snapshot.history[key].sequence = 8;
  snapshot.devices[key].connected = true;
  assert.equal(result.history[key].sequence, 2); assert.equal(result.devices[key].connected, false);
  assert.throws(() => validateSnapshot({ ...result, history: { [key]: { timestamp: NaN, sequence: 1 } } }));
});