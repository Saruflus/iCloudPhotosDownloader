export interface AuthStatus {
  authenticated: boolean;
  needs_2fa: boolean;
}

export interface Album {
  name: string;
  asset_count: number | null;
}

export interface Asset {
  asset_id: string;
  filename: string;
  media_type: string | null;
  file_size: number | null;
  created_at: string | null;
  is_live_photo: boolean;
  has_edited_version: boolean;
  has_raw_version: boolean;
  thumbnail_url: string;
}

export interface Job {
  id: number;
  created_at: string | null;
  status: string;
  selected_albums: string[];
  selected_asset_ids: string[];
  folder_structure: string[];
  include_raw: boolean;
  include_jpeg: boolean;
  include_heic: boolean;
  include_video: boolean;
  download_version: string;
  album_fanout: boolean;
  force_redownload: boolean;
  total_assets: number;
  downloaded_count: number;
  skipped_count: number;
  failed_count: number;
  celery_task_id: string | null;
}

export interface CreateJobBody {
  selected_albums: string[];
  selected_asset_ids: string[];
  folder_structure: string[];
  include_raw: boolean;
  include_jpeg: boolean;
  include_heic: boolean;
  include_video: boolean;
  download_version: string;
  album_fanout: boolean;
  force_redownload: boolean;
}

export interface Token {
  id: string;
  label: string;
  example: string;
}

// Live WS event shapes (note 4)
export type WsEvent =
  | { type: "progress"; downloaded: number; skipped: number; failed: number; total: number; current_file?: string }
  | { type: "log"; level: string; message: string }
  | { type: "done"; status: string };

export interface Schedule {
  id: number;
  cron_expression: string;
  // opaque JSON config (same fields as CreateJobBody)
  job_config: Record<string, any>;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface ScheduleBody {
  cron_expression: string;
  job_config: Record<string, any>;
  enabled: boolean;
}
