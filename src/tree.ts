/** Structural helpers: no React execution, native state writes, or element mutation. */
export interface NativeElement {
  type: unknown;
  key?: string | number | null;
  props: Record<string, unknown> & { children?: unknown };
}

export type Clone = (element: NativeElement, props: Record<string, unknown>) => NativeElement;
export type DeviceId = string | number;

export function element(value: unknown): value is NativeElement {
  return !!value && typeof value === "object" && "type" in value &&
    !!(value as NativeElement).props && typeof (value as NativeElement).props === "object";
}

export function deviceId(value: unknown): DeviceId | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value :
    typeof value === "string" && value.length > 0 && value.length < 100 ? value : undefined;
}

/** Copy only the children path to the target; portals have children outside props. */
export function replaceTree(tree: unknown, target: unknown, replacement: unknown, clone: Clone): unknown {
  if (tree === target) return replacement;
  if (Array.isArray(tree)) {
    const next = tree.map(child => replaceTree(child, target, replacement, clone));
    return next.some((child, i) => child !== tree[i]) ? next : tree;
  }
  if (element(tree)) {
    const children = replaceTree(tree.props.children, target, replacement, clone);
    return children === tree.props.children ? tree : clone(tree, { children });
  }
  if (tree && typeof tree === "object" && "children" in tree && "containerInfo" in tree) {
    const portal = tree as { children: unknown };
    const children = replaceTree(portal.children, target, replacement, clone);
    return children === portal.children ? tree : { ...tree, children };
  }
  return tree;
}

export interface RowGroup {
  rows: readonly NativeElement[];
  identify(row: NativeElement): DeviceId | undefined;
  replace(rows: readonly NativeElement[], clone: Clone): unknown;
}

function keyed(row: unknown): row is NativeElement {
  return element(row) && row.key !== null && row.key !== undefined;
}

/** Current Steam QAM Bluetooth group: toggle, paired array, Add Device, separator. */
export function qamRows(tree: unknown): RowGroup | undefined {
  if (!element(tree) || !Array.isArray(tree.props.children) || tree.props.children.length !== 4) return;
  const children = tree.props.children;
  if (!element(children[0]) || !element(children[3]) ||
      !(children[2] === false || element(children[2]))) return;
  if (children[1] === false) return { rows: [], identify: () => undefined, replace: () => tree };
  if (!Array.isArray(children[1])) return;
  const rows: unknown[] = children[1];
  const identify = (row: NativeElement): DeviceId | undefined => {
    const child = row.props.children;
    if (!element(child)) return;
    const device = child.props.device;
    if (!device || typeof device !== "object") return;
    return deviceId((device as { id?: unknown }).id);
  };
  if (!rows.every(row => {
    if (!keyed(row) || !element(row.props.children) || identify(row) === undefined) return false;
    const device = row.props.children.props.device as Record<string, unknown>;
    return device.is_paired === true || device.is_connected === true;
  }) || new Set(rows.map(row => String(identify(row as NativeElement)))).size !== rows.length) return;
  return {
    rows: rows as NativeElement[], identify,
    replace: (ordered, clone) => ordered === rows ? tree :
      clone(tree, { children: [children[0], ordered, children[2], children[3]] }),
  };
}

/** Settings: the labelled paired section only; the available section is untouched. */
export function settingsRows(tree: unknown, rowType: unknown): RowGroup | undefined {
  if (!rowType || !element(tree) || !Array.isArray(tree.props.children) || tree.props.children.length !== 3) return;
  const children = tree.props.children;
  if (!element(children[0]) || !(children[2] === false || element(children[2]))) return;
  const section = children[1];
  if (section === false) return { rows: [], identify: () => undefined, replace: () => tree };
  if (!element(section) || typeof section.props.label !== "string" || !Array.isArray(section.props.children)) return;
  const rows: unknown[] = section.props.children;
  const identify = (row: NativeElement) => deviceId(row.props.nDeviceId);
  if (!rows.every(row => keyed(row) && row.type === rowType && identify(row) !== undefined) ||
      new Set(rows.map(row => String(identify(row as NativeElement)))).size !== rows.length) return;
  return {
    rows: rows as NativeElement[], identify,
    replace: (ordered, clone) => ordered === rows ? tree : clone(tree, {
      children: [children[0], clone(section, { children: ordered }), children[2]],
    }),
  };
}

/** While interacting, retain known row positions; new/unmapped rows keep native slots. */
export function retainOrder<T>(rows: readonly T[], identify: (row: T) => DeviceId | undefined,
  order: readonly string[]): readonly T[] {
  const rank = new Map(order.map((id, index) => [id, index]));
  const slots = rows.flatMap((row, index) => {
    const id = identify(row);
    return id !== undefined && rank.has(String(id)) ? [{ row, index, rank: rank.get(String(id))! }] : [];
  });
  const sorted = [...slots].sort((a, b) => a.rank - b.rank);
  if (sorted.every((entry, i) => entry.row === slots[i].row)) return rows;
  const result = [...rows];
  slots.forEach((entry, i) => { result[entry.index] = sorted[i].row; });
  return result;
}