import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";

type IndexJob = {
  id: string;
  status: string;
  attempts: number;
  profile: string;
  error_code: string | null;
  chunk_count: number | null;
};
type Config = { enabled: boolean; embedding: string; profile: string };
type Citation = {
  chunk_id: string;
  document_id: string;
  version_number: number;
  document_title: string;
  page: number;
  page_number_kind: string;
  section: string | null;
  start_offset: number;
  end_offset: number;
  evidence: string;
  parser_revision: string;
  original_url: string;
};
type Results = {
  embedding: string;
  profile: string;
  searched_versions: number;
  hits: { score: number; citation: Citation }[];
};

export function RetrievalPanel({
  documentId,
  versionNumber,
  canWrite,
  isLatest,
  archived,
}: {
  documentId: string;
  versionNumber: number;
  canWrite: boolean;
  isLatest: boolean;
  archived: boolean;
}) {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState("");
  const [section, setSection] = useState("");
  const [notice, setNotice] = useState("");
  const base = `/api/v1/documents/${documentId}/versions/${versionNumber}`;
  const key = [
    "retrieval",
    identity!.organization.id,
    documentId,
    versionNumber,
  ];
  const config = useQuery({
    queryKey: ["retrieval-config", identity!.organization.id],
    queryFn: () => api<Config>({ url: "/api/v1/search/config" }),
    retry: false,
  });
  const job = useQuery({
    queryKey: [...key, "index"],
    queryFn: () => api<IndexJob | null>({ url: base + "/index" }),
    retry: false,
    refetchInterval: (q) =>
      q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status)
        ? 2000
        : false,
  });
  const search = useMutation({
    mutationFn: () =>
      api<Results>({
        url: "/api/v1/search",
        method: "POST",
        data: {
          query: query.trim(),
          document_id: documentId,
          version_number: versionNumber,
          top_k: 5,
          ...(page ? { page: Number(page) } : {}),
          ...(section.trim() ? { section: section.trim() } : {}),
        },
      }),
  });
  const verify = useMutation({
    mutationFn: (chunkId: string) =>
      api<Citation>({ url: `/api/v1/search/citations/${chunkId}` }),
    onSuccess: () =>
      setNotice(
        "Citation rechecked against the stored source. This is not a legal or AI validation.",
      ),
    onError: (e) => setNotice(errorMessage(e)),
  });
  const action = useMutation({
    mutationFn: (rebuild: boolean) =>
      api<IndexJob>({
        url: base + "/index",
        method: "POST",
        params: { rebuild },
      }),
    onSuccess: async () => {
      setNotice("Index request recorded; the separate index worker must run.");
      search.reset();
      verify.reset();
      await cache.invalidateQueries({ queryKey: [...key, "index"] });
    },
    onError: (e) => setNotice(errorMessage(e)),
  });
  const ready =
    config.data?.enabled &&
    job.data?.status === "READY" &&
    job.data.profile === config.data.profile &&
    !archived;
  return (
    <section className="processing-panel retrieval-panel">
      <h3>Source retrieval · version {versionNumber}</h3>
      <p className="processing-note">
        Search returns source chunks and citations, not generated answers,
        compliance decisions or confidence scores.
      </p>
      {config.isError ? (
        <p role="alert">{errorMessage(config.error)}</p>
      ) : config.isPending ? (
        <p>Checking retrieval configuration…</p>
      ) : (
        <p className="processing-note">
          Embedding: {config.data.embedding}
          {!config.data.enabled &&
            " · Retrieval disabled in server configuration."}
        </p>
      )}
      {job.isError ? (
        <p role="alert">{errorMessage(job.error)}</p>
      ) : job.isPending ? (
        <p>Checking index…</p>
      ) : job.data ? (
        <p className="processing-note">
          Index: <strong>{job.data.status}</strong> · attempt{" "}
          {job.data.attempts}
          {job.data.chunk_count !== null && ` · ${job.data.chunk_count} chunks`}
          {job.data.error_code && ` · ${job.data.error_code}`}
          {config.data &&
            job.data.profile !== config.data.profile &&
            " · Embedding configuration changed; reindex required."}
        </p>
      ) : (
        <p className="processing-note">This version has not been indexed.</p>
      )}
      {canWrite &&
        isLatest &&
        !archived &&
        config.data?.enabled &&
        !job.isPending &&
        !job.isError &&
        !["QUEUED", "RUNNING"].includes(job.data?.status ?? "") && (
          <button
            className="secondary-button"
            disabled={action.isPending}
            onClick={() => {
              if (
                window.confirm(
                  "Index this parsed synthetic source? A configured remote embedding provider receives the chunk text. Rebuilding temporarily removes this version from retrieval until publication.",
                )
              )
                action.mutate(job.data?.status === "READY");
            }}
          >
            {action.isPending
              ? "Requesting index…"
              : job.data?.status === "READY"
                ? "Rebuild source index"
                : job.data?.status === "FAILED"
                  ? "Retry source indexing"
                  : "Index parsed source"}
          </button>
        )}
      {ready && (
        <form
          className="retrieval-form"
          onSubmit={(event) => {
            event.preventDefault();
            verify.reset();
            setNotice("");
            search.mutate();
          }}
        >
          <label>
            Search this source
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              required
              maxLength={2000}
              placeholder="e.g. renewal notice"
            />
          </label>
          <div className="retrieval-filters">
            <label>
              Page / logical segment (optional)
              <input
                type="number"
                min={1}
                value={page}
                onChange={(e) => setPage(e.target.value)}
              />
            </label>
            <label>
              Exact section heading (optional)
              <input
                maxLength={150}
                value={section}
                onChange={(e) => setSection(e.target.value)}
              />
            </label>
          </div>
          <button
            className="primary-button"
            disabled={search.isPending || !query.trim()}
          >
            {search.isPending ? "Retrieving…" : "Retrieve source evidence"}
          </button>
        </form>
      )}
      {search.isError && <p role="alert">{errorMessage(search.error)}</p>}
      {search.data && ready && (
        <div aria-live="polite">
          <p className="processing-note">
            {search.data.hits.length} source result(s). Cosine similarity is a
            ranking signal, not confidence. Overlapping chunks may repeat text.
          </p>
          {search.data.hits.length === 0 && (
            <p>
              No matching indexed evidence. This does not prove a clause is
              absent.
            </p>
          )}
          {search.data.hits.map(({ score, citation }) => (
            <article className="retrieval-hit" key={citation.chunk_id}>
              <h4>
                {citation.document_title} · version {citation.version_number}
              </h4>
              <p className="processing-note">
                {citation.page_number_kind === "PHYSICAL"
                  ? "Page"
                  : "Logical segment (not printed page)"}{" "}
                {citation.page}
                {citation.section && ` · ${citation.section}`} · similarity{" "}
                {score.toFixed(3)}
              </p>
              <pre className="parsed-text">{citation.evidence}</pre>
              <p className="processing-note">
                Source offsets [{citation.start_offset}, {citation.end_offset})
                · Unicode code points · {citation.parser_revision}
              </p>
              <p className="processing-note">Chunk {citation.chunk_id}</p>
              <button
                className="secondary-button"
                disabled={verify.isPending}
                onClick={() => verify.mutate(citation.chunk_id)}
              >
                Recheck citation
              </button>
            </article>
          ))}
        </div>
      )}
      {notice && (
        <p role="status" className="access-notice">
          {notice}
        </p>
      )}
    </section>
  );
}
