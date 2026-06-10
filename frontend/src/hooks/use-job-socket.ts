import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../config";
import type { WsEvent } from "../types";

export interface LiveProgress {
  downloaded: number;
  skipped: number;
  failed: number;
  total: number;
  current_file?: string;
}

export interface JobSocketState {
  progress: LiveProgress | null;
  logs: { level: string; message: string }[];
  done: string | null; // final status once received
  connected: boolean;
}

/**
 * Subscribe to /ws/jobs/{id}. The backend replays the stored log first, then
 * streams live progress (note 4). Logs are capped client-side.
 */
export function useJobSocket(jobId: number | null): JobSocketState {
  const [state, setState] = useState<JobSocketState>({
    progress: null,
    logs: [],
    done: null,
    connected: false,
  });
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (jobId == null) return;
    setState({ progress: null, logs: [], done: null, connected: false });

    const ws = new WebSocket(`${WS_BASE}/ws/jobs/${jobId}`);
    wsRef.current = ws;

    ws.onopen = () => setState((s) => ({ ...s, connected: true }));
    ws.onclose = () => setState((s) => ({ ...s, connected: false }));
    ws.onmessage = (ev) => {
      let msg: WsEvent;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      setState((s) => {
        if (msg.type === "progress") {
          return { ...s, progress: { ...msg } };
        }
        if (msg.type === "log") {
          const logs = [...s.logs, { level: msg.level, message: msg.message }].slice(-100);
          return { ...s, logs };
        }
        if (msg.type === "done") {
          return { ...s, done: msg.status };
        }
        return s;
      });
    };

    return () => ws.close();
  }, [jobId]);

  return state;
}
