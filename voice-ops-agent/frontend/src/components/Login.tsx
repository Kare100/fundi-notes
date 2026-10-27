import { useState } from "react";
import { ApiError, api, credentials } from "../api";

export default function Login({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState("manager");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    credentials.set(user, password);
    try {
      await api.config(); // any authenticated endpoint proves the credentials
      onDone();
    } catch (err) {
      credentials.clear();
      setError(err instanceof ApiError && err.status === 401 ? "Username or password is incorrect." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card login stack" onSubmit={submit}>
      <h1>Field Reports</h1>
      <p className="muted small" style={{ margin: 0 }}>
        Sign in with the manager login set in the backend <span className="mono">.env</span> file.
      </p>
      <label className="field">
        Username
        <input type="text" value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" required />
      </label>
      <label className="field">
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      {error && <div className="banner bad" role="alert">{error}</div>}
      <button className="btn primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
    </form>
  );
}
