import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import axios from "axios";
import {
  ShieldCheck,
  LayoutDashboard,
  Files,
  ScanLine,
  ClipboardCheck,
  ChartNoAxesCombined,
  History,
  Settings2,
  ArrowUpRight,
  ArrowRight,
  Check,
  Circle,
  Layers,
  LockKeyhole,
  GitBranch,
  PanelLeftClose,
  RefreshCw,
} from "lucide-react";
import "./styles.css";
import { WorkspacePage } from "./workspace";
import { LiveStatus } from "./live";
const DocumentsPage = React.lazy(() =>
  import("./documents").then((module) => ({ default: module.DocumentsPage })),
);
const ProvidersPage = React.lazy(() =>
  import("./providers").then((module) => ({ default: module.ProvidersPage })),
);
import { AuthProvider, useAuth } from "./auth";
import { AuthScreen, IdentitySettings } from "./identity";

const navigation = [
  { label: "Overview", path: "/", icon: LayoutDashboard },
  { label: "Documents", path: "/documents", icon: Files },
  { label: "Lease intelligence", path: "/intelligence", icon: ScanLine },
  { label: "Review queue", path: "/reviews", icon: ClipboardCheck },
  { label: "Analytics", path: "/analytics", icon: ChartNoAxesCombined },
  { label: "Audit trail", path: "/audit", icon: History },
];
const phases = [
  "Repository & application setup",
  "Backend foundation",
  "Authentication & tenant isolation",
  "Document management",
  "Document processing",
  "Qdrant retrieval & citations",
  "Knowledge graph",
  "LLM & embedding provider interfaces",
  "Typed LangGraph evidence workflow",
  "Evidence-backed lease schemas",
  "Versioned deterministic rules",
  "Amendment / version comparison",
  "Human field review",
  "Transactional audit chain",
  "Connected workspace pages",
  "Authenticated live invalidations",
  "Durable notification transports",
  "Factual analytics & reviewed dates",
  "Security regression controls",
  "Offline labelled evaluation",
  "Prometheus & OpenTelemetry",
  "Mock enterprise integrations",
  "Organization usage metering",
  "Deployment & recovery tooling",
];
function Overview() {
  const readiness = useQuery({
    queryKey: ["readiness"],
    queryFn: async () => {
      const response = await axios.get<{
        status: string;
        checks: Record<string, string>;
      }>("/api/ready", {
        validateStatus: (status) => status === 200 || status === 503,
      });
      return response.data;
    },
    retry: 1,
    refetchInterval: 30000,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () =>
      (await axios.get<{ status: string; version: string }>("/api/health"))
        .data,
    retry: 1,
    refetchInterval: 30000,
  });
  return (
    <>
      <div className="eyebrow">YOUR WORKSPACE, AT A GLANCE</div>
      <div className="heading">
        <div>
          <h1>
            A stronger foundation.
            <br />
            Smarter lease decisions.
          </h1>
          <p>
            Your controlled workspace for evidence-backed lease intelligence.
          </p>
        </div>
        <span className="phase-pill">
          <span /> Stage 24 · Deployment validation pending
        </span>
      </div>
      <section className="hero">
        <div className="hero-copy">
          <div className="hero-tag">
            <ShieldCheck size={15} /> BUILT FOR TRUST, NOT GUESSWORK
          </div>
          <h2>
            Every clause.
            <br />A clear line of sight.
          </h2>
          <p>
            From document to decision, keep evidence, validation, and human
            oversight at the center of every workflow.
          </p>
          <a href="#foundation" className="hero-link">
            Explore your foundation <ArrowRight size={16} />
          </a>
        </div>
        <div className="diagram">
          <div className="doc-card">
            <div className="doc-icon">
              <Files size={24} />
            </div>
            <strong>Commercial lease</strong>
            <span>Source of truth</span>
            <div className="doc-lines">
              <i />
              <i />
              <i />
            </div>
            <div className="doc-footer">
              <LockKeyhole size={12} /> Evidence-first by design
            </div>
          </div>
          <div className="connector" />
          <div className="steps">
            <div>
              <ScanLine size={17} />
              <span>Extract & cite</span>
            </div>
            <div>
              <ShieldCheck size={17} />
              <span>Validate with rules</span>
            </div>
            <div>
              <ClipboardCheck size={17} />
              <span>Review & approve</span>
            </div>
          </div>
        </div>
        <div className="hero-note">
          TARGET WORKFLOW · RULES & APPROVAL REMAIN PLANNED
        </div>
      </section>
      <div className="section-heading" id="foundation">
        <h2>Workspace foundation</h2>
        <span>Development environment</span>
      </div>
      <div className="stat-grid">
        <article className="stat">
          <div className="stat-top">
            API connection <Layers size={17} />
          </div>
          <strong>
            {health.isPending
              ? "Connecting"
              : health.isError
                ? "Offline"
                : "Connected"}
          </strong>
          <div className={"status " + (health.isSuccess ? "good" : "")}>
            <span />
            {health.isSuccess
              ? `FastAPI · v${health.data.version}`
              : health.isError
                ? "Backend is not reachable"
                : "Checking process health"}
          </div>
        </article>
        <article className="stat">
          <div className="stat-top">
            Current milestone <GitBranch size={17} />
          </div>
          <strong>
            Stage 24 <small>/ 24</small>
          </strong>
          <div className="subtle">
            MVP implementation · deployment unverified
          </div>
        </article>
        <article className="stat">
          <div className="stat-top">
            Data environment <LockKeyhole size={17} />
          </div>
          <strong>
            {readiness.isPending
              ? "Checking services"
              : readiness.isError
                ? "Status unavailable"
                : readiness.data.status === "ready"
                  ? "Services ready"
                  : "Services unavailable"}
          </strong>
          <div className="subtle">
            {readiness.data
              ? `PostgreSQL: ${readiness.data.checks.postgresql} · Redis: ${readiness.data.checks.redis} · Storage: ${readiness.data.checks.object_storage}`
              : "Checking PostgreSQL, Redis & storage"}
          </div>
        </article>
      </div>
      <div className="lower-grid">
        <section className="panel">
          <div className="panel-title">
            <h2>Implementation roadmap</h2>
            <span className="tiny-pill">IN PROGRESS</span>
          </div>
          <p className="panel-description">
            Built incrementally. Validated at every step.
          </p>
          <div className="roadmap">
            {phases.map((phase, i) => (
              <div className="roadmap-row" key={phase}>
                <span className={"step-number " + (i < 23 ? "current" : "")}>
                  {i < 23 ? (
                    <Check size={13} />
                  ) : (
                    String(i + 1).padStart(2, "0")
                  )}
                </span>
                <span>{phase}</span>
                <span className={"row-state " + (i < 23 ? "active" : "")}>
                  {i < 23
                    ? "Implemented"
                    : i === 23
                      ? "Current phase"
                      : "Planned"}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section className="panel principles">
          <div className="panel-title">
            <h2>Guardrails come first</h2>
            <ShieldCheck size={19} />
          </div>
          <p className="panel-description">
            The principles behind every future decision.
          </p>
          <div className="principle">
            <div>
              <ScanLine size={18} />
            </div>
            <section>
              <h3>Evidence, not assumptions</h3>
              <p>Extracted fields must link back to their source clause.</p>
            </section>
          </div>
          <div className="principle">
            <div>
              <Settings2 size={18} />
            </div>
            <section>
              <h3>Rules before decisions</h3>
              <p>Deterministic validation, never unchecked AI output.</p>
            </section>
          </div>
          <div className="principle">
            <div>
              <ClipboardCheck size={18} />
            </div>
            <section>
              <h3>People stay in control</h3>
              <p>Critical actions require explicit human approval.</p>
            </section>
          </div>
          <div className="honesty-note">
            <Circle size={12} /> Design principles; workflow services are
            planned.
          </div>
        </section>
      </div>
      <footer>
        <span>
          <ShieldCheck size={14} /> LeaseGuard AI · Evidence-backed.
          Human-controlled.
        </span>
        <button
          onClick={() => {
            void health.refetch();
            void readiness.refetch();
          }}
        >
          <RefreshCw size={13} /> Refresh connection
        </button>
      </footer>
    </>
  );
}
function Planned({ title }: { title: string }) {
  return (
    <>
      <div className="eyebrow">PLATFORM MODULE</div>
      <h1>{title}</h1>
      <section className="panel empty">
        <Layers size={36} />
        <h2>Built on a trusted foundation.</h2>
        <p>
          This module is planned for a later implementation phase.
          <br />
          No simulated data or AI results are shown.
        </p>
        <NavLink to="/">
          Return to overview <ArrowRight size={16} />
        </NavLink>
      </section>
    </>
  );
}
function App() {
  const { identity } = useAuth();
  return (
    <div className="app">
      <aside>
        <NavLink to="/" className="brand">
          <span>
            <ShieldCheck size={24} />
          </span>
          LeaseGuard<span className="ai">AI</span>
        </NavLink>
        <div className="workspace">
          <div className="workspace-avatar">LG</div>
          <div>
            <strong>
              {identity?.organization.name ?? "Development workspace"}
            </strong>
            <span>{identity?.organization.slug ?? "Not signed in"}</span>
          </div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {navigation.map(({ label, path, icon: Icon }) => (
            <NavLink key={path} to={path} end={path === "/"}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
          <NavLink to="/notifications">
            <History size={18} /> Notifications
          </NavLink>
          <NavLink to="/integrations">
            <GitBranch size={18} /> Integrations (mock)
          </NavLink>
          <NavLink to="/billing">
            <ChartNoAxesCombined size={18} /> Usage
          </NavLink>
          <NavLink to="/providers">
            <Settings2 size={18} /> AI providers
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="build-note">
            <div>
              <span /> CONTROLLED MVP BUILD
            </div>
            <p>
              A deliberate start.
              <br />
              An extensible platform.
            </p>
            <span>Stage 24 of 24</span>
            <div className="progress">
              <i />
            </div>
          </div>
          <NavLink to="/settings" className="settings">
            <Settings2 size={18} /> Settings
          </NavLink>
          <div className="profile">
            <span>{identity?.user.first_name.charAt(0) ?? "G"}</span>
            <div>
              <strong>
                {identity
                  ? `${identity.user.first_name} ${identity.user.last_name}`
                  : "Guest workspace"}
              </strong>
              <small>
                {identity?.user.role ?? (
                  <NavLink to="/login">Sign in / Register</NavLink>
                )}
              </small>
            </div>
            <PanelLeftClose size={16} />
          </div>
        </div>
      </aside>
      <div className="content">
        <header>
          <span>
            Workspace <span className="slash">/</span>{" "}
            <strong>LeaseGuard AI</strong>
          </span>
          <div>
            <LiveStatus /> <span className="env-dot" /> Local development{" "}
            <a
              href="/api/health"
              target="_blank"
              rel="noreferrer"
              aria-label="Open API health"
            >
              <ArrowUpRight size={17} />
            </a>
          </div>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route
              path="/documents"
              element={
                <React.Suspense fallback={<p>Loading documents…</p>}>
                  <DocumentsPage />
                </React.Suspense>
              }
            />
            {navigation.slice(2).map((n) => (
              <Route
                key={n.path}
                path={n.path}
                element={
                  <WorkspacePage
                    kind={
                      n.path === "/reviews"
                        ? "reviews"
                        : n.path === "/audit"
                          ? "audit"
                          : n.path === "/analytics"
                            ? "analytics"
                            : "intelligence"
                    }
                  />
                }
              />
            ))}
            <Route
              path="/providers"
              element={
                <React.Suspense fallback={<p>Loading provider diagnostics…</p>}>
                  <ProvidersPage />
                </React.Suspense>
              }
            />
            <Route
              path="/dashboard"
              element={<WorkspacePage kind="analytics" />}
            />
            <Route
              path="/notifications"
              element={<WorkspacePage kind="notifications" />}
            />
            <Route
              path="/integrations"
              element={<WorkspacePage kind="integrations" />}
            />
            <Route path="/billing" element={<WorkspacePage kind="billing" />} />
            <Route path="/settings" element={<IdentitySettings />} />
            <Route
              path="/login"
              element={<AuthScreen key="login" mode="login" />}
            />
            <Route
              path="/register"
              element={<AuthScreen key="register" mode="register" />}
            />
            <Route path="*" element={<Planned title="Page not found" />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
