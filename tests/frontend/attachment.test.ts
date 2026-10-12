import test from "node:test";
import assert from "node:assert/strict";
// Import only the pure patch utility, not the Steam-bound UI entrypoint.
import { afterPatch } from "@decky/ui/dist/utils/patcher.js";
import { attachMountedRenderer } from "../../src/attachment.ts";
import type { MountedRenderer } from "../../src/attachment.ts";

test("updates only matching renderer references and restores both once", () => {
  const original = () => {}; const patched = () => {}; const memo = {};
  const alternate = { type: original, elementType: memo };
  const node = { type: original, elementType: memo, alternate, child: { untouched: true } };
  const child = node.child;
  const attachment = attachMountedRenderer(node, memo, original, patched);
  assert.equal(attachment.count, 2); assert.equal(node.type, patched); assert.equal(alternate.type, patched);
  assert.equal(node.child, child);
  attachment.restore(); attachment.restore();
  assert.equal(node.type, original); assert.equal(alternate.type, original);
});

test("mismatched and read-only alternates cause no writes on either side", () => {
  const original = () => {}; const patched = () => {}; const memo = {};
  for (const alternate of [
    { type: original, elementType: {} },
    Object.freeze({ type: original, elementType: memo }),
    { type: () => {}, elementType: memo },
  ]) {
    const node = { type: original, elementType: memo, alternate };
    assert.equal(attachMountedRenderer(node, memo, original, patched).count, 0);
    assert.equal(node.type, original);
  }
});

test("cleanup does not clobber later renderer changes", () => {
  const original = () => {}; const patched = () => {}; const later = () => {}; const memo = {};
  const node = { type: original, elementType: memo };
  const attachment = attachMountedRenderer(node, memo, original, patched);
  node.type = later; attachment.restore(); assert.equal(node.type, later);
});

test("cleanup retains the currently shared stacked-patch renderer", () => {
  const original = () => {}; const patched = () => {}; const remaining = () => {}; const memo = {};
  const node = { type: original, elementType: memo };
  const attachment = attachMountedRenderer(node, memo, original, patched);
  attachment.restore(remaining); assert.equal(node.type, remaining);
});

test("cleanup restores an alternate created after attachment to the current renderer only once", () => {
  const original = () => {}; const patched = () => {}; const remaining = () => {}; const memo = {};
  const node: MountedRenderer = { type: original, elementType: memo };
  const attachment = attachMountedRenderer(node, memo, original, patched);
  assert.equal(attachment.count, 1);
  const alternate = { type: patched, elementType: memo, child: { untouched: true } };
  const child = alternate.child;
  node.alternate = alternate;
  attachment.restore(remaining);
  assert.equal(node.type, remaining); assert.equal(alternate.type, remaining);
  assert.equal(alternate.child, child);
  node.type = patched; alternate.type = patched;
  attachment.restore(original);
  assert.equal(node.type, patched); assert.equal(alternate.type, patched);
});

test("cleanup leaves mismatched, frozen and newer alternates created after attachment untouched", () => {
  const original = () => {}; const patched = () => {}; const later = () => {}; const memo = {};
  for (const alternate of [
    { type: patched, elementType: {} },
    Object.freeze({ type: patched, elementType: memo }),
    { type: later, elementType: memo },
  ]) {
    const node: MountedRenderer = { type: original, elementType: memo };
    const attachment = attachMountedRenderer(node, memo, original, patched);
    const alternateType = alternate.type;
    node.alternate = alternate;
    node.type = later;
    attachment.restore();
    assert.equal(node.type, later); assert.equal(alternate.type, alternateType);
  }
});

test("real shared patch stacking preserves the remaining renderer on a late alternate", t => {
  t.mock.method(console, "debug", () => {});
  const original = () => "native";
  const memo = { type: original };
  const before = afterPatch(memo, "type", (_args, value) => `${value}:before`);
  const node: MountedRenderer = { type: memo.type, elementType: memo };
  const own = afterPatch(memo, "type", (_args, value) => `${value}:own`);
  const attachment = attachMountedRenderer(node, memo, before.patchedFunction, memo.type);
  const alternate = { type: memo.type, elementType: memo };
  node.alternate = alternate;
  const later = afterPatch(memo, "type", (_args, value) => `${value}:later`);
  const remaining = memo.type;
  node.type = remaining;
  try {
    own.unpatch();
    attachment.restore(memo.type);
    assert.equal(memo.type, remaining);
    assert.equal(node.type, remaining); assert.equal(alternate.type, remaining);
    assert.equal(memo.type(), "native:before:later");
  } finally {
    if (!own.hasUnpatched) own.unpatch();
    later.unpatch(); before.unpatch();
  }
  assert.equal(memo.type, original);
});

test("missing or unknown targets are passthrough", () => {
  const fn = () => {};
  for (const node of [null, undefined, {}, { elementType: {}, type: fn }]) {
    assert.equal(attachMountedRenderer(node, {}, fn, () => {}).count, 0);
  }
});