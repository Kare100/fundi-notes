import type {
  AppConfig,
  Filters,
  ReportDetail,
  ReportListItem,
  Sample,
  Stats,
  SummaryResult,
} from "./types";

const KEY = "voiceops.auth";

/** Credentials live in sessionStorage only (cleared when the tab closes). */
export const credentials = {
  get(): string | null {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return memory;
    }
  },
  set(user: string, password: string) {
    const token = btoa(unescape(encodeURIComponent(`${user}:${password}`)));
    memory = token;
    try {
      sessionStorage.setItem(KEY, token);
    } catch {
      /* private mode: fall back to memory */
    }
  },
  clear() {
    memory = null;
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  },
};
let memory: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function messageFrom(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const where = first.loc ? first.loc.filter((p) => p !== "body").join(".") : "";
    return `${where ? where + ": " : ""}${first.msg ?? "Invalid input."}`;
  }
  if (status === 0) return "Cannot reach the server. Is the backend running?";
  return `Request failed (HTTP ${status}).`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = credentials.get();
  if (token) headers.set("Authorization", `Basic ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, messageFrom(null, 0));
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Please sign in.");
  }
  if (res.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non JSON error page */
  }
  if (!res.ok) throw new ApiError(res.status, messageFrom(body, res.status));
  return body as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

function query(params: Record<string, string>): string {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== "" && q.set(k, v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  health: () => request<{ auth_required: boolean; demo_transcription: boolean; demo_llm: boolean }>("/api/health"),
  config: () => request<AppConfig>("/api/config"),
  samples: () => request<Sample[]>("/api/samples"),
  stats: (f: Pick<Filters, "date_from" | "date_to">) => request<Stats>(`/api/stats${query(f)}`),
  technicians: () => request<string[]>("/api/technicians"),
  reports: (f: Filters) => request<ReportListItem[]>(`/api/reports${query({ ...f })}`),
  report: (id: number) => request<ReportDetail>(`/api/reports/${id}`),
  upload: (file: File, reportedOn: string) => {
    const form = new FormData();
    form.append("file", file);
    if (reportedOn) form.append("reported_on", reportedOn);
    return request<{ id: number }>("/api/reports/upload", { method: "POST", body: form });
  },
  demo: (body: { sample_id?: string; text?: string; reported_on?: string }) =>
    request<{ id: number }>("/api/reports/demo", json("POST", body)),
  reprocess: (id: number) => request<{ id: number }>(`/api/reports/${id}/reprocess`, json("POST")),
  remove: (id: number) => request<void>(`/api/reports/${id}`, json("DELETE")),
  patchReport: (id: number, body: Record<string, unknown>) => request<ReportDetail>(`/api/reports/${id}`, json("PATCH", body)),
  patchField: (id: number, key: string, body: Record<string, unknown>) =>
    request<ReportDetail>(`/api/reports/${id}/fields/${key}`, json("PATCH", body)),
  patchFlag: (flagId: number, body: { state: string; note?: string }) =>
    request<ReportDetail>(`/api/flags/${flagId}`, json("PATCH", body)),
  addAction: (id: number, body: { description: string; owner?: string; deadline?: string }) =>
    request<ReportDetail>(`/api/reports/${id}/actions`, json("POST", body)),
  patchAction: (actionId: number, body: Record<string, unknown>) =>
    request<ReportDetail>(`/api/actions/${actionId}`, json("PATCH", body)),
  deleteAction: (actionId: number) => request<ReportDetail>(`/api/actions/${actionId}`, json("DELETE")),
  summary: (date: string, end?: string) => request<SummaryResult>(`/api/summary${query({ date, end: end ?? "" })}`),
  /** Audio is behind authentication, so it is fetched with the auth header and played from a blob URL. */
  audioBlobUrl: async (id: number): Promise<string> => {
    const token = credentials.get();
    const res = await fetch(`/api/reports/${id}/audio`, { headers: token ? { Authorization: `Basic ${token}` } : {} });
    if (!res.ok) throw new ApiError(res.status, "The original audio is not available.");
    return URL.createObjectURL(await res.blob());
  },
};
