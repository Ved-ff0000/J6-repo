import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";

type Config = {
  enabled: boolean;
  credential_configured: boolean;
  provider: string;
  model: string;
  profile: string;
  embedding: string;
  probe_limit: number;
  probe_window_seconds: number;
  prompt_name: string;
  prompt_version: string;
};
type Run = {
  id: string;
  request_key: string;
  status: string;
  configured_model: string;
  returned_model: string | null;
  provider_response_id: string | null;
  prompt_name: string;
  prompt_version: string;
  temperature: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number | null;
  error_code: string | null;
  output_sha256: string | null;
  created_at: string;
  replayed: boolean;
};

export function ProvidersPage() {
  const { identity } = useAuth();
  if (!identity)
    return (
      <section className="panel empty">
        <h1>AI provider diagnostics</h1>
        <p>Sign in to inspect your organization's provider configuration.</p>
        <Link to="/login">Sign in →</Link>
      </section>
    );
  if (!["ORG_ADMIN", "SUPER_ADMIN"].includes(identity.user.role))
    return (
      <section className="panel empty">
        <h1>Administrator access required</h1>
        <p>
          Provider diagnostics can incur external charges. Only organization
          administrators can run or inspect them.
        </p>
      </section>
    );
  return (
    <ProviderWorkspace
      key={`${identity.organization.id}-${identity.user.id}`}
    />
  );
}
function ProviderWorkspace() {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const key = ["ai-providers", identity!.organization.id];
  const config = useQuery({
    queryKey: [...key, "config"],
    queryFn: () => api<Config>({ url: "/api/v1/ai/config" }),
    retry: false,
  });
  const history = useQuery({
    queryKey: [...key, "history"],
    queryFn: () =>
      api<Run[]>({ url: "/api/v1/ai/probes", params: { limit: 20 } }),
    retry: false,
  });
  const probe = useMutation({
    retry: false,
    mutationFn: (requestKey: string) =>
      api<Run>({
        url: "/api/v1/ai/probes",
        method: "POST",
        timeout: 75000,
        data: { request_key: requestKey, acknowledge_external_request: true },
      }),
    onSuccess: (run) =>
      setNotice(
        run.status === "SUCCEEDED"
          ? `Synthetic structured-output diagnostic succeeded${run.replayed ? " (recorded result replayed; no new call)" : ""}. This verifies neither lease facts nor future provider availability.`
          : run.status === "STARTED"
            ? "This request is already started or has an unknown outcome. It was not sent again. Inspect provider records before requesting a new paid call."
            : `Recorded diagnostic failed: ${run.error_code ?? "unknown error"}. Replaying the same key will not send it again.`,
      ),
    onError: () =>
      setNotice(
        "Request failed or its outcome is uncertain. Refresh history or safely retry the same request key before creating a new paid diagnostic.",
      ),
    onSettled: () => cache.invalidateQueries({ queryKey: [...key, "history"] }),
  });
  function run(reuse = false) {
    if (
      !window.confirm(
        "Send a fixed synthetic prompt to the configured external provider? This may incur charges. No lease content is sent. No automatic retries occur.",
      )
    )
      return;
    const requestKey = reuse && lastKey ? lastKey : crypto.randomUUID();
    setLastKey(requestKey);
    setNotice("");
    probe.mutate(requestKey);
  }
  return (
    <>
      <div className="eyebrow">CONTROLLED PROVIDER BOUNDARY · PHASE 08</div>
      <h1>AI provider diagnostics</h1>
      <p className="graph-warning">
        Synthetic diagnostic only. No chat, lease extraction, approval or
        automated action. Enabling a key is not proof of a live connection. A
        successful check is only a historical response from the configured
        service.
      </p>
      <section className="panel provider-panel">
        <h2>Server-side configuration</h2>
        {config.isPending ? (
          <p>Loading provider configuration…</p>
        ) : config.isError ? (
          <p role="alert">{errorMessage(config.error)}</p>
        ) : (
          <>
            <dl className="document-metadata">
              <dt>Generation provider</dt>
              <dd>
                {config.data.provider} · {config.data.model}
              </dd>
              <dt>Generation gate</dt>
              <dd>
                {config.data.enabled
                  ? "Enabled, not automatically verified"
                  : "Disabled; no generation requests permitted"}
              </dd>
              <dt>Credential</dt>
              <dd>
                {config.data.credential_configured
                  ? "Configured on server (not displayed)"
                  : "Not configured"}
              </dd>
              <dt>Embeddings</dt>
              <dd>{config.data.embedding}</dd>
              <dt>Diagnostic prompt</dt>
              <dd>
                {config.data.prompt_name} · {config.data.prompt_version}
              </dd>
              <dt>Request limits</dt>
              <dd>
                {config.data.probe_limit} new requests per{" "}
                {config.data.probe_window_seconds} seconds per organization; one
                outbound diagnostic at a time.
              </dd>
            </dl>
            <p className="processing-note">
              Configure LEASEGUARD_LLM_ENABLED, LEASEGUARD_LLM_URL,
              LEASEGUARD_LLM_MODEL and LEASEGUARD_LLM_API_KEY securely on the
              server and restart it. Never paste keys here or into chat. The
              default is off. Provider network calls are never part of automatic
              readiness checks.
            </p>
            <button
              className="primary-button"
              disabled={!config.data.enabled || probe.isPending}
              onClick={() => run()}
            >
              {probe.isPending
                ? "Running bounded diagnostic…"
                : "Run new synthetic diagnostic"}
            </button>
            {lastKey && (
              <button
                className="secondary-button"
                disabled={!config.data.enabled || probe.isPending}
                onClick={() => run(true)}
              >
                Safely retry same request key
              </button>
            )}
          </>
        )}
        {probe.isError && <p role="alert">{errorMessage(probe.error)}</p>}
        {notice && (
          <p role="status" className="access-notice">
            {notice}
          </p>
        )}
        {lastKey && (
          <p className="processing-note">
            Last request key: {lastKey}. A stored key is never automatically
            sent upstream twice, even after failure.
          </p>
        )}
      </section>
      <section className="panel provider-panel">
        <div className="section-heading">
          <h2>Organization diagnostic history</h2>
          <button
            className="secondary-button"
            disabled={history.isFetching}
            onClick={() => void history.refetch()}
          >
            Refresh history
          </button>
        </div>
        <p className="processing-note">
          Newest 20 attempts. Tokens are service-reported usage, not confidence
          or an invoice. Missing or unretained usage is unknown, not zero.
          STARTED can mean an interrupted/unknown outcome; there is no automatic
          resume or paid retry.
        </p>
        {history.isPending ? (
          <p>Loading diagnostic history…</p>
        ) : history.isError ? (
          <p role="alert">{errorMessage(history.error)}</p>
        ) : history.data.length === 0 ? (
          <p>
            No provider diagnostics have been recorded. No live success is
            assumed.
          </p>
        ) : (
          history.data.map((run) => (
            <article className="retrieval-hit" key={run.id}>
              <h3>
                {run.status} · {run.configured_model}
              </h3>
              <p className="processing-note">
                {run.created_at} · run {run.id}
                {run.error_code && ` · ${run.error_code}`}
              </p>
              <p className="processing-note">
                Returned model: {run.returned_model ?? "Not recorded"} · tokens:{" "}
                {run.total_tokens === null
                  ? "Not recorded"
                  : `${run.total_tokens} (${run.input_tokens} in / ${run.output_tokens} out)`}{" "}
                · duration:{" "}
                {run.duration_ms === null
                  ? "Unknown"
                  : `${Math.round(run.duration_ms)} ms`}
              </p>
              <p className="processing-note">
                Prompt {run.prompt_name} · {run.prompt_version} · temperature{" "}
                {run.temperature}. No generated content or customer document is
                stored in this record.
              </p>
              {run.output_sha256 && (
                <details>
                  <summary>Validated output fingerprint</summary>
                  <code className="checksum">{run.output_sha256}</code>
                </details>
              )}
            </article>
          ))
        )}
      </section>
    </>
  );
}
