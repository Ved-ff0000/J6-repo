import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";
import { WorkflowPanel } from "./workflows";
import { GraphPanel } from "./graph";
import { RetrievalPanel } from "./retrieval";

type Job = {
  id: string;
  status: string;
  stage: string;
  progress: number;
  attempts: number;
  error_code: string | null;
  page_number_kind: string | null;
  segment_count: number | null;
  chunk_count: number | null;
  character_count: number | null;
  parser_revision: string;
};
type Page = {
  page_number: number;
  page_number_kind: string;
  text: string;
  headings: { text: string }[];
  tables: string[][][];
};
export function ProcessingPanel({
  documentId,
  versionNumber,
  isLatest,
  archived,
  canWrite,
  enabled,
}: {
  documentId: string;
  versionNumber: number;
  isLatest: boolean;
  archived: boolean;
  canWrite: boolean;
  enabled: boolean;
}) {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState("");
  const base = `/api/v1/documents/${documentId}/versions/${versionNumber}`;
  const key = [
    "processing",
    identity!.organization.id,
    documentId,
    versionNumber,
  ];
  const job = useQuery({
    queryKey: [...key, "job"],
    queryFn: () => api<Job | null>({ url: base + "/job" }),
    retry: false,
    refetchInterval: (query) =>
      query.state.data &&
      ["QUEUED", "RUNNING"].includes(query.state.data.status)
        ? 2000
        : false,
  });
  const pages = useQuery({
    queryKey: [...key, "pages", offset],
    queryFn: () =>
      api<Page[]>({ url: base + "/pages", params: { limit: 1, offset } }),
    enabled: job.data?.status === "SUCCEEDED",
    retry: false,
  });
  const status = job.data?.status;
  useEffect(() => {
    if (status === "SUCCEEDED" || status === "FAILED")
      void cache.invalidateQueries({
        queryKey: ["documents", identity!.organization.id],
      });
  }, [status, cache, identity]);
  const action = useMutation({
    mutationFn: (retry: boolean) =>
      api<Job>({ url: base + (retry ? "/retry" : "/process"), method: "POST" }),
    onSuccess: async () => {
      setNotice("Request recorded. A separate worker must run to process it.");
      await cache.invalidateQueries({ queryKey: key });
      await cache.invalidateQueries({
        queryKey: ["documents", identity!.organization.id],
      });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  return (
    <section className="processing-panel">
      <h3>Parsed source · version {versionNumber}</h3>
      <p className="processing-note">
        Deterministic parsing only. No AI extraction, automatic indexing,
        compliance decision or malware clearance.
      </p>
      {job.isPending ? (
        <p>Checking processing status…</p>
      ) : job.isError ? (
        <p role="alert">{errorMessage(job.error)}</p>
      ) : job.data ? (
        <>
          <div className="processing-state">
            <strong>{job.data.status}</strong>
            <span>
              {job.data.stage} · {job.data.progress}% · attempt{" "}
              {job.data.attempts}
            </span>
          </div>
          <progress
            aria-label="Processing progress"
            value={job.data.progress}
            max={100}
          />
          <p className="processing-note">Job {job.data.id}</p>
          {job.data.status === "QUEUED" && (
            <p className="processing-note">
              Waiting for the durable worker. This is not a completed analysis.
            </p>
          )}
          {job.data.error_code && (
            <p role="alert" className="form-error">
              {job.data.error_code}
              {job.data.error_code === "OCR_REQUIRED"
                ? " — Scanned content requires an OCR provider. None is configured."
                : " — No parsed result was published for this attempt."}
            </p>
          )}
          {job.data.status === "SUCCEEDED" && (
            <>
              <p className="processing-note">
                {job.data.segment_count}{" "}
                {job.data.page_number_kind === "PHYSICAL"
                  ? "physical pages"
                  : "logical segments (not printed page numbers)"}{" "}
                · {job.data.chunk_count} chunks · {job.data.character_count}{" "}
                characters
              </p>
              {pages.isPending ? (
                <p>Loading parsed source…</p>
              ) : pages.isError ? (
                <p role="alert">{errorMessage(pages.error)}</p>
              ) : (
                pages.data?.map((page) => (
                  <div key={page.page_number}>
                    <h3>
                      {page.page_number_kind === "PHYSICAL"
                        ? "Page"
                        : "Logical segment"}{" "}
                      {page.page_number}
                    </h3>
                    <pre className="parsed-text">
                      {page.text || "(Blank segment)"}
                    </pre>
                    {page.tables.length > 0 && (
                      <p className="processing-note">
                        {page.tables.length} DOCX table(s) retained as
                        structured rows in the API.
                      </p>
                    )}
                  </div>
                ))
              )}
              <div className="pagination">
                <button
                  className="secondary-button"
                  disabled={offset === 0}
                  onClick={() => setOffset((value) => Math.max(0, value - 1))}
                >
                  Previous
                </button>
                <button
                  className="secondary-button"
                  disabled={offset + 1 >= (job.data.segment_count ?? 0)}
                  onClick={() => setOffset((value) => value + 1)}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </>
      ) : (
        <p className="processing-note">
          No processing job has been requested for this original.
        </p>
      )}
      {canWrite &&
        isLatest &&
        !archived &&
        enabled &&
        (!job.data || job.data.status === "FAILED") && (
          <button
            className="primary-button"
            disabled={action.isPending || job.isPending || job.isError}
            onClick={() => {
              if (
                window.confirm(
                  "Process this synthetic original? It has NOT been malware scanned. Parsing runs in a limited child process, not a fully hardened sandbox.",
                )
              )
                action.mutate(job.data?.status === "FAILED");
            }}
          >
            {action.isPending
              ? "Requesting…"
              : job.data?.status === "FAILED"
                ? "Retry failed parsing"
                : "Parse this version"}
          </button>
        )}
      {!enabled && (
        <p className="processing-note">
          Parsing is disabled in server configuration. Operators must explicitly
          enable it for synthetic development data.
        </p>
      )}
      {job.data?.status === "SUCCEEDED" && (
        <RetrievalPanel
          documentId={documentId}
          versionNumber={versionNumber}
          canWrite={canWrite}
          isLatest={isLatest}
          archived={archived}
        />
      )}
      {job.data?.status === "SUCCEEDED" && (
        <GraphPanel
          documentId={documentId}
          versionNumber={versionNumber}
          canWrite={canWrite}
          isLatest={isLatest}
          archived={archived}
        />
      )}
      {job.data?.status === "SUCCEEDED" && (
        <WorkflowPanel
          documentId={documentId}
          versionNumber={versionNumber}
          isLatest={isLatest}
          archived={archived}
        />
      )}
      {notice && (
        <p role="status" className="access-notice">
          {notice}
        </p>
      )}
    </section>
  );
}
