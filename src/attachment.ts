/** Decky's scoped QAM attachment pattern; never resets the React root. */
export interface MountedRenderer {
  elementType: unknown;
  type: unknown;
  alternate?: MountedRenderer | null;
}

export interface Attachment {
  count: number;
  restore(currentRenderer?: unknown): void;
}

export function attachMountedRenderer(
  node: unknown, memo: unknown, original: unknown, patched: unknown,
): Attachment {
  const unchanged: Attachment = { count: 0, restore() {} };
  if (!node || typeof node !== "object" || typeof original !== "function" || typeof patched !== "function") return unchanged;
  const current = node as MountedRenderer;
  const nodes = current.alternate ? [current, current.alternate] : [current];
  // Verify both sides before writing either. Never change an unrelated or newer renderer.
  if (nodes.some(entry => !entry || entry.elementType !== memo || entry.type !== original ||
      Object.getOwnPropertyDescriptor(entry, "type")?.writable !== true)) return unchanged;
  for (const entry of nodes) entry.type = patched;
  let restored = false;
  return {
    count: nodes.length,
    restore(currentRenderer = original) {
      if (restored) return;
      restored = true;
      // React may create an alternate on the first update after attachment.
      for (const entry of new Set([...nodes, current.alternate])) {
        if (entry && entry.elementType === memo && entry.type === patched &&
            Object.getOwnPropertyDescriptor(entry, "type")?.writable === true) entry.type = currentRenderer;
      }
    },
  };
}