import test from "node:test";
import assert from "node:assert/strict";
import { createElement, cloneElement } from "react";
import { qamRows, settingsRows, replaceTree, retainOrder } from "../../src/tree.ts";
import type { Clone, NativeElement } from "../../src/tree.ts";

const clone: Clone = (node, props) => cloneElement(node as any, props) as unknown as NativeElement;
const node = (type: any, props: Record<string, unknown> = {}) => createElement(type, props) as unknown as NativeElement;
const Row = () => null;
const Wrapper = () => null;
const controls = Object.freeze({ toggle: node("toggle"), add: node("add"), separator: node("separator") });
const qamRow = (id: number, paired = true) => node(Wrapper, { key: id, children: node(Row, {
  device: Object.freeze({ id, is_paired: paired, is_connected: false }),
  onActivate: () => id, bottomSeparator: "none",
}) });
const settingsRow = (id: number) => node(Row, { key: id, nDeviceId: id, onActivate: () => id, bottomSeparator: "standard" });

test("QAM copies only paired container, retaining keys, props, actions and controls", () => {
  const rows = Object.freeze([qamRow(2), qamRow(1)]);
  const children = Object.freeze([controls.toggle, rows, controls.add, controls.separator]);
  const original = node("group", { children, nativeFocus: () => true });
  const group = qamRows(original)!;
  assert.deepEqual(group.rows.map(group.identify), [2, 1]);
  const sorted = group.replace([rows[1], rows[0]], clone) as NativeElement;
  assert.notEqual(sorted, original);
  assert.equal(sorted.key, original.key);
  assert.equal(sorted.props.nativeFocus, original.props.nativeFocus);
  const changed = sorted.props.children as any[];
  assert.equal(changed[0], children[0]); assert.equal(changed[2], children[2]); assert.equal(changed[3], children[3]);
  assert.equal(changed[1][0], rows[1]); assert.equal(changed[1][1], rows[0]);
  assert.equal(original.props.children, children);
  assert.equal(group.replace(rows, clone), original);
});

test("Settings reorders only exact native paired section and never available devices", () => {
  const rows = Object.freeze([settingsRow(2), settingsRow(1)]);
  const availableRows = Object.freeze([settingsRow(9), settingsRow(8)]);
  const availableChildren = Object.freeze([node("header"), availableRows]);
  const available = node("available", { children: availableChildren });
  const paired = node("paired", { label: "Native paired label", children: rows, nativeFocus: () => true });
  const original = node("settings", { children: Object.freeze([controls.toggle, paired, available]) });
  const group = settingsRows(original, Row)!;
  const result = group.replace([rows[1], rows[0]], clone) as NativeElement;
  const children = result.props.children as NativeElement[];
  assert.equal(children[0], controls.toggle); assert.equal(children[2], available);
  assert.equal(children[1].props.label, paired.props.label);
  assert.equal(children[1].props.nativeFocus, paired.props.nativeFocus);
  assert.deepEqual((children[1].props.children as NativeElement[]).map(group.identify), [1, 2]);
  assert.equal(available.props.children, availableChildren);
  assert.equal((original.props.children as NativeElement[])[1], paired);
});

test("empty/disabled and all-paired layouts are recognized without creating a list", () => {
  const qam = node("group", { children: [controls.toggle, false, false, controls.separator] });
  assert.equal(qamRows(qam)!.replace([], clone), qam);
  const empty = node("settings", { children: [controls.toggle, false, false] });
  assert.equal(settingsRows(empty, Row)!.replace([], clone), empty);
  const onlyPaired = node("settings", { children: [controls.toggle, node("paired", { label: "Paired", children: [settingsRow(1)] }), false] });
  assert.equal(settingsRows(onlyPaired, Row)!.rows.length, 1);
});

test("unknown shapes, unpaired QAM rows, duplicate IDs and wrong row type fail open", () => {
  assert.equal(qamRows(null), undefined);
  assert.equal(qamRows(node("unknown", { children: [[qamRow(1)]] })), undefined);
  assert.equal(qamRows(node("group", { children: [controls.toggle, [qamRow(1, false)], controls.add, controls.separator] })), undefined);
  assert.equal(qamRows(node("group", { children: [controls.toggle, [qamRow(1), qamRow(1)], controls.add, controls.separator] })), undefined);
  const original = node("settings", { children: [controls.toggle, node("paired", { label: "Paired", children: [settingsRow(1)] }), false] });
  assert.equal(settingsRows(original, Wrapper), undefined);
  assert.equal(settingsRows(original, undefined), undefined);
  const malformed = node("settings", { children: [controls.toggle, node("paired", { children: [settingsRow(1)] }), false] });
  assert.equal(settingsRows(malformed, Row), undefined);
});

test("immutable children and portal paths are cloned without touching sibling controls", () => {
  const target = node(Row, { key: "native", nativeHandler: () => true });
  const replacement = clone(target, { marker: true });
  const portal = Object.freeze({ $$typeof: Symbol.for("react.portal"), key: "portal", containerInfo: {}, children: target });
  const original = node("root", { children: Object.freeze([controls.toggle, portal]) });
  const result = replaceTree(original, target, replacement, clone) as NativeElement;
  const children = result.props.children as any[];
  assert.equal(children[0], controls.toggle); assert.notEqual(children[1], portal);
  assert.equal(children[1].containerInfo, portal.containerInfo); assert.equal(children[1].children, replacement);
  assert.equal(portal.children, target); assert.equal(replacement.props.nativeHandler, target.props.nativeHandler);
  assert.equal(replaceTree(original, node("missing"), replacement, clone), original);
});

test("interaction freeze restores known order while new and unmapped slots stay native", () => {
  const rows = Object.freeze([3, "unknown", 2, 1, 4]);
  assert.deepEqual(retainOrder(rows, x => x, ["1", "2", "3"]), [1, "unknown", 2, 3, 4]);
  const stable = Object.freeze([1, 2]); assert.equal(retainOrder(stable, x => x, ["1", "2"]), stable);
});