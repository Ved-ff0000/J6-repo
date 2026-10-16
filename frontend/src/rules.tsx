import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";
export type RuleReport = {
  outcome: string;
  input_sha256: string;
  policy_sha256: string;
  engine_revision?: string;
  evaluated_at: string;
  legal_compliance_established: false;
  decision: "REVIEW_REQUIRED";
  policy: {
    enabled: boolean;
    engine_revision: string;
    rules: {
      id: string;
      name: string;
      version: string;
      severity: string;
      enabled: boolean;
      description: string;
      conditions: { operation: string; scope: string };
      actions: string[];
      created_at: string;
      updated_at: string;
    }[];
  };
  findings: {
    rule_id: string;
    rule_version: string;
    severity: string;
    outcome: string;
    reason: string;
    field_indices: number[];
    chunk_ids: string[];
  }[];
  field_outcomes: {
    index: number;
    status: string;
    passed: boolean | null;
    rules: string[];
  }[];
};
type Evaluation = {
  id: string;
  report: RuleReport;
  created_at: string;
  replayed: boolean;
};
export function RuleReportView({ report }: { report: RuleReport }) {
  return (
    <section>
      <h5>Local rule outcome: {report.outcome}</h5>
      <p className="graph-warning">
        Local deterministic checks, not legal compliance, verified lease facts
        or approval. Missing/ambiguous partial-context inputs can be
        indeterminate. Even PASS remains REVIEW_REQUIRED; semantic confidence is
        unchanged.
      </p>
      <p className="processing-note">
        {report.policy.engine_revision} · {report.evaluated_at}
      </p>
      <details>
        <summary>Rule versions, reasons and field links</summary>
        {report.findings.map((finding, index) => (
          <p className="processing-note" key={index}>
            {finding.rule_id}@{finding.rule_version} · {finding.severity} ·{" "}
            {finding.outcome} · {finding.reason} ·{" "}
            {finding.field_indices.length
              ? `source fields ${finding.field_indices.map((i) => i + 1).join(", ")}`
              : "extraction-level / not applied to an observed field"}
          </p>
        ))}
        <p className="processing-note">
          Field numbers refer to this immutable extraction's ordered fields, not
          page or clause numbers.
        </p>
      </details>
      <details>
        <summary>Pinned policy and input fingerprints</summary>
        <p className="checksum">Input: {report.input_sha256}</p>
        <p className="checksum">Policy: {report.policy_sha256}</p>
        {report.policy.rules.map((rule) => (
          <p className="processing-note" key={rule.id}>
            {rule.id}@{rule.version} · {rule.name} ·{" "}
            {rule.enabled ? "enabled" : "disabled"} · {rule.severity} ·{" "}
            {rule.conditions.operation} · {rule.description} · actions
            (annotations only): {rule.actions.join(", ")} · created{" "}
            {rule.created_at} / updated {rule.updated_at}
          </p>
        ))}
      </details>
    </section>
  );
}
export function RulePanel({
  runId,
  inline,
}: {
  runId: string;
  inline?: RuleReport | null;
}) {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const key = ["rule-evaluations", identity!.organization.id, runId];
  const base = `/api/v1/workflow-runs/${runId}/rule-evaluations`;
  const catalog = useQuery({
    queryKey: ["rule-catalog", identity!.organization.id],
    queryFn: () =>
      api<{ policy: { enabled: boolean } }>({ url: "/api/v1/rules/catalog" }),
    retry: false,
  });
  const history = useQuery({
    queryKey: key,
    queryFn: () => api<Evaluation[]>({ url: base, params: { limit: 10 } }),
    retry: false,
  });
  const action = useMutation({
    retry: false,
    mutationFn: (requestKey: string) =>
      api<Evaluation>({
        url: base,
        method: "POST",
        timeout: 30000,
        data: { request_key: requestKey },
      }),
    onSuccess: (value) =>
      setNotice(
        `Local rules evaluated${value.replayed ? " (stored snapshot replayed)" : ""}: ${value.report.outcome}. Original extraction is unchanged; no model calls or approval.`,
      ),
    onError: () =>
      setNotice(
        "Evaluation request failed or its commit is uncertain. Refresh history or retry the same key; do not interpret this as a rule pass.",
      ),
    onSettled: () => cache.invalidateQueries({ queryKey: key }),
  });
  function run(reuse = false) {
    const requestKey = reuse && lastKey ? lastKey : crypto.randomUUID();
    setLastKey(requestKey);
    setNotice("");
    action.mutate(requestKey);
  }
  return (
    <section className="rule-panel">
      <h4>Versioned deterministic checks</h4>
      {inline && (
        <>
          <p className="processing-note">
            Rule snapshot recorded during extraction:
          </p>
          <RuleReportView report={inline} />
        </>
      )}
      <p className="processing-note">
        Evaluate the current server rule snapshot against this stored
        extraction. This is local and makes no LLM, vector-service or other
        external calls. New evaluations are separate records; historical
        extraction JSON is never rewritten. New evaluation requires the latest
        eligible source.
      </p>
      {catalog.data && !catalog.data.policy?.enabled && (
        <p>
          Rule execution is disabled on the server. Historical findings remain
          readable.
        </p>
      )}
      <button
        className="secondary-button"
        disabled={!catalog.data?.policy?.enabled || action.isPending}
        onClick={() => run()}
      >
        Evaluate current rule snapshot
      </button>
      {lastKey && (
        <button
          className="secondary-button"
          disabled={action.isPending}
          onClick={() => run(true)}
        >
          Retry same rule key
        </button>
      )}
      <button
        className="secondary-button"
        disabled={history.isFetching}
        onClick={() => {
          void history.refetch();
          void catalog.refetch();
        }}
      >
        Refresh rule history
      </button>
      {notice && <p role="status">{notice}</p>}
      {(action.isError || history.isError || catalog.isError) && (
        <p role="alert">
          {errorMessage(action.error ?? history.error ?? catalog.error)}
        </p>
      )}
      {history.isPending ? (
        <p>Loading rule history…</p>
      ) : history.data?.length ? (
        history.data.map((record) => (
          <details key={record.id}>
            <summary>
              Saved evaluation · {record.report.outcome} · {record.created_at}
            </summary>
            <RuleReportView report={record.report} />
          </details>
        ))
      ) : (
        !history.isError && <p>No separate rule evaluations recorded.</p>
      )}
    </section>
  );
}
