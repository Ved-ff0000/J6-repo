export type LeaseResult = {
  schema_version: string;
  document_completeness: "NOT_ESTABLISHED";
  authoritative_lease_created: false;
  groups: { group: string; proposed_count: number; observation: string }[];
  fields: {
    group: string;
    field: string;
    value: string;
    interpretation: string;
    normalized: Record<string, string | null> | null;
    normalization_status: string;
    normalization_revision: string;
    status: string;
    confidence: null;
    evidence_text: string;
    quote_start: number;
    quote_end: number;
    value_start: number;
    value_end: number;
    explanation: string;
    citation: {
      version_number: number;
      page: number;
      page_number_kind: string;
      section: string | null;
    };
    rule_validation: {
      status: "NOT_RUN" | "PASS" | "FAIL" | "INDETERMINATE" | "DISABLED";
      passed: boolean | null;
      rules: string[];
    };
  }[];
};
export function LeaseFields({
  result,
  rulesEvaluated = false,
}: {
  result: LeaseResult;
  rulesEvaluated?: boolean;
}) {
  return (
    <section className="lease-fields">
      <h4>Lease field proposals · {result.schema_version}</h4>
      <p className="graph-warning">
        Document completeness: not established. These are model-proposed,
        unverified values from partial context, not an authoritative lease.
        Exact evidence and literal normalization do not verify field meaning.
        {rulesEvaluated
          ? "Versioned local findings appear below; a pass is not legal approval."
          : "No rule result is attached to this original extraction."}{" "}
        Semantic confidence is not measured. No approval, amendment
        reconciliation or review task is created.
      </p>
      {result.groups.map((group) => (
        <section key={group.group}>
          <h5>
            {group.group.toUpperCase()} · {group.proposed_count} proposed
          </h5>
          {group.observation === "NOT_OBSERVED_IN_CONTEXT" && (
            <p className="processing-note">
              Not observed in inspected context — absence from the lease is not
              established.
            </p>
          )}
          {result.fields
            .filter((field) => field.group === group.group)
            .map((field, index) => (
              <article
                className="retrieval-hit"
                key={`${field.field}-${index}`}
              >
                <strong>
                  {field.field}: {field.value}
                </strong>
                <p className="processing-note">
                  {field.status} · model interpretation: {field.interpretation}{" "}
                  · normalization: {field.normalization_status}
                </p>
                {field.normalized ? (
                  <details>
                    <summary>
                      Literal normalization (not legal validation)
                    </summary>
                    <pre>{JSON.stringify(field.normalized, null, 2)}</pre>
                    <p className="processing-note">
                      {field.normalization_revision}; no currency inference or
                      deadline calculation.
                    </p>
                  </details>
                ) : (
                  <p className="processing-note">
                    No normalized value. Raw source value is retained;
                    ambiguity, conditions and references are not resolved.
                  </p>
                )}
                <blockquote>{field.evidence_text}</blockquote>
                <p className="processing-note">
                  Version {field.citation.version_number} ·{" "}
                  {field.citation.page_number_kind.toLowerCase()} page{" "}
                  {field.citation.page}
                  {field.citation.section && ` · ${field.citation.section}`} ·
                  quote offsets {field.quote_start}–{field.quote_end} · value
                  offsets {field.value_start}–{field.value_end}
                </p>
                <p className="processing-note">
                  {field.explanation} Rule validation:{" "}
                  {field.rule_validation.status}.
                </p>
              </article>
            ))}
        </section>
      ))}
    </section>
  );
}
