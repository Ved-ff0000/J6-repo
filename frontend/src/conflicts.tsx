import { useState, useId } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";
import type { LeaseResult } from "./lease-extraction";
type Source = {
  run_id: string;
  document_id: string;
  version_id: string;
  version_number: number;
  checksum: string;
  version_created_at: string;
  parent_document_id: string | null;
  document_type: string;
};
export type ComparisonReport = {
  engine_revision: string;
  relationship: string;
  left_source: Source;
  right_source: Source;
  left_input_sha256: string;
  right_input_sha256: string;
  evaluated_at: string;
  outcome: string;
  latest_uploaded_version_id: string | null;
  authority_status: "NOT_ESTABLISHED";
  decision: "REVIEW_REQUIRED";
  completeness_established: false;
  previous_values_overwritten: false;
  findings: {
    group: string;
    field: string;
    outcome: string;
    reason: string;
    left: LeaseResult["fields"];
    right: LeaseResult["fields"];
  }[];
};
type Saved = {
  id: string;
  report: ComparisonReport;
  created_at: string;
  replayed: boolean;
};
type Input = { request_key: string; left_run_id: string; right_run_id: string };
export function ComparisonView({ report }: { report: ComparisonReport }) {
  return (
    <section>
      <h5>{report.outcome}</h5>
      <p className="graph-warning">
        Potential differences between partial, unverified model proposals — not
        established legal conflicts. Authority: NOT_ESTABLISHED. Newer uploads
        and amendment labels do not prove supersession. No values were
        overwritten; REVIEW_REQUIRED. No differences observed is not proof that
        the documents agree or are complete.
      </p>
      <p className="processing-note">
        {report.engine_revision} · {report.relationship} · {report.evaluated_at}
      </p>
      <ol className="comparison-timeline" aria-label="Compared source versions">
        {([report.left_source, report.right_source] as Source[]).map(
          (source, i) => (
            <li key={i}>
              <strong>
                {i === 0 ? "Left" : "Right"} source · version{" "}
                {source.version_number}
              </strong>
              <p className="processing-note">
                Document {source.document_id} · extraction {source.run_id} ·
                uploaded {source.version_created_at} · user-supplied type{" "}
                {source.document_type}
                {source.parent_document_id &&
                  ` · parent ${source.parent_document_id}`}
              </p>
              {report.latest_uploaded_version_id === source.version_id && (
                <p>
                  Latest uploaded version of this document among these two
                  sources only — not authoritative.
                </p>
              )}
            </li>
          ),
        )}
      </ol>
      {!report.findings.length && (
        <p>No comparable fields observed in these partial contexts.</p>
      )}
      {report.findings.map((finding, i) => (
        <details key={i}>
          <summary>
            {finding.group}.{finding.field} · {finding.outcome}
          </summary>
          <p className="processing-note">{finding.reason}</p>
          <div className="comparison-values">
            {([finding.left, finding.right] as LeaseResult["fields"][]).map(
              (fields, side) => (
                <section key={side}>
                  <h6>{side === 0 ? "Left" : "Right"} proposals</h6>
                  {!fields.length && (
                    <p>
                      Not observed in inspected context; not an
                      addition/deletion finding.
                    </p>
                  )}
                  {fields.map((field, index) => (
                    <div key={index}>
                      <strong>{field.value}</strong>
                      <p className="processing-note">
                        {field.interpretation} · MODEL_PROPOSED_UNVERIFIED ·
                        version {field.citation.version_number} ·{" "}
                        {field.citation.page_number_kind.toLowerCase()} page{" "}
                        {field.citation.page} · quote offsets{" "}
                        {field.quote_start}–{field.quote_end}
                      </p>
                      <blockquote>{field.evidence_text}</blockquote>
                      <p className="processing-note">
                        Literal representation:{" "}
                        {field.normalized
                          ? JSON.stringify(field.normalized)
                          : "not established"}
                      </p>
                    </div>
                  ))}
                </section>
              ),
            )}
          </div>
        </details>
      ))}
      <details>
        <summary>Input fingerprints</summary>
        <p className="checksum">Left: {report.left_input_sha256}</p>
        <p className="checksum">Right: {report.right_input_sha256}</p>
      </details>
    </section>
  );
}
export function ConflictPanel({ runId }: { runId: string }) {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const inputId = useId();
  const [other, setOther] = useState("");
  const [last, setLast] = useState<Input | null>(null);
  const [notice, setNotice] = useState("");
  const historyKey = ["conflict-comparisons", identity!.organization.id, runId];
  const config = useQuery({
    queryKey: ["conflicts-config", identity!.organization.id],
    queryFn: () =>
      api<{ enabled: boolean }>({ url: "/api/v1/conflicts/config" }),
    retry: false,
  });
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () =>
      api<Saved[]>({
        url: `/api/v1/workflow-runs/${runId}/conflict-comparisons`,
        params: { limit: 10 },
      }),
    retry: false,
  });
  const action = useMutation({
    retry: false,
    mutationFn: (body: Input) =>
      api<Saved>({
        url: "/api/v1/conflict-comparisons",
        method: "POST",
        data: body,
        timeout: 30000,
      }),
    onSuccess: (saved) =>
      setNotice(
        `Comparison recorded${saved.replayed ? " (stored snapshot replayed)" : ""}: ${saved.report.outcome}. No source values or authority were changed.`,
      ),
    onError: () =>
      setNotice(
        "Comparison failed or its commit is uncertain. Refresh history or retry the same saved pair/key; no conflict-free result is assumed.",
      ),
    onSettled: () =>
      cache.invalidateQueries({
        queryKey: ["conflict-comparisons", identity!.organization.id],
      }),
  });
  const valid =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      other.trim(),
    ) && other.trim().toLowerCase() !== runId.toLowerCase();
  function run(retry = false) {
    const body =
      retry && last
        ? last
        : {
            request_key: crypto.randomUUID(),
            left_run_id: other.trim(),
            right_run_id: runId,
          };
    setLast(body);
    setNotice("");
    action.mutate(body);
  }
  return (
    <section className="rule-panel">
      <h4>Amendment / version comparison</h4>
      <p className="processing-note">
        Compare this extraction (right) to another stored extraction (left). Use
        two different versions of the same document, or a directly linked
        amendment uploaded with its parent and AMENDMENT type. Copy the other
        run UUID from workflow history. Local checks make no model calls; source
        completeness and legal authority remain unestablished.
      </p>
      <label htmlFor={inputId}>Other extraction run UUID</label>
      <input
        id={inputId}
        value={other}
        onChange={(event) => setOther(event.target.value)}
        maxLength={36}
        placeholder="UUID from the other workflow history"
        autoComplete="off"
      />
      {config.data && !config.data.enabled && (
        <p>
          Conflict comparison execution is disabled. Saved comparisons remain
          readable.
        </p>
      )}
      <button
        className="secondary-button"
        disabled={!config.data?.enabled || !valid || action.isPending}
        onClick={() => run()}
      >
        Compare stored extractions
      </button>
      {last && (
        <button
          className="secondary-button"
          disabled={action.isPending}
          onClick={() => run(true)}
        >
          Retry same comparison key
        </button>
      )}
      <button
        className="secondary-button"
        disabled={history.isFetching}
        onClick={() => {
          void history.refetch();
          void config.refetch();
        }}
      >
        Refresh comparison history
      </button>
      {notice && <p role="status">{notice}</p>}
      {(action.isError || history.isError || config.isError) && (
        <p role="alert">
          {errorMessage(action.error ?? history.error ?? config.error)}
        </p>
      )}
      {history.isPending ? (
        <p>Loading comparison history…</p>
      ) : history.data?.length ? (
        history.data.map((row) => (
          <details key={row.id}>
            <summary>
              Saved comparison · {row.report.outcome} · {row.created_at}
            </summary>
            <ComparisonView report={row.report} />
          </details>
        ))
      ) : (
        !history.isError && (
          <p>No visible comparisons recorded for this extraction.</p>
        )
      )}
    </section>
  );
}
