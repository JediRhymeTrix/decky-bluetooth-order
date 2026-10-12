import { addEventListener, callable, definePlugin, removeEventListener } from "@decky/api";
import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField, staticClasses } from "@decky/ui";
import { useState, useSyncExternalStore } from "react";
import type { Diagnostic } from "./runtime";
import { Runtime } from "./runtime";
import type { Settings, Snapshot, SortMode } from "./model";
import { installNative } from "./native";

const getSnapshot = callable<[], unknown>("get_snapshot");
const setSettings = callable<[Partial<Settings>], unknown>("set_settings");
const resetHistory = callable<[], unknown>("reset_history");
const logDiagnostics = callable<[Diagnostic[]], void>("log_diagnostics");
const exportDiagnostics = callable<[], string>("export_diagnostics");

function Content({ runtime }: { runtime: Runtime }) {
  const snapshot = useSyncExternalStore(runtime.subscribe, runtime.getSnapshot);
  const statuses = useSyncExternalStore(runtime.subscribeStatus, runtime.getStatuses);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [report, setReport] = useState("");
  const unloaded = runtime.isStopped();
  const disabled = busy || unloaded;
  const action = async (callback: () => Promise<void>) => {
    if (disabled) return;
    setBusy(true); setMessage("");
    try { await callback(); }
    catch { setMessage("The request failed. Both menus keep their last confirmed settings. Check diagnostics."); }
    finally { setBusy(false); }
  };
  const change = (settings: Partial<Settings>) => void action(() => runtime.updateSettings(settings));
  return <>
    {unloaded && <PanelSection title="Plugin reloaded">
      <PanelSectionRow><div>Go back to Decky’s plugin list and open Bluetooth Order again. This old panel is inactive.</div></PanelSectionRow>
    </PanelSection>}
    <PanelSection title="Native Bluetooth menus">
      <PanelSectionRow>
        <DropdownItem label="Sort order" disabled={disabled} selectedOption={snapshot.settings.mode}
          rgOptions={[
            { data: "native", label: "Native (unchanged)" },
            { data: "recent", label: "Most recently connected" },
            { data: "az", label: "Name A–Z" },
            { data: "za", label: "Name Z–A" },
          ]} onChange={choice => change({ mode: choice.data as SortMode })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Connected devices first" disabled={disabled || snapshot.settings.mode === "native"}
          checked={snapshot.settings.connectedFirst} onChange={connectedFirst => change({ connectedFirst })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Apply to QAM Bluetooth" disabled={disabled} checked={snapshot.settings.qam}
          onChange={qam => change({ qam })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Apply to Settings Bluetooth" disabled={disabled} checked={snapshot.settings.settings}
          onChange={settings => change({ settings })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: 12, color: "#adb5bd" }}>
          Reorders Steam’s existing paired-device rows only. Overrides start off.
          Reopen the native menu after first enabling its adapter. QAM uses Decky’s scoped
          renderer attachment without resetting the UI. Connection history begins with observed successful connections;
          older or unmapped devices retain native order.
        </div>
      </PanelSectionRow>
      <PanelSectionRow><ButtonItem disabled={disabled} onClick={() => change({ qam: false, settings: false })}>
        Restore both native menus
      </ButtonItem></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Diagnostics">
      <PanelSectionRow><div style={{ fontSize: 12 }}>
        Observer: {snapshot.observerStatus}<br />
        Recorded connections: {Object.keys(snapshot.history).length}<br />
        QAM: {statuses.qam}<br />Settings: {statuses.settings}
      </div></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Detailed logging" disabled={disabled} checked={snapshot.settings.debug}
        onChange={debug => change({ debug })} /></PanelSectionRow>
      <PanelSectionRow><ButtonItem disabled={disabled} onClick={() => void action(async () => {
        setReport(await runtime.exportDiagnostics());
      })}>Show redacted diagnostic report</ButtonItem></PanelSectionRow>
      {report && <PanelSectionRow><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 10 }}>{report}</pre></PanelSectionRow>}
      <PanelSectionRow><ButtonItem disabled={disabled} onClick={() => void action(async () => {
        await runtime.resetHistory(); setMessage("Connection history reset. Existing connections are not given invented timestamps.");
      })}>Reset connection history</ButtonItem></PanelSectionRow>
      {message && <PanelSectionRow><div style={{ fontSize: 12 }}>{message}</div></PanelSectionRow>}
    </PanelSection>
  </>;
}

export default definePlugin(() => {
  const runtime = new Runtime({ getSnapshot, setSettings, resetHistory, logDiagnostics, exportDiagnostics });
  const listener = addEventListener<[Snapshot]>("snapshot", value => runtime.accept(value));
  let stopNative = () => {};
  let disposed = false;
  // Loader initialization never waits on Bluetooth or installs an enabled override.
  void runtime.start().then(() => {
    if (disposed) return;
    try { stopNative = installNative(runtime); }
    catch { runtime.log("error", "native-adapters-unavailable"); }
  });
  return {
    name: "Bluetooth Order",
    titleView: <div className={staticClasses.Title}>Bluetooth Order</div>,
    icon: <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path fill="none" stroke="currentColor" strokeWidth="2" d="M6 6l12 12-6 4V2l6 4L6 18" />
    </svg>,
    content: <Content runtime={runtime} />,
    onDismount() {
      disposed = true;
      runtime.dispose();
      stopNative();
      removeEventListener("snapshot", listener);
    },
  };
});