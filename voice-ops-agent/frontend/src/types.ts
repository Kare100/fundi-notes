export type JobStatus =
  | "completed"
  | "in_progress"
  | "partially_completed"
  | "awaiting_return_visit"
  | "blocked"
  | "unknown";

export interface Evidence {
  text: string;
  verified: boolean;
}

export interface ReportListItem {
  id: number;
  created_at: string;
  reported_on: string;
  state: "transcribing" | "extracting" | "done" | "failed";
  error_message: string | null;
  label: string;
  customer: string | null;
  job_reference: string | null;
  technician: string | null;
  location: string | null;
  status: JobStatus;
  status_label: string;
  is_demo: boolean;
  reviewed: boolean;
  open_actions: number;
  outstanding_flags: Record<string, number>;
  outstanding_flag_count: number;
  missing_info: boolean;
  action_required: boolean;
  summary: string | null;
}

export interface FieldRow {
  key: string;
  label: string;
  kind: "text" | "list" | "enum";
  help: string;
  ai_value: string | string[] | null;
  value: string | string[] | null;
  basis: "stated" | "interpreted" | "missing";
  evidence: Evidence[];
  review_state: string;
  edited_at: string | null;
}

export interface Flag {
  id: number;
  type: "missing" | "contradiction" | "ambiguous" | "confirmation";
  field_key: string | null;
  severity: "high" | "medium" | "low";
  detail: string;
  question: string;
  evidence: Evidence[];
  origin: string;
  state: "outstanding" | "confirmed" | "corrected" | "not_applicable";
  resolved_by: string | null;
  note: string | null;
  resolved_at: string | null;
}

export interface Action {
  id: number;
  description: string;
  owner: string | null;
  deadline: string | null;
  evidence: Evidence[];
  source: string;
  completed: boolean;
  completed_at: string | null;
  created_at: string;
}

export interface Transcript {
  original_text: string;
  provider: string;
  language: string | null;
  confidence: number | null;
  uncertain: boolean;
  warnings: string[];
  is_demo: boolean;
  created_at: string;
}

export interface JobReportView {
  header: Record<"customer" | "technician" | "date" | "job_reference" | "location" | "status", string>;
  executive_summary: string;
  work_completed: string[];
  materials_equipment: { equipment: string[]; parts_materials: string[] };
  outstanding_issues: { issues: string[]; outstanding_work: string[]; customer_concerns: string[] };
  next_actions: { id: number; description: string; owner: string | null; deadline: string | null; completed: boolean }[];
  missing_information: { id: number; type: string; question: string }[];
  source_confidence: { notes: string[]; transcription_uncertain: boolean };
}

export interface ReportDetail extends ReportListItem {
  updated_at: string;
  source_type: string;
  source_filename: string | null;
  error_stage: string | null;
  transcription_provider: string | null;
  llm_provider: string | null;
  summary_ai: string | null;
  transcript: Transcript | null;
  fields: FieldRow[];
  flags: Flag[];
  actions: Action[];
  job_report: JobReportView;
  report_text: string;
  audio_available: boolean;
}

export interface Stats {
  total_reports: number;
  processed: number;
  failed: number;
  processing: number;
  completed: number;
  in_progress: number;
  blocked_or_incomplete: number;
  status_not_stated: number;
  with_missing_info: number;
  open_actions: number;
  reports_with_open_actions: number;
  unreviewed: number;
}

export interface Sample {
  id: string;
  title: string;
  description: string;
  transcript: string;
}

export interface AppConfig {
  demo: { transcription: boolean; llm: boolean };
  providers: { transcription: string; llm: string };
  upload: { extensions: string[]; max_mb: number; max_minutes: number };
  retention: { mode: string; days: number };
  data_sharing: string[];
  job_statuses: { value: JobStatus; label: string }[];
  checklist: { field: string; label: string; severity: string; when_status: string[] | null }[];
}

export interface SummaryResult {
  start: string;
  end: string;
  generated_at: string;
  totals: Stats & { reviewed: number };
  notes: string[];
  text: string;
}

export interface Filters {
  search: string;
  status: string;
  technician: string;
  date_from: string;
  date_to: string;
  missing_info: string;
  action_required: string;
}
