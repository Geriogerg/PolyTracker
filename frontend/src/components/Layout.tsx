import { createContext, useContext, useEffect, useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAlerts, useOverview } from "../lib/api";
import { useLive } from "../lib/live";

const ThemeCtx = createContext<{ theme: string; toggle: () => void }>({ theme: "dark", toggle: () => {} });
export const useTheme = () => useContext(ThemeCtx);

function initialTheme(): string {
  try {
    return localStorage.getItem("pt-theme") ?? "dark";
  } catch {
    return "dark";
  }
}

function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
      <path d="M9 20 22 10 23 24Z" fill="none" stroke="var(--ink-3)" strokeWidth="1.5" />
      <circle cx="9" cy="20" r="5" fill="var(--accent)" stroke="var(--page)" strokeWidth="2" />
      <circle cx="22" cy="10" r="6" fill="var(--crit)" stroke="var(--page)" strokeWidth="2" />
      <circle cx="23" cy="24" r="3.5" fill="var(--warn)" stroke="var(--page)" strokeWidth="2" />
    </svg>
  );
}

export default function Layout() {
  const [theme, setTheme] = useState(initialTheme);
  const { connected, toasts, dismiss } = useLive();
  const { data: ov } = useOverview();
  const { data: alerts } = useAlerts();
  const unread = alerts?.filter((a) => !a.acknowledged).length ?? 0;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("pt-theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);

  const nav = [
    ["/", "Overview"],
    ["/clusters", "Clusters"],
    ["/wallets", "Wallets"],
    ["/markets", "Markets"],
    ["/alerts", "Alerts"],
    ["/settings", "Settings"],
    ["/status", "Status"],
  ];

  return (
    <ThemeCtx.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>
      <header className="topbar">
        <Link to="/" className="brand" aria-label="PolyTracker home">
          <Mark />
          <span>POLYTRACKER</span>
        </Link>
        <nav className="nav" aria-label="Main">
          {nav.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label}
              {to === "/alerts" && unread > 0 && <span className="count">{unread}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          {ov?.demo && <span className="pill demo" title="Synthetic data, no network calls">Demo data</span>}
          <span className={`live ${connected ? "on" : ""}`} title={connected ? "Receiving live trades" : "Live stream disconnected"}>
            <i />
            <span>{connected ? "Live" : "Offline"}</span>
          </span>
          <button className="icon-btn" onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
      <div className="toasts" aria-live="polite">
        {toasts.map((a) => (
          <div className="toast" key={a.id} role="status">
            <div className="flow" style={{ justifyContent: "space-between" }}>
              <span className="t">{a.title}</span>
              <button className="icon-btn" style={{ width: 24, height: 24 }} onClick={() => dismiss(a.id)} aria-label="Dismiss">×</button>
            </div>
            <div className="m">{a.message}</div>
            <div className="small" style={{ marginTop: 6 }}>
              <Link to={a.kind === "cluster" ? `/clusters/${a.ref}` : `/wallets/${a.ref}`} onClick={() => dismiss(a.id)}>Open →</Link>
            </div>
          </div>
        ))}
      </div>
    </ThemeCtx.Provider>
  );
}
