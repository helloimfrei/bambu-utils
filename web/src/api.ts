import type { PrinterStatus, RunRecord } from "./types";

async function checked<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: string }
      | null;
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function fetchStatus(): Promise<PrinterStatus> {
  return checked<PrinterStatus>(await fetch("/api/status"));
}

export async function fetchRuns(): Promise<RunRecord[]> {
  return checked<RunRecord[]>(await fetch("/api/runs?limit=12"));
}

export async function sendControl(
  command: "pause" | "resume" | "stop",
): Promise<void> {
  await checked(
    await fetch(`/api/control/${command}`, {
      method: "POST",
      headers: { "X-Bambu-Control": "1" },
    }),
  );
}
