import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { fetchRuns, fetchStatus, sendControl } from "./api";
import type {
  AmsTrayStatus,
  PrinterStatus,
  RunRecord,
  TemperatureStatus,
} from "./types";

const SPEED_NAMES: Record<number, string> = {
  1: "Silent",
  2: "Standard",
  3: "Sport",
  4: "Ludicrous",
};

function App() {
  const [status, setStatus] = useState<PrinterStatus | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cameraVisible, setCameraVisible] = useState(true);
  const [cameraFailed, setCameraFailed] = useState(false);
  const [pendingControl, setPendingControl] = useState<string | null>(null);

  useEffect(() => {
    fetchStatus().then(setStatus).catch((reason: unknown) => {
      setError(errorMessage(reason));
    });
    const events = new EventSource("/api/events");
    events.addEventListener("status", (event) => {
      setStatus(JSON.parse((event as MessageEvent<string>).data) as PrinterStatus);
      setError(null);
    });
    events.onerror = () => setError("Live connection interrupted — reconnecting");
    return () => events.close();
  }, []);

  useEffect(() => {
    const load = () => fetchRuns().then(setRuns).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const stateClass = useMemo(
    () => stateTone(status?.run.state ?? "UNKNOWN"),
    [status?.run.state],
  );

  async function control(command: "pause" | "resume" | "stop") {
    if (command === "stop" && !window.confirm("Stop the active print?")) return;
    setPendingControl(command);
    try {
      await sendControl(command);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setPendingControl(null);
    }
  }

  if (!status) {
    return (
      <main className="boot-screen">
        <div className="boot-mark" />
        <p>Connecting to printer…</p>
      </main>
    );
  }

  const jobName = status.run.name || "No active print";
  const showCamera =
    cameraVisible && status.connected && status.device.camera_available;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">B</div>
          <div>
            <p className="eyebrow">Bambu monitor</p>
            <h1>{status.device.model ?? "Printer"}</h1>
          </div>
        </div>
        <div className="connection-block">
          <span className={`connection-dot ${status.connected ? "online" : ""}`} />
          <div>
            <strong>{status.connected ? "Connected" : "Offline"}</strong>
            <small>{freshness(status.updated_at)}</small>
          </div>
        </div>
      </header>

      {(error || status.connection_error) && (
        <div className="notice error-notice">{error ?? status.connection_error}</div>
      )}

      <main className="dashboard">
        <section className="hero-grid">
          <article className="panel camera-panel">
            <div className="panel-heading camera-heading">
              <div>
                <p className="eyebrow">Live view</p>
                <h2>Build plate</h2>
              </div>
              <button
                className="text-button"
                onClick={() => {
                  setCameraVisible((visible) => !visible);
                  setCameraFailed(false);
                }}
              >
                {cameraVisible ? "Release camera" : "Start camera"}
              </button>
            </div>
            <div className="camera-frame">
              {showCamera && !cameraFailed ? (
                <img
                  src="/api/camera.mjpeg"
                  alt="Live view of the printer build plate"
                  onError={() => setCameraFailed(true)}
                />
              ) : (
                <div className="camera-placeholder">
                  <div className="camera-icon" />
                  <strong>{cameraFailed ? "Camera unavailable" : "Camera released"}</strong>
                  <span>
                    {cameraFailed
                      ? "Check LAN live-view and try again."
                      : "Start it when you need a look."}
                  </span>
                </div>
              )}
              <div className="camera-badge">
                {status.device.camera_resolution ?? "LAN"}
              </div>
            </div>
          </article>

          <article className="panel job-panel">
            <div className="job-topline">
              <span className={`state-pill ${stateClass}`}>{prettyState(status.run.state)}</span>
              <span className="job-speed">
                {SPEED_NAMES[status.run.speed_level] ?? "Speed"} · {status.run.speed_percent}%
              </span>
            </div>
            <div className="job-copy">
              <p className="eyebrow">Current run</p>
              <h2>{jobName}</h2>
              <p className="muted filename">{status.run.file || "Printer is ready"}</p>
            </div>
            {status.run.active ? (
              <div className="progress-block">
                <div className="progress-labels">
                  <strong>{status.run.progress}%</strong>
                  <span>{remaining(status.run.remaining_minutes)}</span>
                </div>
                <div className="progress-track">
                  <div style={{ width: `${status.run.progress}%` }} />
                </div>
                <div className="progress-meta">
                  <span>
                    Layer {status.run.layer.toLocaleString()} / {status.run.total_layers.toLocaleString()}
                  </span>
                  {status.run.stage && <span>Stage {status.run.stage}</span>}
                </div>
              </div>
            ) : (
              <div className="idle-block">
                <strong>Ready for the next run</strong>
                <span>Send a sliced file or start one from the printer.</span>
              </div>
            )}
            <div className="controls">
              <button
                className="control-button primary"
                disabled={!status.run.active || status.run.paused || pendingControl !== null}
                onClick={() => void control("pause")}
              >
                Pause
              </button>
              <button
                className="control-button primary"
                disabled={!status.run.paused || pendingControl !== null}
                onClick={() => void control("resume")}
              >
                Resume
              </button>
              <button
                className="control-button danger"
                disabled={!status.run.active || pendingControl !== null}
                onClick={() => void control("stop")}
              >
                Stop
              </button>
            </div>
          </article>
        </section>

        <section className="metric-grid">
          <TemperatureCard label="Nozzle" value={status.temperatures.nozzle} />
          <TemperatureCard label="Bed" value={status.temperatures.bed} />
          {status.temperatures.chamber && (
            <TemperatureCard label="Chamber" value={status.temperatures.chamber} />
          )}
          <article className="metric-card">
            <p className="eyebrow">Part fan</p>
            <strong>{percent(status.fans.part)}</strong>
            <span>{nozzleDescription(status)}</span>
          </article>
          <article className="metric-card">
            <p className="eyebrow">Network</p>
            <strong>{status.device.wifi_signal_dbm ?? "—"}<small> dBm</small></strong>
            <span>{status.device.sd_card ? "SD card ready" : "No SD card"}</span>
          </article>
        </section>

        {status.alerts.length > 0 && (
          <section className="panel alerts-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Needs attention</p>
                <h2>Printer alerts</h2>
              </div>
            </div>
            {status.alerts.map((alert) => (
              <div className="alert-row" key={alert.code}>
                <strong>{alert.code}</strong>
                <span>{alert.message ?? "See the printer for details"}</span>
              </div>
            ))}
          </section>
        )}

        <section className="lower-grid">
          <article className="panel ams-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Material system</p>
                <h2>{status.ams.connected ? "AMS Lite" : "External spool"}</h2>
              </div>
              <span className="panel-meta">{status.ams.trays.filter((tray) => tray.present).length} loaded</span>
            </div>
            <div className="tray-grid">
              {status.ams.trays.map((tray) => (
                <Tray key={tray.id} tray={tray} />
              ))}
              {status.ams.trays.length === 0 && (
                <p className="empty-state">No AMS telemetry reported.</p>
              )}
            </div>
          </article>

          <article className="panel history-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Local journal</p>
                <h2>Recent runs</h2>
              </div>
              <span className="panel-meta">Pi storage</span>
            </div>
            <div className="run-list">
              {runs.map((run) => (
                <div className="run-row" key={run.id}>
                  <span className={`run-result ${stateTone(run.final_state ?? "RUNNING")}`} />
                  <div className="run-name">
                    <strong>{run.name}</strong>
                    <span>{formatDate(run.started_at)}</span>
                  </div>
                  <div className="run-outcome">
                    <strong>{prettyState(run.final_state ?? "RUNNING")}</strong>
                    <span>{duration(run.started_at, run.ended_at)}</span>
                  </div>
                </div>
              ))}
              {runs.length === 0 && (
                <p className="empty-state">Completed runs will appear here.</p>
              )}
            </div>
          </article>
        </section>
      </main>
    </div>
  );
}

function TemperatureCard({ label, value }: { label: string; value: TemperatureStatus }) {
  return (
    <article className="metric-card">
      <p className="eyebrow">{label}</p>
      <strong>{temperature(value.current)}</strong>
      <span>Target {temperatureTarget(value.target)}</span>
    </article>
  );
}

function Tray({ tray }: { tray: AmsTrayStatus }) {
  return (
    <div className={`tray ${tray.present ? "loaded" : "empty"} ${tray.active ? "active" : ""}`}>
      <div className="tray-topline">
        <span>Slot {tray.id + 1}</span>
        {tray.active && <b>Active</b>}
      </div>
      <div className="spool" style={{ "--spool-color": tray.color ?? "#2b312d" } as CSSProperties}>
        <i />
      </div>
      <div className="tray-copy">
        <strong>{tray.present ? tray.subtype || tray.filament_type : "Empty"}</strong>
        <span>{tray.present ? tray.filament_type : "Ready for spool"}</span>
      </div>
      <div className="remaining-track">
        <div style={{ width: `${tray.remaining_percent ?? 0}%` }} />
      </div>
      <small>{tray.remaining_percent == null ? "—" : `${tray.remaining_percent}% remaining`}</small>
    </div>
  );
}

function stateTone(state: string): string {
  if (["RUNNING", "FINISH"].includes(state)) return "good";
  if (["PAUSE", "PREPARE"].includes(state)) return "warn";
  if (["FAILED", "STOP", "ERROR"].includes(state)) return "bad";
  return "neutral";
}

function prettyState(state: string): string {
  return state.charAt(0) + state.slice(1).toLowerCase().replaceAll("_", " ");
}

function temperature(value: number | null): string {
  return value == null ? "—" : `${Math.round(value)}°`;
}

function temperatureTarget(value: number | null): string {
  return value === 0 ? "Off" : temperature(value);
}

function percent(value: number | null): string {
  return value == null ? "—" : `${value}%`;
}

function remaining(minutes: number): string {
  if (minutes <= 0) return "No ETA";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours}h ${rest}m remaining` : `${rest}m remaining`;
}

function freshness(value: string | null): string {
  if (!value) return "Waiting for telemetry";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 1000));
  return seconds < 5 ? "Live telemetry" : `Updated ${seconds}s ago`;
}

function nozzleDescription(status: PrinterStatus): string {
  const diameter = status.device.nozzle_diameter;
  const type = status.device.nozzle_type?.replaceAll("_", " ");
  return diameter == null ? "Nozzle unavailable" : `${diameter} mm ${type ?? "nozzle"}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function duration(start: string, end: string | null): string {
  if (!end) return "In progress";
  const minutes = Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / 60_000));
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Unexpected dashboard error";
}

export default App;
