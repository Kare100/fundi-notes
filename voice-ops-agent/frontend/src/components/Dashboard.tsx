import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AppConfig, Filters, ReportListItem, Sample, Stats } from "../types";
import { STATUS_OPTIONS, fmtDate } from "../util";
import UploadPanel from "./UploadPanel";

const EMPTY: Filters = { search: "", status: "", technician: "", date_from: "", date_to: "", missing_info: "", action_required: "" };

interface Props {
  config: AppConfig;
  samples: Sample[];
  onOpen: (id: number) => void;
}

export default function Dashboard({ config, samples, onOpen }: Props) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [stats, setStats] = useState<Stats | null>(null);
  const [rows, setRows] = useState<ReportListItem[] | null>(null);
  const [techs, setTechs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const n = ++seq.current;
    try {
      const [s, r, t] = await Promise.all([api.stats(filters), api.reports(filters), api.technicians()]);
      if (n !== seq.current) return; // a newer request superseded this one
      setStats(s);
      setRows(r);
      setTechs(t);
      setError("");
    } catch (e) {
      if (n === seq.current) setError((e as Error).message);
    }
  }, [filters]);

  useEffect(() => {
    const t = setTimeout(load, filters.search ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, filters.search]);

  const processing = rows?.some((r) => r.state === "transcribing" || r.state === "extracting") ?? false;
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [processing, load]);

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const tile = (label: string, n: number | undefined, patch: Partial<Filters>, active: boolean) => (
    <button className={`tile ${active ? "active" : ""}`} onClick={() => setFilters(active ? EMPTY : { ...EMPTY, ...patch })} aria-pressed={active}>
      <div className="n">{n ?? "-"}</div>
      <div className="l">{label}</div>
    </button>
  );
  const anyFilter = Object.values(filters).some(Boolean);

  return (
    <div className="stack">
      <div className="row">
        <h2 className="grow">Dashboard</h2>
        <button className="btn primary" onClick={() => setShowUpload((v) => !v)}>{showUpload ? "Close" : "+ Add voice note"}</button>
      </div>

      {showUpload && <UploadPanel config={config} samples={samples} onCreated={(id) => { setShowUpload(false); void load(); onOpen(id); }} />}

      {error && <div className="banner bad" role="alert">{error} <button className="btn small" onClick={load}>Retry</button></div>}

      <div className="tiles" aria-label="Summary statistics">
        {tile("Reports processed", stats?.total_reports, {}, false)}
        {tile("Completed jobs", stats?.completed, { status: "completed" }, filters.status === "completed")}
        {tile("In progress", stats?.in_progress, { status: "in_progress" }, filters.status === "in_progress")}
        {tile("Blocked", stats?.blocked_or_incomplete, { status: "blocked" }, filters.status === "blocked")}
        {tile("Missing information", stats?.with_missing_info, { missing_info: "true" }, filters.missing_info === "true")}
        {tile("Outstanding actions", stats?.open_actions, { action_required: "true" }, filters.action_required === "true")}
      </div>
      {stats && (stats.failed > 0 || stats.processing > 0 || stats.unreviewed > 0) && (
        <div className="small muted">
          {stats.failed > 0 && <><button className="btn small" onClick={() => setFilters({ ...EMPTY, status: "failed" })}>{stats.failed} failed</button>{" "}</>}
          {stats.processing > 0 && <>{stats.processing} processing <span className="spinner" /> </>}
          {stats.unreviewed > 0 && <>{stats.unreviewed} not yet reviewed. </>}
          "In progress" includes jobs awaiting a return visit; "Blocked" includes partially completed jobs. {stats.status_not_stated > 0 && `${stats.status_not_stated} report(s) have no status stated.`}
        </div>
      )}

      <div className="card filters">
        <label className="field">Search
          <input type="search" value={filters.search} onChange={(e) => set({ search: e.target.value })} placeholder="Customer, job, transcript..." />
        </label>
        <label className="field">Status
          <select value={filters.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">All</option>
            {STATUS_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            <option value="failed">Failed to process</option>
            <option value="processing">Still processing</option>
          </select>
        </label>
        <label className="field">Technician
          <select value={filters.technician} onChange={(e) => set({ technician: e.target.value })}>
            <option value="">All</option>
            {techs.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="field">From
          <input type="date" value={filters.date_from} onChange={(e) => set({ date_from: e.target.value })} />
        </label>
        <label className="field">To
          <input type="date" value={filters.date_to} onChange={(e) => set({ date_to: e.target.value })} />
        </label>
        <label className="field">Missing information
          <select value={filters.missing_info} onChange={(e) => set({ missing_info: e.target.value })}>
            <option value="">Any</option><option value="true">Has missing info</option><option value="false">None missing</option>
          </select>
        </label>
        <label className="field">Action required
          <select value={filters.action_required} onChange={(e) => set({ action_required: e.target.value })}>
            <option value="">Any</option><option value="true">Action required</option><option value="false">No action needed</option>
          </select>
        </label>
        <button className="btn" disabled={!anyFilter} onClick={() => setFilters(EMPTY)}>Clear filters</button>
      </div>

      <div className="card table-wrap" style={{ padding: 0 }}>
        {rows === null ? (
          <p className="muted" style={{ padding: 16 }}><span className="spinner" /> Loading reports...</p>
        ) : rows.length === 0 ? (
          <p className="muted" style={{ padding: 16 }}>
            {anyFilter ? "No reports match these filters." : "No reports yet. Add a voice note, or in demo mode process one of the sample transcripts."}
          </p>
        ) : (
          <table className="responsive">
            <thead>
              <tr><th>Job / customer</th><th>Technician</th><th>Date</th><th>Status</th><th>Attention</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} tabIndex={0} onClick={() => onOpen(r.id)} onKeyDown={(e) => e.key === "Enter" && onOpen(r.id)}>
                  <td>
                    <strong>{r.label}</strong>
                    {r.job_reference && r.label !== r.job_reference && <span className="muted small"> · {r.job_reference}</span>}
                    {r.is_demo && <> <span className="badge demo">demo</span></>}
                    {r.summary && <div className="muted small" style={{ maxWidth: 460 }}>{r.summary.length > 120 ? r.summary.slice(0, 117) + "..." : r.summary}</div>}
                  </td>
                  <td>{r.technician ?? <span className="muted">Not provided</span>}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(r.reported_on)}</td>
                  <td>
                    {r.state === "done" ? <span className={`badge ${r.status}`}>{r.status_label}</span>
                      : r.state === "failed" ? <span className="badge failed">Failed</span>
                      : <span className="badge"><span className="spinner" /> Processing</span>}
                  </td>
                  <td>
                    {r.state === "failed" ? <span className="small">{r.error_message}</span> : (
                      <div className="row" style={{ gap: 4 }}>
                        {r.action_required ? <span className="badge warn">Action required</span> : <span className="badge ok">Clear</span>}
                        {r.missing_info && <span className="badge warn">Missing info</span>}
                        {r.outstanding_flags.contradiction ? <span className="badge failed">Conflict</span> : null}
                        {r.open_actions > 0 && <span className="badge">{r.open_actions} open action{r.open_actions > 1 ? "s" : ""}</span>}
                        {r.state === "done" && !r.reviewed && <span className="badge">Unreviewed</span>}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
