import { API_BASE, getSecret } from "./config";
import type { Album, Asset, AuthStatus, CreateJobBody, Job } from "./types";

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
  assets: (name: string, offset: number, limit: number) =>
    req<Asset[]>(`/api/albums/${encodeURIComponent(name)}/assets?offset=${offset}&limit=${limit}`),

  jobs: () => req<Job[]>("/api/jobs"),
  job: (id: number) => req<Job>(`/api/jobs/${id}`),
  createJob: (body: CreateJobBody) =>
    req<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id: number) => req<{ cancelled: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }),
  retryFailed: (id: number) => req<Job>(`/api/jobs/${id}/retry-failed`, { method: "POST" }),
};
