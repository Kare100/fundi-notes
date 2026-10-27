import { useCallback, useEffect, useState } from "react";
import { ApiError, api, credentials, setUnauthorizedHandler } from "./api";
import type { AppConfig, Sample } from "./types";
import Dashboard from "./components/Dashboard";
import Login from "./components/Login";
import ReportView from "./components/ReportView";
import SummaryView from "./components/SummaryView";

type View = { name: "dashboard" } | { name: "summary" } | { name: "report"; id: number };

function parseHash(): View {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (h === "summary") return { name: "summary" };
  const m = /^report\/(\d+)$/.exec(h);
  return m ? { name: "report", id: Number(m[1]) } : { name: "dashboard" };
}

export default function App() {
  const [phase, setPhase] = useState<"loading" | "login" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [view, setView] = useState<View>(parseHash());

  const go = useCallback((v: View) => {
    window.location.hash = v.name === "dashboard" ? "" : v.name === "summary" ? "#/summary" : `#/report/${v.id}`;
    setView(v);
  }, []);

  useEffect(() => {
    const onHash = () => setView(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const boot = useCallback(async () => {
    try {
      const health = await api.health();
      if (health.auth_required && !credentials.get()) return setPhase("login");
      const [cfg, smp] = await Promise.all([api.config(), api.samples()]);
      setConfig(cfg);
      setSamples(smp);
      setPhase("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return setPhase("login");
      setError((e as Error).message);
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => { credentials.clear(); setPhase("login"); });
    void boot();
  }, [boot]);

  if (phase === "loading") return <div className="shell"><p className="muted" style={{ paddingTop: 40 }}><span className="spinner" /> Loading...</p></div>;
  if (phase === "login") return <div className="shell"><Login onDone={boot} /></div>;
  if (phase === "error" || !config) {
    return (
      <div className="shell stack" style={{ paddingTop: 40 }}>
        <div className="banner bad" role="alert">{error || "Something went wrong."}</div>
        <p className="muted">Is the backend running? Start it with <span className="mono">uvicorn app.main:app --reload</span> in the backend folder.</p>
        <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => { setPhase("loading"); void boot(); }}>Try again</button>
      </div>
    );
  }

  const nav = (name: View["name"], label: string) => (
    <button className={`tab ${view.name === name || (name === "dashboard" && view.name === "report") ? "active" : ""}`} onClick={() => go({ name } as View)}>{label}</button>
  );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>Field Reports</h1>
          <small>Voice notes to operations reports</small>
        </div>
        <nav className="tabs" style={{ margin: 0, border: "none" }} aria-label="Main">
          {nav("dashboard", "Dashboard")}
          {nav("summary", "Daily summary")}
        </nav>
        <div className="row">
          {config.demo.transcription && <span className="badge demo" title="No speech-to-text service configured">Demo transcription</span>}
          {config.demo.llm && <span className="badge demo" title="Mock extractor, not a real AI model">Demo extraction</span>}
          <button className="btn small" onClick={() => { credentials.clear(); setPhase("login"); }}>Sign out</button>
        </div>
      </header>
      {view.name === "dashboard" && <Dashboard config={config} samples={samples} onOpen={(id) => go({ name: "report", id })} />}
      {view.name === "summary" && <SummaryView onOpen={(id) => go({ name: "report", id })} />}
      {view.name === "report" && <ReportView key={view.id} id={view.id} onBack={() => go({ name: "dashboard" })} />}
    </div>
  );
}
