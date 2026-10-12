export type SortMode = "native" | "recent" | "az" | "za";
export type Surface = "qam" | "settings";
export type LogLevel = "debug" | "info" | "warn" | "error";
export type LogFields = Record<string, string | number | boolean | null>;

export interface Settings {
  mode: SortMode;
  connectedFirst: boolean;
  qam: boolean;
  settings: boolean;
  debug: boolean;
}

export interface ConnectionTime {
  timestamp: number;
  sequence: number;
}

export interface Snapshot {
  settings: Settings;
  history: Record<string, ConnectionTime>;
  devices: Record<string, { connected: boolean; paired: boolean }>;
  observerStatus: string;
  revision: number;
}

export interface DeviceInfo {
  id: string;
  mac: string;
  name: string;
  connected: boolean;
  operationPending: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  mode: "recent",
  connectedFirst: true,
  qam: false,
  settings: false,
  debug: false,
};

export function initialSnapshot(): Snapshot {
  return {
    settings: { ...DEFAULT_SETTINGS },
    history: {},
    devices: {},
    observerStatus: "starting",
    revision: 0,
  };
}

export function normalizeMac(value: unknown): string | undefined {
  if (typeof value !== "string" || !/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(value)) {
    return undefined;
  }
  return value.toUpperCase();
}

export function validateSnapshot(value: unknown): Snapshot {
  if (!value || typeof value !== "object") throw new Error("Invalid backend snapshot");
  const data = value as Snapshot;
  if (!data.settings || !["native", "recent", "az", "za"].includes(data.settings.mode)) {
    throw new Error("Invalid sort mode");
  }
  for (const key of ["qam", "settings", "connectedFirst", "debug"] as const) {
    if (typeof data.settings[key] !== "boolean") throw new Error("Invalid settings");
  }
  if (!data.history || typeof data.history !== "object" || Array.isArray(data.history) ||
      !data.devices || typeof data.devices !== "object" || Array.isArray(data.devices) ||
      !Number.isSafeInteger(data.revision) || data.revision < 0 ||
      typeof data.observerStatus !== "string" || data.observerStatus.length > 200) {
    throw new Error("Invalid snapshot fields");
  }
  const history: Snapshot["history"] = {};
  const devices: Snapshot["devices"] = {};
  if (Object.keys(data.history).length > 512 || Object.keys(data.devices).length > 512) {
    throw new Error("Device limit exceeded");
  }
  for (const [key, time] of Object.entries(data.history)) {
    const mac = normalizeMac(key);
    if (!mac || !time || !Number.isSafeInteger(time.timestamp) || time.timestamp < 0 ||
        !Number.isSafeInteger(time.sequence) || time.sequence < 0) {
      throw new Error("Invalid connection history");
    }
    if (mac in history) throw new Error("Duplicate connection identity");
    history[mac] = { timestamp: time.timestamp, sequence: time.sequence };
  }
  for (const [key, device] of Object.entries(data.devices)) {
    const mac = normalizeMac(key);
    if (!mac || !device || typeof device.connected !== "boolean" || typeof device.paired !== "boolean") {
      throw new Error("Invalid device state");
    }
    if (mac in devices) throw new Error("Duplicate device identity");
    devices[mac] = { connected: device.connected, paired: device.paired };
  }
  return {
    settings: { ...data.settings }, history, devices,
    observerStatus: data.observerStatus, revision: data.revision,
  };
}

/** Unmapped device slots stay in place; never guess identity from display names. */
export function orderRows<T>(
  rows: readonly T[],
  identify: (row: T) => string | number | undefined,
  resolve: (id: string | number) => DeviceInfo | undefined,
  snapshot: Snapshot,
): readonly T[] {
  if (snapshot.settings.mode === "native" || rows.length < 2) return rows;
  const known = rows.flatMap((row, index) => {
    const id = identify(row);
    const device = id === undefined ? undefined : resolve(id);
    return device ? [{ row, index, device }] : [];
  });
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const sorted = [...known].sort((a, b) => {
    if (snapshot.settings.connectedFirst && a.device.connected !== b.device.connected) {
      return a.device.connected ? -1 : 1;
    }
    if (snapshot.settings.mode === "recent") {
      const left = snapshot.history[a.device.mac];
      const right = snapshot.history[b.device.mac];
      if (!!left !== !!right) return left ? -1 : 1;
      if (left && right) {
        const recency = right.sequence - left.sequence || right.timestamp - left.timestamp;
        if (recency) return recency;
      }
    } else {
      const names = collator.compare(a.device.name, b.device.name);
      if (names) return snapshot.settings.mode === "za" ? -names : names;
    }
    return a.index - b.index;
  });
  if (sorted.every((entry, index) => entry.row === known[index].row)) return rows;
  const result = [...rows];
  known.forEach((entry, index) => { result[entry.index] = sorted[index].row; });
  return result;
}