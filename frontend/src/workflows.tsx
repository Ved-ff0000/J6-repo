import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";
import { LeaseFields, type LeaseResult } from "./lease-extraction";
import { RulePanel, type RuleReport } from "./rules";
import { ConflictPanel } from "./conflicts";
type Mode = "CANDIDATES" | "LEASE_FIELDS";

type Config = {
  enabled: boolean;
  revision: string;
  timeout_seconds: number;
  lease_extraction_enabled?: boolean;
};
type Candidate = {
  kind: string;
  value: string;
  assertion: string;
  quote_start: number;
  quote_end: number;
  citation: {
    page: number;
    page_number_kind: string;
    start_offset: number;
    evidence: string;
    version_number: number;
  };
};
type Run = {
  id: string;
  request_key: string;
  status: string;
  stage: string;
  extraction_mode?: Mode;
  error_code: string | null;
  created_at: string;
  trace: { name: string; status: string; timestamp: string }[];
  calls: {
    prompt_name: string;
    returned_model: string;
    usage: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    } | null;
  }[];
  result: {
    classification: { kind: string };
    lease_extraction?: LeaseResult | null;
    rule_report?: RuleReport | null;
    candidates: Candidate[];
    confidence: { semantic_score: null; evidence_coverage: number | null };
    source_chunk_count: number;
    total_chunk_count: number;
    retrieved_chunk_count: number;
    decision: string;
    review_queue_created: false;
  } | null;
  replayed: boolean;
};
type Props = {
  documentId: string;
  versionNumber: number;
  isLatest: boolean;
  archived: boolean;
};
export function WorkflowPanel(props: Props) {
  const { identity } = useAuth();
  if (!identity || !["ORG_ADMIN", "SUPER_ADMIN"].includes(identity.user.role))
    return null;
  return (
    <Workspace
      key={`${identity.organization.id}-${identity.user.id}-${props.documentId}-${props.versionNumber}`}
      {...props}
    />
  );
}
function Workspace({ documentId, versionNumber, isLatest, archived }: Props) {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [lastRequest, setLastRequest] = useState<{
    requestKey: string;
    mode: Mode;
  } | null>(null);
  const [selectedMode, setSelectedMode] = useState<Mode>("CANDIDATES");
  const lastKey = lastRequest?.requestKey;
  const [notice, setNotice] = useState("");
  const base = `/api/v1/documents/${documentId}/versions/${versionNumber}/workflow-runs`;
  const key = [
    "workflow-runs",
    identity!.organization.id,
    documentId,
    versionNumber,
  ];
  const config = useQuery({
    queryKey: ["workflow-config", identity!.organization.id],
    queryFn: () => api<Config>({ url: "/api/v1/workflows/config" }),
    retry: false,
  });
  const action = useMutation({
    retry: false,
    mutationFn: ({ requestKey, mode }: { requestKey: string; mode: Mode }) =>
      api<Run>({
        url: base,
        method: "POST",
        timeout: 260000,
        data: {
          request_key: requestKey,
          acknowledge_external_request: true,
          extraction_mode: mode,
        },
      }),
    onSuccess: (run) =>
      setNotice(
        run.status === "REVIEW_REQUIRED"
          ? `Bounded workflow completed${run.replayed ? " (recorded result replayed)" : ""}. Candidates remain unverified; no approval or review task was created.`
          : run.status === "STARTED"
            ? "Recorded request is pending or its outcome is unknown. It was not automatically resumed. Inspect history and provider records before spending on a new key."
            : `Workflow failed: ${run.error_code ?? "unknown"}. Paid calls may still have occurred. The same stored key will not send them again.`,
      ),
    onError: () =>
      setNotice(
        "The request failed or its outcome is uncertain. Refresh history or safely retry the same key before requesting a new paid run.",
      ),
    onSettled: () => cache.invalidateQueries({ queryKey: key }),
  });
  const history = useQuery({
    queryKey: key,
    queryFn: () => api<Run[]>({ url: base, params: { limit: 20 } }),
    enabled: !archived,
    retry: false,
    refetchInterval: action.isPending ? 2000 : false,
  });
  function run(reuse = false) {
    if (
      !window.confirm(
        "Send bounded text from this document version to the configured external AI provider? Up to two generation calls and a retrieval embedding request may incur charges. Use only approved synthetic data while malware scanning is unavailable. Candidates are unverified, not legal advice or approval. Continue?",
      )
    )
      return;
    const request =
      reuse && lastRequest
        ? lastRequest
        : { requestKey: crypto.randomUUID(), mode: selectedMode };
    setLastRequest(request);
    setNotice("");
    action.mutate(request);
  }
  return (
    <section className="provider-panel panel">
      <div className="eyebrow">PHASE 10 · EVIDENCE-BACKED LEASE SCHEMAS</div>
      <h3>Bounded evidence workflow</h3>
      <p className="graph-warning">
        Choose title/heading candidates or twelve-group lease-field proposals.
        The model receives partial source text; empty groups do not establish
        absence. Exact source linkage does not verify the proposed label, legal
        meaning or completeness. All completed runs require human review; a
        routing label does not create a review task.
      </p>
      {config.isError && <p role="alert">{errorMessage(config.error)}</p>}
      {config.data && !config.data.enabled && (
        <p>
          Workflow generation is disabled. Earlier document features still work
          without it.
        </p>
      )}
      <label>
        Extraction mode
        <select
          aria-label="Extraction mode"
          disabled={action.isPending}
          value={selectedMode}
          onChange={(event) => setSelectedMode(event.target.value as Mode)}
        >
          <option value="CANDIDATES">Title and heading candidates</option>
          <option
            value="LEASE_FIELDS"
            disabled={!config.data?.lease_extraction_enabled}
          >
            Lease field proposals (12 groups)
          </option>
        </select>
      </label>
      {selectedMode === "LEASE_FIELDS" && (
        <p className="processing-note">
          Lease extraction sends selected source text to the provider. It
          requires explicit server enablement and at least 1024 output tokens;
          missing values, ambiguous dates, currencies and conditions are not
          guessed.
        </p>
      )}
      <button
        className="primary-button"
        disabled={
          !config.data?.enabled ||
          (selectedMode === "LEASE_FIELDS" &&
            !config.data?.lease_extraction_enabled) ||
          !isLatest ||
          archived ||
          action.isPending
        }
        onClick={() => run()}
      >
        {action.isPending
          ? "Running bounded workflow…"
          : selectedMode === "LEASE_FIELDS"
            ? "Run lease-field extraction"
            : "Run new evidence workflow"}
      </button>
      {lastKey && (
        <button
          className="secondary-button"
          disabled={
            !config.data?.enabled ||
            (lastRequest?.mode === "LEASE_FIELDS" &&
              !config.data?.lease_extraction_enabled) ||
            archived ||
            action.isPending
          }
          onClick={() => run(true)}
        >
          Safely retry workflow key
        </button>
      )}
      <p className="processing-note">
        Requires the latest parsed, policy-eligible version and a ready vector
        index. A run conservatively reserves two generation admission units
        shared with diagnostics, even if it stops early. No automatic model
        retries, resume, tools or approval. The server deadline is{" "}
        {config.data?.timeout_seconds ?? "not yet loaded"} seconds.
      </p>
      {lastKey && (
        <p className="processing-note">
          Last workflow key: {lastKey} · {lastRequest?.mode}
        </p>
      )}
      {action.isError && <p role="alert">{errorMessage(action.error)}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="section-heading">
        <h3>Version workflow history</h3>
        <button
          className="secondary-button"
          disabled={archived || history.isFetching}
          onClick={() => void history.refetch()}
        >
          Refresh workflows
        </button>
      </div>
      {archived ? (
        <p>
          Source is archived; workflow results are retained but not exposed
          here.
        </p>
      ) : history.isPending ? (
        <p>Loading workflow history…</p>
      ) : history.isError ? (
        <p role="alert">{errorMessage(history.error)}</p>
      ) : !history.data.length ? (
        <p>No workflow runs recorded. No AI success is assumed.</p>
      ) : (
        history.data.map((item) => (
          <article key={item.id} className="retrieval-hit">
            <h4>
              {item.status} · {item.stage} ·{" "}
              {item.extraction_mode ?? "CANDIDATES"}
            </h4>
            <p className="processing-note">
              {item.created_at} · {item.id}
              {item.error_code && ` · ${item.error_code}`}
            </p>
            {item.status === "STARTED" && (
              <p>Pending or unknown outcome. Never automatically resumed.</p>
            )}
            {item.result && (
              <>
                <p>
                  Proposed classification: {item.result.classification.kind}.
                  Sampled {item.result.source_chunk_count} of{" "}
                  {item.result.total_chunk_count} source chunks; retrieved{" "}
                  {item.result.retrieved_chunk_count} chunks.
                </p>
                <p>
                  Semantic confidence: not measured. Exact evidence coverage:{" "}
                  {item.result.confidence.evidence_coverage === null
                    ? "Not applicable"
                    : `${Math.round(item.result.confidence.evidence_coverage * 100)}% of returned candidates only`}
                  . This is not document coverage or accuracy.
                </p>
                {item.result.lease_extraction && (
                  <>
                    <LeaseFields
                      result={item.result.lease_extraction}
                      rulesEvaluated={!!item.result.rule_report}
                    />
                    <RulePanel
                      runId={item.id}
                      inline={item.result.rule_report}
                    />
                    <ConflictPanel runId={item.id} />
                  </>
                )}
                {item.extraction_mode === "LEASE_FIELDS" &&
                  !item.result.lease_extraction && (
                    <p>
                      Lease extraction was not performed for this
                      classification. No lease-field absence or completeness is
                      established.
                    </p>
                  )}
                {!item.result.lease_extraction &&
                  !item.result.candidates.length && (
                    <p>No title or heading candidates returned.</p>
                  )}
                {item.result.candidates.map((candidate, index) => (
                  <div key={index}>
                    <strong>
                      {candidate.kind}: {candidate.value}
                    </strong>
                    <p className="processing-note">
                      {candidate.assertion} · version{" "}
                      {candidate.citation.version_number} ·{" "}
                      {candidate.citation.page_number_kind.toLowerCase()} page{" "}
                      {candidate.citation.page} · offsets{" "}
                      {candidate.quote_start}–{candidate.quote_end}
                    </p>
                    <blockquote>
                      {candidate.citation.evidence.slice(
                        candidate.quote_start - candidate.citation.start_offset,
                        candidate.quote_end - candidate.citation.start_offset,
                      )}
                    </blockquote>
                  </div>
                ))}
              </>
            )}
            <details>
              <summary>Recorded stage trace and provider usage</summary>
              {item.trace.map((event, index) => (
                <p className="processing-note" key={index}>
                  {event.name} · {event.status} · {event.timestamp}
                </p>
              ))}
              {!item.calls.length && (
                <p>
                  Provider usage not recorded; this does not establish zero
                  cost.
                </p>
              )}
              {item.calls.map((call, index) => (
                <p className="processing-note" key={index}>
                  {call.prompt_name} · {call.returned_model} ·{" "}
                  {call.usage
                    ? `${call.usage.total_tokens} provider-reported tokens`
                    : "Usage not recorded"}
                </p>
              ))}
            </details>
          </article>
        ))
      )}
    </section>
  );
}
