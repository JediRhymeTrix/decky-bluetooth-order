import { initialSnapshot, normalizeMac, orderRows, validateSnapshot } from "./model.js";
import type { DeviceInfo, LogFields, LogLevel, Settings, Snapshot, Surface } from "./model.js";

export interface Diagnostic {
  time: number;
  level: LogLevel;
  event: string;
  fields: LogFields;
}

export interface Backend {
  getSnapshot(): Promise<unknown>;
  setSettings(settings: Partial<Settings>): Promise<unknown>;
  resetHistory(): Promise<unknown>;
  logDiagnostics(entries: Diagnostic[]): Promise<void>;
  exportDiagnostics(): Promise<string>;
}

export interface QueryClient {
  getQueryData(key: readonly unknown[]): unknown;
  getQueryCache(): {
    subscribe(callback: (event: { type?: string; query?: { queryKey?: readonly unknown[] } }) => void): () => void;
  };
}

const deviceKey = (id: string | number) => ["BluetoothManagerService", "State", "Device", Number(id)];

async function backendResponse<T>(request: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([request, new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error("Backend response timed out")), 5000);
    })]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export class Runtime {
  private snapshot = initialSnapshot();
  private listeners = new Set<() => void>();
  private statusListeners = new Set<() => void>();
  private queryClient?: QueryClient;
  private unsubscribeCache?: () => void;
  private pendingLogs: Diagnostic[] = [];
  private logTimer?: ReturnType<typeof setTimeout>;
  private lastEvents = new Map<string, number>();
  private faults = new Set<Surface>();
  private stopped = false;
  private cacheScheduled = false;
  private statusScheduled = false;
  private flushingLogs: Promise<void> = Promise.resolve();
  private operation: Promise<unknown> = Promise.resolve();
  statuses: Readonly<Record<Surface, string>> = { qam: "off", settings: "off" };

  private backend: Backend;

  constructor(backend: Backend) { this.backend = backend; }

  getSnapshot = (): Snapshot => this.snapshot;
  isStopped = (): boolean => this.stopped;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getStatuses = () => this.statuses;
  subscribeStatus = (listener: () => void): (() => void) => {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  };

  accept(value: unknown, command = false): void {
    if (this.stopped) return;
    try {
      const next = validateSnapshot(value);
      if (next.revision < this.snapshot.revision) return;
      this.snapshot = next;
      this.publish();
    } catch (error) {
      this.log("error", "snapshot-rejected");
      if (command) throw error;
    }
  }

  async start(): Promise<void> {
    this.log("info", "frontend-start");
    try { this.accept(await backendResponse(this.backend.getSnapshot())); }
    catch { this.log("error", "backend-unavailable"); }
  }

  private publish(): void {
    if (this.stopped) return;
    for (const listener of this.listeners) listener();
  }

  setQueryClient(client: QueryClient): void {
    if (this.stopped || this.queryClient === client) return;
    this.unsubscribeCache?.();
    this.queryClient = client;
    this.unsubscribeCache = client.getQueryCache().subscribe(event => {
      // Native selectors change on render; observer bookkeeping is not device data.
      if (!["updated", "removed"].includes(event.type ?? "") || this.stopped || this.cacheScheduled ||
          event.query?.queryKey?.[0] !== "BluetoothManagerService") return;
      this.cacheScheduled = true;
      queueMicrotask(() => {
        this.cacheScheduled = false;
        if (this.stopped) return;
        this.snapshot = { ...this.snapshot };
        this.publish();
      });
    });
    this.log("info", "native-cache-attached");
  }

  resolveDevice(id: string | number): DeviceInfo | undefined {
    if (!this.queryClient || !Number.isSafeInteger(Number(id)) || Number(id) < 0) return;
    const cached = this.queryClient.getQueryData(deviceKey(id));
    if (!cached || typeof cached !== "object") return;
    const d = cached as Record<string, unknown>;
    const mac = normalizeMac(d.mac);
    if (!mac || typeof d.name !== "string" || typeof d.is_connected !== "boolean") return;
    return {
      id: String(id), mac, name: d.name,
      connected: this.snapshot.devices[mac]?.connected ?? d.is_connected,
      operationPending: d.operation_in_progress === true || d.is_pairing === true,
    };
  }

  sortRows<T>(rows: readonly T[], identify: (row: T) => string | number | undefined): readonly T[] {
    return orderRows(rows, identify, id => this.resolveDevice(id), this.snapshot);
  }

  shouldSort(surface: Surface): boolean {
    return !this.stopped && !this.faults.has(surface) && this.snapshot.settings[surface] && this.snapshot.settings.mode !== "native";
  }

  status(surface: Surface, value: string): void {
    if (this.statuses[surface] === value) return;
    this.statuses = { ...this.statuses, [surface]: value };
    this.log("info", "surface-status", { surface, status: value });
    if (this.statusScheduled || this.stopped) return;
    this.statusScheduled = true;
    queueMicrotask(() => {
      this.statusScheduled = false;
      if (this.stopped) return;
      // Diagnostics must not rerender native groups that report competing statuses.
      for (const listener of this.statusListeners) listener();
    });
  }

  onFault(surface: Surface, event: string, _error?: unknown): void {
    if (this.faults.has(surface)) return;
    this.faults.add(surface);
    this.status(surface, "disabled after error");
    this.log("error", event, { surface });
    queueMicrotask(() => {
      if (this.stopped) return;
      this.snapshot = { ...this.snapshot };
      this.publish();
    });
  }

  async updateSettings(settings: Partial<Settings>): Promise<void> {
    this.operation = this.operation.catch(() => undefined).then(async () => {
      if (this.stopped) return;
      const response = await backendResponse(this.backend.setSettings(settings));
      if (this.stopped) return;
      this.accept(response, true);
      if (settings.qam === false) this.faults.delete("qam");
      if (settings.settings === false) this.faults.delete("settings");
      this.log("info", "settings-updated");
    });
    await this.operation;
  }

  async resetHistory(): Promise<void> {
    if (this.stopped) return;
    this.accept(await backendResponse(this.backend.resetHistory()), true);
    this.log("info", "history-reset");
  }

  log(level: LogLevel, event: string, fields: LogFields = {}): void {
    if (this.stopped || (level === "debug" && !this.snapshot.settings.debug)) return;
    const now = Date.now();
    if (now - (this.lastEvents.get(event) ?? 0) < 2000) return;
    if (this.lastEvents.size >= 128) this.lastEvents.clear();
    this.lastEvents.set(event, now);
    // Diagnostics only accept short categorical metadata; never arbitrary state or errors.
    const safe: LogFields = {};
    for (const [key, value] of Object.entries(fields).slice(0, 12)) {
      if (/mac|address|name|token|secret|password/i.test(key)) continue;
      if (typeof value === "string") {
        if (value.length > 100 || /(?:[0-9a-f]{2}:){5}[0-9a-f]{2}/i.test(value)) continue;
        safe[key] = value;
      } else if (value === null || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) {
        safe[key] = value;
      }
    }
    const entry = { time: now, level, event, fields: safe };
    this.pendingLogs = [...this.pendingLogs.slice(-31), entry];
    const output = level === "error" ? console.error : level === "warn" ? console.warn : console.info;
    output("[Bluetooth Order]", event, safe);
    if (!this.logTimer) this.logTimer = setTimeout(() => void this.flushLogs(), 1000);
  }

  private async flushLogs(): Promise<void> {
    if (this.logTimer) clearTimeout(this.logTimer);
    this.logTimer = undefined;
    const entries = this.pendingLogs.splice(0);
    if (entries.length) {
      this.flushingLogs = this.flushingLogs.then(async () => {
        try { await backendResponse(this.backend.logDiagnostics(entries)); }
        catch { console.warn("[Bluetooth Order] diagnostic batch could not be persisted"); }
      });
    }
    await this.flushingLogs;
  }

  async exportDiagnostics(): Promise<string> {
    await this.flushLogs();
    return backendResponse(this.backend.exportDiagnostics());
  }

  dispose(): void {
    if (this.stopped) return;
    this.log("info", "frontend-stop");
    this.stopped = true;
    this.snapshot = { ...this.snapshot, settings: { ...this.snapshot.settings, qam: false, settings: false } };
    // Notify mounted wrappers before detaching; inactive wrappers remain passthrough.
    for (const listener of this.listeners) listener();
    this.unsubscribeCache?.();
    this.unsubscribeCache = undefined;
    this.listeners.clear();
    this.statusListeners.clear();
    void this.flushLogs();
  }
}