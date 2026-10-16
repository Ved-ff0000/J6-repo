import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";
import { LeaseFields, type LeaseResult } from "./lease-extraction";
import { RuleReportView, type RuleReport } from "./rules";
type Task = {
  id: string;
  run_id: string;
  document_id: string;
  version_id: string;
  field_index: number;
  status: string;
  revision: number;
  original: LeaseResult["fields"][number] & {
    citation: LeaseResult["fields"][number]["citation"] & { chunk_id: string };
  };
  created_at: string;
};
type Decision = {
  id: string;
  task_id: string;
  action: string;
  reason: string;
  reviewer_id: string;
  original: Task["original"];
  reviewed: Task["original"] | null;
  created_at: string;
};
export function WorkspacePage({
  kind,
}: {
  kind:
    | "reviews"
    | "audit"
    | "analytics"
    | "intelligence"
    | "notifications"
    | "integrations"
    | "billing";
}) {
  const { identity } = useAuth();
  if (!identity)
    return (
      <section>
        <h1>Sign in to your workspace</h1>
        <a href="/login">Sign in</a>
      </section>
    );
  return kind === "reviews" ? (
    <Reviews />
  ) : kind === "audit" ? (
    <Audit />
  ) : kind === "analytics" ? (
    <Analytics />
  ) : kind === "intelligence" ? (
    <Intelligence />
  ) : kind === "notifications" ? (
    <Notifications />
  ) : kind === "integrations" ? (
    <Integrations />
  ) : (
    <Usage />
  );
}
function useData<T>(name: string, url: string) {
  const { identity, api } = useAuth();
  return useQuery({
    queryKey: [name, identity!.organization.id],
    queryFn: () => api<T>({ url }),
    retry: false,
    refetchInterval: 30000,
  });
}
function Failure({ error }: { error: unknown }) {
  return <p role="alert">{errorMessage(error)}</p>;
}
function Reviews() {
  const { identity } = useAuth();
  const [notice, setNotice] = useState("");
  const allowed = ["ORG_ADMIN", "SUPER_ADMIN", "REVIEWER", "AUDITOR"].includes(
    identity!.user.role,
  );
  return (
    <section>
      <h1>Human review</h1>
      <p>
        Field-level human decisions are separate from model proposals. No action
        approves a lease or silently overwrites historical extraction. Decisions
        require the latest unchanged source.
      </p>
      {notice && <p role="status">{notice}</p>}
      {allowed ? (
        <ReviewList onSaved={setNotice} />
      ) : (
        <p>Your role does not have review access.</p>
      )}
    </section>
  );
}
function ReviewList({ onSaved }: { onSaved: (message: string) => void }) {
  const query = useData<Task[]>(
    "reviews",
    "/api/v1/reviews?status=OPEN&limit=100",
  );
  return query.isPending ? (
    <p>Loading queue…</p>
  ) : query.isError ? (
    <Failure error={query.error} />
  ) : (
    <>
      <p>
        {query.data.length} open tasks shown, up to 100. Older or changed
        sources may be stale.
      </p>
      {query.data.map((task) => (
        <ReviewCard key={task.id} task={task} onSaved={onSaved} />
      ))}
    </>
  );
}
function ReviewCard({
  task,
  onSaved,
}: {
  task: Task;
  onSaved: (message: string) => void;
}) {
  const { api, identity } = useAuth();
  const cache = useQueryClient();
  const [action, setAction] = useState("ACCEPT");
  const [reason, setReason] = useState("");
  const [value, setValue] = useState(task.original.value);
  const [quote, setQuote] = useState(task.original.evidence_text);
  const [chunk, setChunk] = useState(task.original.citation.chunk_id);
  const [last, setLast] = useState<Record<string, unknown> | null>(null);
  const save = useMutation({
    retry: false,
    mutationFn: (body: Record<string, unknown>) =>
      api<Decision>({
        url: `/api/v1/reviews/${task.id}/decision`,
        method: "POST",
        data: body,
      }),
    onSuccess: (decision) => {
      onSaved(
        `Saved ${decision.action} decision ${decision.id}. This is not lease approval.`,
      );
      void cache.invalidateQueries();
    },
  });
  const canWrite = ["ORG_ADMIN", "SUPER_ADMIN", "REVIEWER"].includes(
    identity!.user.role,
  );
  function submit() {
    const body = {
      request_key: crypto.randomUUID(),
      expected_revision: task.revision,
      action,
      reason,
      ...(action === "MODIFY"
        ? { value, quote, evidence_chunk_id: chunk }
        : {}),
    };
    setLast(body);
    save.mutate(body);
  }
  return (
    <article className="retrieval-hit">
      <h3>
        {task.original.group}.{task.original.field}
      </h3>
      <strong>{task.original.value}</strong>
      <p className="processing-note">
        Model proposal · {task.original.interpretation} · semantic confidence
        not measured · version {task.original.citation.version_number},{" "}
        {task.original.citation.page_number_kind.toLowerCase()} page{" "}
        {task.original.citation.page} · quote offsets{" "}
        {task.original.quote_start}–{task.original.quote_end}
      </p>
      <blockquote>{task.original.evidence_text}</blockquote>
      <p className="processing-note">
        Task {task.id} · source run {task.run_id} · document {task.document_id}
      </p>
      {canWrite && (
        <>
          <label>
            Decision
            <select value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="ACCEPT">Accept field proposal</option>
              <option value="MODIFY">Modify with exact source evidence</option>
              <option value="REJECT">Reject proposal</option>
            </select>
          </label>
          {action === "MODIFY" && (
            <>
              <label>
                Revised literal value
                <input
                  value={value}
                  maxLength={300}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              <label>
                Exact source quotation
                <textarea
                  value={quote}
                  maxLength={500}
                  onChange={(e) => setQuote(e.target.value)}
                />
              </label>
              <label>
                Evidence chunk UUID
                <input
                  value={chunk}
                  onChange={(e) => setChunk(e.target.value)}
                  maxLength={36}
                />
              </label>
              <p>
                The modified value must occur uniquely within its exact quote in
                the same source version. Interpretive rewrites are not
                represented as literal evidence.
              </p>
            </>
          )}
          <label>
            Review reason
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              minLength={8}
              maxLength={2000}
            />
          </label>
          <button
            className="primary-button"
            disabled={reason.trim().length < 8 || save.isPending}
            onClick={submit}
          >
            Save field decision
          </button>
          {last && save.isError && (
            <button
              className="secondary-button"
              disabled={save.isPending}
              onClick={() => save.mutate(last)}
            >
              Retry same review key
            </button>
          )}
        </>
      )}
      {save.isError && <Failure error={save.error} />}
    </article>
  );
}
type Event = {
  id: string;
  sequence: number;
  event_type: string;
  actor_id: string | null;
  resource_type: string;
  resource_id: string;
  request_id: string;
  sha256: string;
  previous_sha256: string;
  payload: Record<string, unknown>;
  created_at: string;
};
function Audit() {
  const { api, identity } = useAuth();
  const [after, setAfter] = useState(0);
  const [proof, setProof] = useState<string>("");
  const query = useQuery({
    queryKey: ["audit", identity!.organization.id, after],
    queryFn: () =>
      api<Event[]>({ url: "/api/v1/audit", params: { after, limit: 50 } }),
    retry: false,
  });
  async function verify() {
    try {
      const result = await api<{
        valid: boolean;
        through_sequence: number;
        anchor_sha256: string;
        more: boolean;
        limitation: string;
      }>({ url: "/api/v1/audit/verify" });
      setProof(
        `${result.valid ? "Window verified" : "VERIFICATION FAILED"} through sequence ${result.through_sequence}; anchor ${result.anchor_sha256}. ${result.more ? "More events remain. " : ""}${result.limitation}`,
      );
    } catch (error) {
      setProof(errorMessage(error));
    }
  }
  return (
    <section>
      <h1>Audit trail</h1>
      <p>
        Tenant-scoped, hash-linked events. Database migrations forbid event
        updates/deletes. Retain external anchors and backups: a hash chain alone
        cannot prove absence of privileged reconstruction or tail truncation.
      </p>
      <button className="secondary-button" onClick={() => void verify()}>
        Verify first 500 events
      </button>
      {proof && (
        <p role="status" className="checksum">
          {proof}
        </p>
      )}
      {query.isPending ? (
        <p>Loading audit events…</p>
      ) : query.isError ? (
        <Failure error={query.error} />
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Sequence / time</th>
                  <th>Event</th>
                  <th>Resource</th>
                  <th>Record</th>
                </tr>
              </thead>
              <tbody>
                {query.data.map((row) => (
                  <tr key={row.id}>
                    <td>
                      #{row.sequence}
                      <br />
                      {row.created_at}
                    </td>
                    <td>{row.event_type}</td>
                    <td>
                      {row.resource_type} · {row.resource_id}
                    </td>
                    <td>
                      <details>
                        <summary>Actor, correlation and digest</summary>
                        <p className="processing-note">
                          Actor {row.actor_id ?? "system"} · request{" "}
                          {row.request_id}
                        </p>
                        <p className="checksum">{row.sha256}</p>
                        <pre>{JSON.stringify(row.payload, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            className="secondary-button"
            disabled={after === 0}
            onClick={() => setAfter(0)}
          >
            First page
          </button>
          <button
            className="secondary-button"
            disabled={query.data.length < 50}
            onClick={() => setAfter(query.data.at(-1)!.sequence)}
          >
            Next page
          </button>
          {!query.data.length && <p>No events recorded.</p>}
        </>
      )}
    </section>
  );
}
type Summary = {
  documents: Record<string, number>;
  processing: Record<string, number>;
  workflows: Record<string, number>;
  reviews: Record<string, number>;
  decisions: Record<string, number>;
  comparison_records: number;
  review_acceptance_rate: number | null;
  ai_accuracy: null;
  calibrated_confidence: null;
  quality_note: string;
};
function Analytics() {
  const query = useData<Summary>("analytics", "/api/v1/analytics/summary");
  const dates = useData<{
    items: {
      task_id: string;
      document_id: string;
      field: string;
      date: string;
      days_from_today: number;
      status: string;
    }[];
    scope: string;
  }>("deadlines", "/api/v1/analytics/deadlines");
  return (
    <section>
      <h1>Workspace analytics</h1>
      <p>
        Observed SQL records only. Counts include retained histories; they are
        not legal risk scores or measured AI accuracy.
      </p>
      {query.isPending ? (
        <p>Loading measured activity…</p>
      ) : query.isError ? (
        <Failure error={query.error} />
      ) : (
        <>
          <div className="stat-grid">
            {Object.entries({
              "Retained documents": Object.values(query.data.documents).reduce(
                (a, b) => a + b,
                0,
              ),
              "Open review records": query.data.reviews.OPEN ?? 0,
              "Recorded decisions": Object.values(query.data.decisions).reduce(
                (a, b) => a + b,
                0,
              ),
              "Comparison records": query.data.comparison_records,
            }).map(([label, count]) => (
              <article className="stat" key={label}>
                <div>{label}</div>
                <strong>{count}</strong>
              </article>
            ))}
          </div>
          <h3>Recorded processing and review states</h3>
          {(["processing", "workflows", "decisions"] as const).map((group) => (
            <section key={group}>
              <h4>{group}</h4>
              {Object.entries(query.data[group]).map(([state, count]) => (
                <p key={state}>
                  {state}: {count}
                </p>
              ))}
            </section>
          ))}
          <p>
            Human acceptance rate:{" "}
            {query.data.review_acceptance_rate === null
              ? "Not measured (no decisions)"
              : `${(query.data.review_acceptance_rate * 100).toFixed(1)}%`}
            . AI accuracy: not measured. Confidence: not calibrated.
          </p>
          <p className="graph-warning">{query.data.quality_note}</p>
        </>
      )}
      <h3>Human-reviewed literal dates</h3>
      {dates.isError ? (
        <Failure error={dates.error} />
      ) : (
        dates.data && (
          <>
            <p>{dates.data.scope}</p>
            {dates.data.items.map((row) => (
              <p key={row.task_id}>
                {row.field} · {row.date} · {row.days_from_today} days from today
                (UTC) · document {row.document_id} · {row.status}
              </p>
            ))}
            {!dates.data.items.length && (
              <p>
                No eligible human-reviewed dates in this sample. No deadlines
                are inferred from unreviewed proposals.
              </p>
            )}
          </>
        )
      )}
    </section>
  );
}
type Extraction = {
  id: string;
  document_id: string;
  version_id: string;
  lease_extraction: LeaseResult;
  rule_report?: RuleReport | null;
};
function Intelligence() {
  const query = useData<Extraction[]>("intelligence", "/api/v1/extractions");
  const { api, identity } = useAuth();
  const [notice, setNotice] = useState("");
  const cache = useQueryClient();
  async function enqueue(id: string) {
    try {
      const tasks = await api<Task[]>({
        url: `/api/v1/workflow-runs/${id}/reviews`,
        method: "POST",
      });
      setNotice(
        `${tasks.length} review tasks retained for this extraction; no duplicate tasks created.`,
      );
      void cache.invalidateQueries({ queryKey: ["reviews"] });
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }
  return (
    <section>
      <h1>Lease intelligence</h1>
      <p>
        Latest-source, unverified field proposals. Human decisions remain
        separate from these immutable model results. Run extraction through
        Documents.
      </p>
      {notice && <p role="status">{notice}</p>}
      {query.isPending ? (
        <p>Loading extractions…</p>
      ) : query.isError ? (
        <Failure error={query.error} />
      ) : query.data.length ? (
        query.data.map((row) => (
          <article className="retrieval-hit" key={row.id}>
            <p className="processing-note">
              Run {row.id} · document {row.document_id}
            </p>
            <LeaseFields
              result={row.lease_extraction}
              rulesEvaluated={!!row.rule_report}
            />
            {row.rule_report && <RuleReportView report={row.rule_report} />}
            {["ORG_ADMIN", "SUPER_ADMIN"].includes(identity!.user.role) && (
              <button
                className="secondary-button"
                onClick={() => void enqueue(row.id)}
              >
                Create missing review tasks
              </button>
            )}
          </article>
        ))
      ) : (
        <p>
          No completed latest-source lease extractions. No AI success is
          assumed.
        </p>
      )}
    </section>
  );
}
function Notifications() {
  const { api } = useAuth();
  const cache = useQueryClient();
  const query = useData<
    { id: string; sequence: number; event_type: string; read: boolean }[]
  >("notifications", "/api/v1/notifications");
  const preferences = useData<{
    in_app: boolean;
    email: boolean;
    webhook: boolean;
  }>("notification-preferences", "/api/v1/notifications/preferences");
  const [notice, setNotice] = useState("");
  async function change(key: "in_app" | "email" | "webhook") {
    try {
      await api({
        url: "/api/v1/notifications/preferences",
        method: "PUT",
        data: { ...preferences.data, [key]: !preferences.data?.[key] },
      });
      void cache.invalidateQueries();
      setNotice(
        "Preferences saved. External delivery is at least once; inspect delivery logs for outcome.",
      );
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }
  return (
    <section>
      <h1>Notifications</h1>
      <p>
        In-app workspace events. External email/webhook transport must be
        explicitly enabled by the deployment operator; enabling a preference
        never invents a delivery success.
      </p>
      {preferences.data &&
        (["in_app", "email", "webhook"] as const).map((key) => (
          <button
            className="secondary-button"
            key={key}
            onClick={() => void change(key)}
          >
            {key}: {preferences.data![key] ? "on" : "off"}
          </button>
        ))}
      {notice && <p role="status">{notice}</p>}
      {query.isError ? (
        <Failure error={query.error} />
      ) : (
        query.data?.map((row) => (
          <p key={row.id}>
            #{row.sequence} · {row.event_type} ·{" "}
            {row.read ? (
              "Read"
            ) : (
              <button
                className="secondary-button"
                onClick={() =>
                  void api({
                    url: `/api/v1/notifications/${row.id}/read`,
                    method: "POST",
                  })
                    .then(() =>
                      cache.invalidateQueries({ queryKey: ["notifications"] }),
                    )
                    .catch((e) => setNotice(errorMessage(e)))
                }
              >
                Mark read
              </button>
            )}
          </p>
        ))
      )}
    </section>
  );
}
function Integrations() {
  const { api } = useAuth();
  const cache = useQueryClient();
  const runs = useData<
    {
      id: string;
      target: string;
      status: string;
      result: { warning: string };
    }[]
  >("integrations", "/api/v1/integrations");
  const decisions = useData<Decision[]>(
    "review-decisions",
    "/api/v1/review-decisions",
  );
  const [decision, setDecision] = useState("");
  const [target, setTarget] = useState("salesforce_mock");
  const action = useMutation({
    mutationFn: () =>
      api({
        url: "/api/v1/integrations",
        method: "POST",
        data: {
          request_key: crypto.randomUUID(),
          decision_id: decision,
          target,
        },
      }),
    onSuccess: () => cache.invalidateQueries({ queryKey: ["integrations"] }),
    retry: false,
  });
  return (
    <section>
      <h1>Enterprise integrations</h1>
      <p className="graph-warning">
        Explicit mock adapters only. SIMULATED does not mean an ERP/CRM record
        was synchronized. Generic signed webhooks are separately operated
        through notification preferences and delivery logs.
      </p>
      <label>
        Accepted/modified human decision
        <select value={decision} onChange={(e) => setDecision(e.target.value)}>
          <option value="">Select a reviewed field</option>
          {decisions.data
            ?.filter((d) => d.action !== "REJECT")
            .map((d) => (
              <option value={d.id} key={d.id}>
                {d.original.group}.{d.original.field} · {d.action} · {d.id}
              </option>
            ))}
        </select>
      </label>
      <label>
        Simulation target
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          {["salesforce_mock", "sap_mock", "servicenow_mock"].map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </label>
      <button
        className="primary-button"
        disabled={!decision || action.isPending}
        onClick={() => action.mutate()}
      >
        Run explicit mock integration
      </button>
      {action.isError && <Failure error={action.error} />}{" "}
      {runs.isError ? (
        <Failure error={runs.error} />
      ) : (
        runs.data?.map((row) => (
          <article key={row.id}>
            <h3>
              {row.target} · {row.status}
            </h3>
            <p>{row.result.warning}</p>
          </article>
        ))
      )}
    </section>
  );
}
function Usage() {
  const query = useData<{
    plan: string;
    uploaded_versions: number;
    retained_bytes: number;
    provider_diagnostic_requests: number;
    reported_diagnostic_tokens: number | null;
    workflow_runs_in_scope: number;
    workflow_generations_in_scope: number;
    reported_workflow_tokens_in_scope: number;
    workflow_calls_without_usage: number;
    billing_note: string;
  }>("usage", "/api/v1/billing/usage");
  return (
    <section>
      <h1>Organization usage</h1>
      <p>
        Usage architecture, not payment collection or an invoice. Diagnostic and
        workflow scopes are separate; workflow totals cover the latest 1000
        runs.
      </p>
      {query.isError ? (
        <Failure error={query.error} />
      ) : (
        query.data && (
          <>
            <dl>
              {Object.entries(query.data)
                .filter(([key]) => key !== "billing_note")
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{value === null ? "Not reported" : String(value)}</dd>
                  </div>
                ))}
            </dl>
            <p className="graph-warning">{query.data.billing_note}</p>
            <p>
              Actual provider cost: not measured. No currency or charge is
              invented.
            </p>
          </>
        )
      )}
    </section>
  );
}
