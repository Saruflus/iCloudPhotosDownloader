// The backend is published on :8000 on the same host as the frontend. The
// browser talks to it DIRECTLY (the backend has open CORS), so we don't depend
// on the frontend container resolving the backend by name — important since the
// backend runs with network_mode: bridge (no inter-container DNS).
//
// Override at build time with VITE_API_BASE if the backend lives elsewhere.
const ORIGIN =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  `${location.protocol}//${location.hostname}:8000`;

export const API_BASE = ORIGIN;
export const WS_BASE = ORIGIN.replace(/^http/, "ws"); // http→ws, https→wss

const SECRET_KEY = "syncSecret";

export function getSecret(): string | null {
  return localStorage.getItem(SECRET_KEY);
}

export function setSecret(secret: string): void {
  if (secret) localStorage.setItem(SECRET_KEY, secret);
  else localStorage.removeItem(SECRET_KEY);
}

// Static token palette (mirrors backend AVAILABLE_TOKENS).
export const TOKENS = [
  { id: "year", label: "Year", example: "2024" },
  { id: "month", label: "Month", example: "06" },
  { id: "day", label: "Day", example: "15" },
  { id: "album", label: "Album", example: "Holidays" },
  { id: "mediatype", label: "Media Type", example: "HEIC" },
  { id: "person", label: "Person", example: "Alice" },
  { id: "make", label: "Camera Make", example: "Apple" },
  { id: "model", label: "Camera Model", example: "iPhone 15 Pro" },
  { id: "filename", label: "Filename", example: "IMG_0001" },
];
