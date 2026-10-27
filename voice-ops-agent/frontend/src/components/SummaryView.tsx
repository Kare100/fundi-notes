import { useEffect, useState } from "react";
import { api } from "../api";
import type { SummaryResult } from "../types";
import { copyText, download, today } from "../util";

export default function SummaryView({ onOpen }: { onOpen: (id: number) => void }) {
  const [date, setDate] = useState(today());
  const [end, setEnd] = useState("");
  const [data, setData] = useState<SummaryResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      setData(await api.summary(date, end || undefined));
    } catch (e) {
      setData(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void generate(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const t = data?.totals;
  return (
    <div className="stack">
      <h2>Daily executive summary</h2>
      <div className="card row">
        <label className="field">Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="field">To (optional)<input type="date" value={end} min={date} onChange={(e) => setEnd(e.target.value)} /></label>
        <button className="btn primary" disabled={busy || !date} onClick={generate} style={{ alignSelf: "flex-end" }}>{busy ? "Generating..." : "Generate summary"}</button>
      </div>
      {error && <div className="banner bad" role="alert">{error}</div>}
      {data && t && (
        <>
          {data.notes.map((n) => <div key={n} className={`banner ${/NOT reflected|failed/.test(n) ? "bad" : /unverified|demo/i.test(n) ? "warn" : "info"}`}>{n}</div>)}
          <div className="tiles">
            {([["Received", t.total_reports], ["Processed", t.processed], ["Completed", t.completed], ["In progress", t.in_progress], ["Blocked / partial", t.blocked_or_incomplete], ["Open actions", t.open_actions]] as const).map(([l, n]) => (
              <div className="tile" key={l} style={{ cursor: "default" }}><div className="n">{n}</div><div className="l">{l}</div></div>
            ))}
          </div>
          <div className="card stack">
            <div className="row">
              <h3 className="grow">Summary for {data.start === data.end ? data.start : `${data.start} to ${data.end}`}</h3>
              <button className="btn small" onClick={async () => { setCopied(await copyText(data.text)); setTimeout(() => setCopied(false), 2000); }}>{copied ? "Copied" : "Copy"}</button>
              <button className="btn small" onClick={() => download(`executive-summary-${data.start}${data.start === data.end ? "" : "_to_" + data.end}.txt`, data.text)}>Download .txt</button>
            </div>
            <div className="summary-text" tabIndex={0} aria-label="Executive summary text">{data.text}</div>
            <div className="muted small">Built directly from saved reports, with no AI rewriting, so totals cannot be invented. Items marked unreviewed are AI output that no manager has checked yet.</div>
          </div>
          <SectionLinks date={date} end={end} onOpen={onOpen} />
        </>
      )}
    </div>
  );
}

/** Quick links to the underlying reports for the selected period. */
function SectionLinks({ date, end, onOpen }: { date: string; end: string; onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<{ id: number; label: string; status_label: string }[]>([]);
  useEffect(() => {
    api.reports({ search: "", status: "", technician: "", date_from: date, date_to: end || date, missing_info: "", action_required: "" })
      .then((r) => setRows(r.map((x) => ({ id: x.id, label: x.label, status_label: x.state === "done" ? x.status_label : x.state }))))
      .catch(() => setRows([]));
  }, [date, end]);
  if (!rows.length) return null;
  return (
    <div className="card stack">
      <h3>Open a report from this period</h3>
      <div className="row">{rows.map((r) => <button key={r.id} className="btn small" onClick={() => onOpen(r.id)}>{r.label} · {r.status_label}</button>)}</div>
    </div>
  );
}
