import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { FieldRow, Flag, ReportDetail } from "../types";
import { STATUS_OPTIONS, asLines, copyText, download, fmtDate } from "../util";

/** Find a quote in the transcript. Tolerant of case and punctuation, mirroring the backend's check. */
function locate(text: string, quote: string): [number, number] | null {
  const direct = text.toLowerCase().indexOf(quote.toLowerCase());
  if (direct >= 0) return [direct, direct + quote.length];
  const words = quote.split(/[\W_]+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return null;
  const m = new RegExp(words.join("[\\W_]+"), "i").exec(text);
  return m ? [m.index, m.index + m[0].length] : null;
}

const FLAG_LABEL: Record<Flag["type"], string> = {
  missing: "Missing",
  contradiction: "Conflict",
  ambiguous: "Unclear",
  confirmation: "To confirm",
};

interface Props {
  id: number;
  onBack: () => void;
}

export default function ReportView({ id, onBack }: Props) {
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"report" | "fields">("report");
  const [highlight, setHighlight] = useState<string>("");
  const [toast, setToast] = useState("");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.report(id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const inFlight = report && (report.state === "transcribing" || report.state === "extracting");
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [inFlight, load]);

  useEffect(() => {
    if (!report?.audio_available) { setAudioUrl(null); return; }
    let url: string | null = null;
    let cancelled = false;
    api.audioBlobUrl(id).then((u) => { if (cancelled) URL.revokeObjectURL(u); else { url = u; setAudioUrl(u); } }).catch(() => setAudioUrl(null));
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [id, report?.audio_available]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  /** Run a mutation that returns the updated report. */
  async function mutate(fn: () => Promise<ReportDetail>, done?: string) {
    setBusy(true);
    setError("");
    try {
      setReport(await fn());
      if (done) setToast(done);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const show = (quote: string) => {
    setHighlight(quote);
    setTimeout(() => transcriptRef.current?.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" }), 30);
  };

  const transcriptNodes = useMemo(() => {
    const text = report?.transcript?.original_text ?? "";
    const range = highlight ? locate(text, highlight) : null;
    if (!range) return text;
    return <>{text.slice(0, range[0])}<mark>{text.slice(range[0], range[1])}</mark>{text.slice(range[1])}</>;
  }, [report?.transcript?.original_text, highlight]);

  if (!report) {
    return (
      <div className="stack">
        <button className="btn" onClick={onBack} style={{ alignSelf: "flex-start" }}>← Dashboard</button>
        {error ? <div className="banner bad" role="alert">{error}</div> : <p className="muted"><span className="spinner" /> Loading...</p>}
      </div>
    );
  }

  const open = report.flags.filter((f) => f.state === "outstanding");
  const resolved = report.flags.filter((f) => f.state !== "outstanding");
  const done = report.state === "done";

  return (
    <div className="stack">
      <div className="row">
        <button className="btn" onClick={onBack}>← Dashboard</button>
        <div className="grow">
          <h1>{report.label} {report.is_demo && <span className="badge demo">demo</span>}</h1>
          <div className="muted small">Report #{report.id} · reported {fmtDate(report.reported_on)}{report.source_filename ? ` · ${report.source_filename}` : ""}</div>
        </div>
        {done && (
          <>
            <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              Status
              <select value={report.status} disabled={busy} onChange={(e) => mutate(() => api.patchReport(id, { job_status: e.target.value }), "Status updated")} style={{ width: "auto" }}>
                {STATUS_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </label>
            <button className={`btn ${report.reviewed ? "" : "primary"}`} disabled={busy} onClick={() => mutate(() => api.patchReport(id, { reviewed: !report.reviewed }), report.reviewed ? "Marked unreviewed" : "Marked reviewed")}>
              {report.reviewed ? "✓ Reviewed (undo)" : "Mark as reviewed"}
            </button>
          </>
        )}
      </div>

      {error && <div className="banner bad" role="alert">{error}</div>}
      {inFlight && <div className="banner info"><span className="spinner" /> {report.state === "transcribing" ? "Transcribing the recording..." : "Extracting job details..."}</div>}
      {report.state === "failed" && (
        <div className="banner bad" role="alert">
          <strong>Processing failed{report.error_stage ? ` (${report.error_stage.replace("_", " ")})` : ""}.</strong> {report.error_message}
          {report.transcript && <> The transcript was saved. <button className="btn small" disabled={busy} onClick={() => mutate(async () => { await api.reprocess(id); return api.report(id); })}>Reprocess</button></>}
        </div>
      )}
      {report.is_demo && <div className="banner demo">Demo data: built from sample or typed text{report.llm_provider === "mock" ? ", extracted by the mock demo extractor (not a real AI model)" : ""}. Nothing here came from a real recording.</div>}
      {report.transcript?.uncertain && <div className="banner warn">Transcription confidence is low{report.transcript.confidence != null ? ` (${Math.round(report.transcript.confidence * 100)}%)` : ""}. Check key facts against the recording.</div>}
      {report.transcript?.warnings.map((w) => <div className="banner warn" key={w}>{w}</div>)}

      <div className="grid2">
        <div className="stack">
          {done && (
            <div className="card">
              <div className="tabs">
                <button className={`tab ${tab === "report" ? "active" : ""}`} onClick={() => setTab("report")}>Job report</button>
                <button className={`tab ${tab === "fields" ? "active" : ""}`} onClick={() => setTab("fields")}>Review and edit fields</button>
              </div>
              {tab === "report" ? <ReportDoc report={report} busy={busy} onCopied={setToast} onSummary={(s) => mutate(() => api.patchReport(id, { summary: s }), "Summary saved")} />
                : <FieldsEditor report={report} busy={busy} show={show} onSave={(key, value) => mutate(() => api.patchField(id, key, { value }), "Saved")} onReview={(key, state) => mutate(() => api.patchField(id, key, { review_state: state }))} />}
            </div>
          )}
        </div>

        <div className="stack">
          {report.transcript && (
            <div className="card stack">
              <div className="row">
                <h3 className="grow">Original transcript</h3>
                <span className="badge">{report.transcript.is_demo ? "demo text" : report.transcript.provider}{report.transcript.language ? ` · ${report.transcript.language}` : ""}</span>
              </div>
              <div className="transcript" ref={transcriptRef} aria-label="Original transcript">{transcriptNodes}</div>
              {highlight && <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => setHighlight("")}>Clear highlight</button>}
              {audioUrl && <audio controls src={audioUrl} style={{ width: "100%" }} />}
              <div className="muted small">This text is never changed by edits. Click a quote in the report to find it here.</div>
            </div>
          )}

          {done && (
            <div className="card stack">
              <h3>Questions for the technician ({open.length} open)</h3>
              {open.length === 0 && <p className="muted small" style={{ margin: 0 }}>Nothing outstanding.</p>}
              {open.map((f) => <FlagCard key={f.id} flag={f} busy={busy} show={show} onSet={(state, note) => mutate(() => api.patchFlag(f.id, { state, note }))} />)}
              {resolved.length > 0 && (
                <details>
                  <summary className="small muted">{resolved.length} resolved</summary>
                  <div className="stack" style={{ marginTop: 8 }}>
                    {resolved.map((f) => <FlagCard key={f.id} flag={f} busy={busy} show={show} onSet={(state, note) => mutate(() => api.patchFlag(f.id, { state, note }))} />)}
                  </div>
                </details>
              )}
              <div className="muted small">Questions are shown here only. Nothing is sent to the technician or customer automatically.</div>
            </div>
          )}

          {done && <ActionsCard report={report} busy={busy} show={show} mutate={mutate} />}

          <div className="card row">
            <button className="btn" disabled={busy || !report.transcript} onClick={() => confirm("Re-run extraction? This replaces the AI output and discards your edits to this report.") && mutate(async () => { await api.reprocess(id); return api.report(id); })}>Reprocess</button>
            <button className="btn danger" disabled={busy} onClick={async () => { if (confirm("Delete this report and its transcript permanently?")) { await api.remove(id); onBack(); } }}>Delete report</button>
          </div>
        </div>
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

// ------------------------------------------------------------------------------------ job report
function ReportDoc({ report, busy, onCopied, onSummary }: { report: ReportDetail; busy: boolean; onCopied: (m: string) => void; onSummary: (s: string) => void }) {
  const jr = report.job_report;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(report.summary ?? "");
  const list = (items: string[], empty = "Not provided") => items.length ? <ul>{items.map((i) => <li key={i}>{i}</li>)}</ul> : <p className="muted" style={{ margin: "4px 0 12px" }}>{empty}</p>;
  const h = jr.header;
  return (
    <div className="report-doc">
      <div className="row">
        <strong className="grow" style={{ letterSpacing: "0.08em" }}>JOB REPORT</strong>
        <button className="btn small" onClick={async () => onCopied((await copyText(report.report_text)) ? "Report copied" : "Copy failed")}>Copy</button>
        <button className="btn small" onClick={() => download(`job-report-${report.id}.txt`, report.report_text)}>Download .txt</button>
      </div>
      <dl>
        <dt>Customer</dt><dd>{h.customer}</dd>
        <dt>Technician</dt><dd>{h.technician}</dd>
        <dt>Date</dt><dd>{h.date}</dd>
        <dt>Job reference</dt><dd>{h.job_reference}</dd>
        <dt>Location</dt><dd>{h.location}</dd>
        <dt>Status</dt><dd><span className={`badge ${report.status}`}>{h.status}</span></dd>
      </dl>
      <h3>Executive summary</h3>
      {editing ? (
        <div className="stack">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={4} />
          <div className="row">
            <button className="btn primary small" disabled={busy || !draft.trim()} onClick={() => { onSummary(draft); setEditing(false); }}>Save</button>
            <button className="btn small" onClick={() => { setDraft(report.summary ?? ""); setEditing(false); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <p style={{ margin: "4px 0 12px" }}>{jr.executive_summary} <button className="btn small ghost" onClick={() => { setDraft(report.summary ?? ""); setEditing(true); }}>Edit</button></p>
      )}
      <h3>Work completed</h3>{list(jr.work_completed)}
      <h3>Materials or equipment</h3>
      {jr.materials_equipment.equipment.length + jr.materials_equipment.parts_materials.length === 0 ? <p className="muted" style={{ margin: "4px 0 12px" }}>Not provided</p> : (
        <ul>
          {jr.materials_equipment.equipment.map((i) => <li key={"e" + i}>Equipment: {i}</li>)}
          {jr.materials_equipment.parts_materials.map((i) => <li key={"p" + i}>Part or material: {i}</li>)}
        </ul>
      )}
      <h3>Outstanding issues</h3>
      {list([
        ...jr.outstanding_issues.issues.map((i) => `Issue: ${i}`),
        ...jr.outstanding_issues.outstanding_work.map((i) => `Outstanding work: ${i}`),
        ...jr.outstanding_issues.customer_concerns.map((i) => `Customer concern: ${i}`),
      ], "None reported")}
      <h3>Next actions</h3>
      {jr.next_actions.length === 0 ? <p className="muted" style={{ margin: "4px 0 12px" }}>None reported</p> : (
        <ul>{jr.next_actions.map((a) => (
          <li key={a.id} style={a.completed ? { textDecoration: "line-through", opacity: 0.6 } : undefined}>
            {a.description} <span className="muted">(owner: {a.owner ?? "not stated"}; deadline: {a.deadline ?? "not stated"})</span>
          </li>
        ))}</ul>
      )}
      <h3>Missing information</h3>{list(jr.missing_information.map((m) => m.question), "None outstanding")}
      <h3>Source confidence</h3>{list(jr.source_confidence.notes)}
    </div>
  );
}

// ------------------------------------------------------------------------------------ field editor
function FieldsEditor({ report, busy, show, onSave, onReview }: { report: ReportDetail; busy: boolean; show: (q: string) => void; onSave: (key: string, value: string | string[]) => void; onReview: (key: string, state: string) => void }) {
  return (
    <div>
      <p className="muted small" style={{ marginTop: 0 }}>
        Facts the technician said are marked <span className="badge ok">stated</span>; AI summaries or inferences are <span className="badge warn">interpreted</span>. Confirm or correct each one. Your edits never change the transcript.
      </p>
      {report.fields.map((f) => <FieldItem key={f.key + (f.edited_at ?? "") + JSON.stringify(f.value)} field={f} busy={busy} show={show} onSave={onSave} onReview={onReview} />)}
    </div>
  );
}

function FieldItem({ field, busy, show, onSave, onReview }: { field: FieldRow; busy: boolean; show: (q: string) => void; onSave: (key: string, value: string | string[]) => void; onReview: (key: string, state: string) => void }) {
  const [draft, setDraft] = useState(asLines(field.value));
  const dirty = draft !== asLines(field.value);
  const isStatus = field.kind === "enum";
  const empty = !field.value || (Array.isArray(field.value) && field.value.length === 0);
  const edited = field.review_state === "corrected" || (field.edited_at != null);
  return (
    <div className="field-row">
      <div className="row">
        <strong className="grow">{field.label}</strong>
        {empty ? <span className="badge">not provided</span> : <span className={`badge ${field.basis === "stated" ? "ok" : "warn"}`}>{field.basis}</span>}
        {field.review_state !== "unreviewed" && <span className="badge info">{field.review_state.replace("_", " ")}</span>}
      </div>
      {isStatus ? (
        <select value={asLines(field.value) || "unknown"} disabled={busy} onChange={(e) => onSave(field.key, e.target.value)}>
          {STATUS_OPTIONS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      ) : field.kind === "list" ? (
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={Math.max(2, draft.split("\n").length)} placeholder="Not provided (one item per line)" aria-label={field.label} />
      ) : (
        <input type="text" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Not provided" aria-label={field.label} />
      )}
      {field.evidence.map((e) => (
        <button key={e.text} className={`quote ${e.verified ? "" : "bad"}`} onClick={() => show(e.text)} title="Show in transcript">
          “{e.text}” {!e.verified && <strong>(not found word for word in the transcript)</strong>}
        </button>
      ))}
      {edited && JSON.stringify(field.ai_value) !== JSON.stringify(field.value) && (
        <div className="muted small">AI originally: {asLines(field.ai_value) || "not provided"}</div>
      )}
      {!isStatus && (
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn primary small" disabled={busy || !dirty} onClick={() => onSave(field.key, field.kind === "list" ? draft.split("\n") : draft)}>Save</button>
          {dirty && <button className="btn small" onClick={() => setDraft(asLines(field.value))}>Discard</button>}
          {!dirty && !empty && field.review_state !== "confirmed" && field.review_state !== "corrected" && (
            <button className="btn small" disabled={busy} onClick={() => onReview(field.key, "confirmed")}>Confirm correct</button>
          )}
          {!dirty && empty && field.review_state !== "not_applicable" && <button className="btn small" disabled={busy} onClick={() => onReview(field.key, "not_applicable")}>Not applicable</button>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------ flags
function FlagCard({ flag, busy, show, onSet }: { flag: Flag; busy: boolean; show: (q: string) => void; onSet: (state: string, note?: string) => void }) {
  const isOpen = flag.state === "outstanding";
  return (
    <div className={`flag ${flag.type} ${isOpen ? "" : "resolved"}`}>
      <div className="row">
        <span className={`badge ${flag.type === "contradiction" ? "failed" : "warn"}`}>{FLAG_LABEL[flag.type]}</span>
        {flag.severity === "high" && <span className="badge failed">high</span>}
        {!isOpen && <span className="badge ok">{flag.state.replace("_", " ")}{flag.resolved_by === "system" ? " (auto)" : ""}</span>}
      </div>
      <div className="q">{flag.question}</div>
      <div className="muted small">{flag.detail}</div>
      {flag.evidence.map((e) => <button key={e.text} className="quote" onClick={() => show(e.text)}>“{e.text}”</button>)}
      <div className="row" style={{ marginTop: 6 }}>
        {isOpen ? (
          <>
            <button className="btn small" disabled={busy} onClick={() => onSet("confirmed")}>Confirmed</button>
            <button className="btn small" disabled={busy} onClick={() => onSet("corrected")}>Corrected</button>
            <button className="btn small" disabled={busy} onClick={() => onSet("not_applicable")}>Not applicable</button>
          </>
        ) : (
          <button className="btn small" disabled={busy} onClick={() => onSet("outstanding")}>Mark still outstanding</button>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------ actions
function ActionsCard({ report, busy, show, mutate }: { report: ReportDetail; busy: boolean; show: (q: string) => void; mutate: (fn: () => Promise<ReportDetail>, done?: string) => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [d, setD] = useState({ description: "", owner: "", deadline: "" });
  return (
    <div className="card stack">
      <div className="row"><h3 className="grow">Follow-up actions</h3><button className="btn small" onClick={() => setAdding((v) => !v)}>{adding ? "Cancel" : "+ Add"}</button></div>
      {report.actions.length === 0 && !adding && <p className="muted small" style={{ margin: 0 }}>No follow-up actions were reported.</p>}
      {report.actions.map((a) => (
        <div key={a.id} className="field-row">
          <label className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
            <input type="checkbox" checked={a.completed} disabled={busy} onChange={(e) => mutate(() => api.patchAction(a.id, { completed: e.target.checked }))} style={{ marginTop: 5 }} />
            <span style={a.completed ? { textDecoration: "line-through", opacity: 0.6 } : undefined}>
              {a.description}
              {a.source === "manager" && <> <span className="badge">added by you</span></>}
            </span>
          </label>
          <div className="row small" style={{ marginLeft: 26 }}>
            <InlineEdit label="Owner" value={a.owner} disabled={busy} onSave={(v) => mutate(() => api.patchAction(a.id, { owner: v }))} />
            <InlineEdit label="Deadline" value={a.deadline} disabled={busy} onSave={(v) => mutate(() => api.patchAction(a.id, { deadline: v }))} />
            <button className="btn small ghost danger" disabled={busy} onClick={() => confirm("Remove this action?") && mutate(() => api.deleteAction(a.id))}>Remove</button>
          </div>
          {a.evidence.map((e) => <button key={e.text} className="quote" style={{ marginLeft: 26 }} onClick={() => show(e.text)}>“{e.text}”</button>)}
        </div>
      ))}
      {adding && (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); void mutate(() => api.addAction(report.id, { description: d.description, owner: d.owner || undefined, deadline: d.deadline || undefined }), "Action added").then(() => { setD({ description: "", owner: "", deadline: "" }); setAdding(false); }); }}>
          <input type="text" required minLength={3} placeholder="What needs to happen?" value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} aria-label="Action description" />
          <div className="row">
            <input type="text" placeholder="Owner (optional)" value={d.owner} onChange={(e) => setD({ ...d, owner: e.target.value })} style={{ flex: 1 }} aria-label="Owner" />
            <input type="text" placeholder="Deadline (optional)" value={d.deadline} onChange={(e) => setD({ ...d, deadline: e.target.value })} style={{ flex: 1 }} aria-label="Deadline" />
          </div>
          <button className="btn primary small" style={{ alignSelf: "flex-start" }} disabled={busy}>Add action</button>
        </form>
      )}
    </div>
  );
}

function InlineEdit({ label, value, disabled, onSave }: { label: string; value: string | null; disabled: boolean; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  if (!editing) return <span>{label}: <strong>{value ?? "not stated"}</strong> <button className="btn small ghost" disabled={disabled} onClick={() => setEditing(true)}>edit</button></span>;
  return (
    <span className="row" style={{ gap: 4 }}>
      {label}:
      <input type="text" value={v} onChange={(e) => setV(e.target.value)} style={{ width: 150 }} autoFocus aria-label={label} />
      <button className="btn small primary" onClick={() => { onSave(v); setEditing(false); }}>Save</button>
      <button className="btn small" onClick={() => { setV(value ?? ""); setEditing(false); }}>Cancel</button>
    </span>
  );
}
