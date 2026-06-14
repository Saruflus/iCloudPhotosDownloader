import { API_BASE, getSecret } from "./config";
import type {
  Album, AppSettings, Asset, AuthStatus, CreateJobBody, Job, JobPreview, Schedule, ScheduleBody,
} from "./types";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  const secret = getSecret();
  if (secret) headers["X-Sync-Secret"] = secret;
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const res = await fetch(API_BASE + path, { ...opts, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* non-JSON error */
    }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("application/json") ? res.json() : (undefined as unknown)) as Promise<T>;
}

export const api = {
  authStatus: () => req<AuthStatus>("/api/auth/status"),
  login: (apple_id: string, password: string) =>
    req<{ requires_2fa: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ apple_id, password }),
    }),
  submit2fa: (code: string) =>
    req<{ success: boolean }>("/api/auth/2fa", { method: "POST", body: JSON.stringify({ code }) }),
  logout: () => req<{ success: boolean }>("/api/auth/logout", { method: "POST" }),

  albums: () => req<Album[]>("/api/albums"),
  albumCount: (name: string) =>
    req<{ name: string; asset_count: number | null }>(`/api/albums/${encodeURIComponent(name)}/count`),
  assets: (name: string, offset: number, limit: number) =>
    req<Asset[]>(`/api/albums/${encodeURIComponent(name)}/assets?offset=${offset}&limit=${limit}`),

  jobs: () => req<Job[]>("/api/jobs"),
  job: (id: number) => req<Job>(`/api/jobs/${id}`),
  createJob: (body: CreateJobBody) =>
    req<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  previewJob: (body: CreateJobBody) =>
    req<JobPreview>("/api/jobs/preview", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id: number) => req<{ cancelled: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }),
  retryFailed: (id: number) => req<Job>(`/api/jobs/${id}/retry-failed`, { method: "POST" }),

  tokens: () => req<{ id: string; label: string; example: string }[]>("/api/tokens"),

  getSettings: () => req<AppSettings>("/api/settings"),
  putSettings: (body: Partial<Pick<AppSettings, "download_concurrency" | "max_retries" | "local_timezone" | "thumbnail_cache_ttl">>) =>
    req<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  resetSetting: (key: string) =>
    req<AppSettings>(`/api/settings/${key}`, { method: "DELETE" }),

  // Multiple schedules (Lot 4)
  listSchedules: () => req<Schedule[]>("/api/schedules"),
  createSchedule: (body: ScheduleBody) =>
    req<Schedule>("/api/schedules", { method: "POST", body: JSON.stringify(body) }),
  updateSchedule: (id: number, body: ScheduleBody) =>
    req<Schedule>(`/api/schedules/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteSchedule: (id: number) =>
    req<{ deleted: boolean }>(`/api/schedules/${id}`, { method: "DELETE" }),
  toggleScheduleById: (id: number, enabled: boolean) =>
    req<Schedule>(`/api/schedules/${id}/toggle`, { method: "POST", body: JSON.stringify({ enabled }) }),
};
