// Read-only cached discovery. No imports, module factories, patches or service calls.
const response = await fetch("http://127.0.0.1:8080/json", { signal: AbortSignal.timeout(5000) });
const targets = await response.json();
const target = targets.find(tab => tab.title === "SharedJSContext");
if (!target?.webSocketDebuggerUrl) throw new Error("SharedJSContext is unavailable");
const ws = new WebSocket(target.webSocketDebuggerUrl);
const result = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("Read-only UI probe timed out; no retries made")), 10000);
  ws.onerror = () => { clearTimeout(timer); reject(new Error("CDP connection failed")); };
  ws.onmessage = message => {
    const data = JSON.parse(message.data);
    if (data.id !== 1) return;
    clearTimeout(timer);
    if (data.result?.exceptionDetails || data.error) reject(new Error("Read-only probe rejected by Steam"));
    else resolve(data.result?.result?.value);
  };
  ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: {
    returnByValue: true,
    expression: `(() => {
      const ui = window.DFL;
      if (!ui?.findModuleByExport) return JSON.stringify({ available: false });
      const source = value => typeof value === 'function' ? String(value) : '';
      const cached = predicate => {
        const mod = ui.findModuleByExport(predicate);
        return mod && typeof mod === 'object' ? Object.values(mod).find(predicate) : undefined;
      };
      const entries = ['QuickAccessMenuBrowserView', 'QuickAccessMenuEmbedded'].map(signature => {
        const value = cached(entry => entry && typeof entry === 'object' && source(entry.type).includes(signature));
        const descriptor = value ? Object.getOwnPropertyDescriptor(value, 'type') : undefined;
        return { signature, found: !!value, writable: descriptor?.writable === true,
          memo: !!value?.$$typeof, type: typeof value?.type };
      });
      const group = cached(value => source(value).includes('#QuickAccess_Tab_Bluetooth_AddDevice'));
      const settings = cached(value => source(value).includes('#Settings_Title') && source(value).includes('pages:'));
      const client = cached(value => value && typeof value === 'object' &&
        typeof value.getQueryData === 'function' && typeof value.getQueryCache === 'function' &&
        typeof value.invalidateQueries === 'function');
      const rootElement = document.getElementById('root');
      const root = rootElement && ui.getReactRoot?.(rootElement);
      const candidates = [];
      for (const signature of ['QuickAccessMenuBrowserView', 'QuickAccessMenuEmbedded']) {
        const memo = cached(entry => entry && typeof entry === 'object' && source(entry.type).includes(signature));
        const fiber = root && memo && ui.findInReactTree?.(root, node => node?.elementType === memo);
        const alternate = fiber?.alternate;
        const field = fiber ? Object.getOwnPropertyDescriptor(fiber, 'type') : undefined;
        candidates.push({ signature, mounted: !!fiber, writable: field?.writable === true,
          matchesCurrentType: !!fiber && fiber.type === memo.type,
          alternate: !!alternate, alternateMatches: !!alternate && alternate.elementType === memo });
      }
      return JSON.stringify({ available: true, settingsTabKey: ui.QuickAccessTab?.Settings,
        entries, bluetoothGroup: typeof group, settings: typeof settings,
        cachedQueryClient: !!client, rootFound: !!root, mountedEntries: candidates,
        installedPlugin: (window.DeckyPluginLoader?.plugins || []).some(p => p.name === 'Bluetooth Order') });
    })()`
  } }));
}).finally(() => ws.close());
console.log(result);