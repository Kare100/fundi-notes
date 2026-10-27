import { useRef, useState } from "react";
import { api } from "../api";
import type { AppConfig, Sample } from "../types";
import { today } from "../util";

interface Props {
  config: AppConfig;
  samples: Sample[];
  onCreated: (id: number) => void;
}

export default function UploadPanel({ config, samples, onCreated }: Props) {
  const [tab, setTab] = useState<"audio" | "sample" | "text">(config.demo.transcription ? "sample" : "audio");
  const [reportedOn, setReportedOn] = useState(today());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [over, setOver] = useState(false);
  const [sampleId, setSampleId] = useState(samples[0]?.id ?? "");
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const sample = samples.find((s) => s.id === sampleId);
  const { extensions, max_mb, max_minutes } = config.upload;

  async function run(fn: () => Promise<{ id: number }>) {
    setBusy(true);
    setError("");
    try {
      const { id } = await fn();
      onCreated(id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function pick(file: File | undefined) {
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!extensions.includes(ext)) return setError(`Unsupported file type. Allowed: ${extensions.map((e) => "." + e).join(", ")}.`);
    if (file.size === 0) return setError("That file is empty.");
    if (file.size > max_mb * 1024 * 1024) return setError(`That file is larger than the ${max_mb} MB limit.`);
    void run(() => api.upload(file, reportedOn));
  }

  return (
    <div className="card stack">
      <div className="row">
        <h2 className="grow">Add a voice note</h2>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          Date of report
          <input type="date" value={reportedOn} onChange={(e) => setReportedOn(e.target.value)} style={{ width: "auto" }} />
        </label>
      </div>

      <div className="tabs" role="tablist">
        {(["audio", "sample", "text"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => { setTab(t); setError(""); }}>
            {t === "audio" ? "Upload recording" : t === "sample" ? "Sample transcripts (demo)" : "Paste text (demo)"}
          </button>
        ))}
      </div>

      {tab === "audio" && (
        <>
          {config.demo.transcription && (
            <div className="banner demo">
              <strong>Demo mode.</strong> No speech-to-text service is configured, so audio cannot be transcribed and uploads are refused rather than faked.
              Use the sample transcripts, or set <span className="mono">TRANSCRIPTION_PROVIDER</span> in backend/.env.
            </div>
          )}
          <div
            className={`dropzone ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}
          >
            {busy ? (
              <span><span className="spinner" /> Uploading...</span>
            ) : (
              <>
                <p style={{ margin: "0 0 8px" }}>Drop an audio file here, or</p>
                <button className="btn primary" onClick={() => input.current?.click()}>Choose file</button>
                <input ref={input} type="file" hidden accept={extensions.map((e) => "." + e).join(",")} onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
                <p className="muted small" style={{ margin: "8px 0 0" }}>
                  {extensions.map((e) => e.toUpperCase()).join(", ")} up to {max_mb} MB and {max_minutes} minutes.
                </p>
              </>
            )}
          </div>
        </>
      )}

      {tab === "sample" && (
        <>
          <div className="banner demo">Demo data. These are written transcripts, no audio and no speech-to-text service is involved.</div>
          <label className="field">
            Sample
            <select value={sampleId} onChange={(e) => setSampleId(e.target.value)}>
              {samples.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
          </label>
          {sample && (
            <>
              <p className="muted small" style={{ margin: 0 }}>{sample.description}</p>
              <div className="transcript">{sample.transcript}</div>
            </>
          )}
          <div className="row">
            <button className="btn primary" disabled={busy || !sample} onClick={() => run(() => api.demo({ sample_id: sampleId, reported_on: reportedOn }))}>
              {busy ? "Processing..." : "Process this sample"}
            </button>
          </div>
        </>
      )}

      {tab === "text" && (
        <>
          <div className="banner demo">
            Demo data. Type or paste what a technician might say. With the mock extractor only a few patterns are recognised; configure an LLM provider for full extraction.
          </div>
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="This is Grace, job 4412 at ..." aria-label="Transcript text" />
          <div className="row">
            <button className="btn primary" disabled={busy || text.trim().length < 20} onClick={() => run(() => api.demo({ text, reported_on: reportedOn }))}>
              {busy ? "Processing..." : "Process text"}
            </button>
            {text.trim().length < 20 && text.length > 0 && <span className="muted small">At least 20 characters.</span>}
          </div>
        </>
      )}

      {error && <div className="banner bad" role="alert">{error}</div>}

      <details className="small muted">
        <summary>What data leaves this server?</summary>
        <ul>{config.data_sharing.map((l) => <li key={l}>{l}</li>)}</ul>
        <p>
          Original audio is {config.retention.mode === "keep_days" ? `kept for ${config.retention.days} days, then deleted` : "deleted as soon as processing finishes"}.
        </p>
      </details>
    </div>
  );
}
