import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth, errorMessage } from "./auth";

type Node = {
  id: string;
  kind: string;
  name: string;
  evidence_chunk_ids: string[];
};
type Edge = {
  id: string;
  kind: string;
  source: string;
  target: string;
  evidence_chunk_ids: string[];
};
type Assertions = { nodes: Node[]; edges: Edge[]; rationale: string };
type Snapshot = {
  id: string;
  revision: number;
  status: string;
  attempts: number;
  error_code: string | null;
  assertions: Assertions;
};
type Graph = {
  revision: number;
  nodes: Node[];
  edges: Edge[];
  traversal: string;
};
type Chunk = {
  id: string;
  page_number: number;
  text: string;
  section: string | null;
};
type Citation = {
  evidence: string;
  version_number: number;
  page: number;
  page_number_kind: string;
  start_offset: number;
  end_offset: number;
};
type Config = {
  enabled: boolean;
  node_kinds: string[];
  relationships: Record<string, string[]>;
};

export function GraphPanel({
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
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [offset, setOffset] = useState(0);
  const [catalog, setCatalog] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [revision, setRevision] = useState<number | null>(null);
  const [root, setRoot] = useState("");
  const [hops, setHops] = useState(1);
  const base = `/api/v1/documents/${documentId}/versions/${versionNumber}`;
  const key = ["graph", identity!.organization.id, documentId, versionNumber];
  const config = useQuery({
    queryKey: ["graph-config", identity!.organization.id],
    queryFn: () => api<Config>({ url: "/api/v1/graph/config" }),
    retry: false,
  });
  const latest = useQuery({
    queryKey: [...key, "snapshot"],
    queryFn: () => api<Snapshot | null>({ url: base + "/graph/snapshot" }),
    retry: false,
    refetchInterval: (q) =>
      q.state.data && ["QUEUED", "RUNNING"].includes(q.state.data.status)
        ? 2000
        : false,
  });
  const history = useQuery({
    queryKey: [...key, "history"],
    queryFn: () =>
      api<Snapshot[]>({
        url: base + "/graph/snapshots",
        params: { limit: 20 },
      }),
    enabled: historyOpen,
    retry: false,
  });
  const selected =
    revision === null
      ? latest.data
      : history.data?.find((s) => s.revision === revision);
  const ready =
    config.data?.enabled && selected?.status === "READY" && !archived;
  const graph = useQuery({
    queryKey: [
      ...key,
      "projection",
      selected?.id,
      selected?.status,
      revision,
      root,
      hops,
    ],
    queryFn: () =>
      api<Graph>({
        url: base + "/graph",
        params: {
          ...(revision !== null ? { revision } : {}),
          ...(root ? { root_id: root, hops } : {}),
        },
      }),
    enabled: !!ready,
    retry: false,
  });
  const chunks = useQuery({
    queryKey: [...key, "chunks", offset],
    queryFn: () =>
      api<Chunk[]>({ url: base + "/chunks", params: { limit: 5, offset } }),
    enabled: catalog,
    retry: false,
  });
  const citation = useMutation({
    mutationFn: (id: string) =>
      api<Citation>({ url: `/api/v1/search/citations/${id}` }),
  });
  const publish = useMutation({
    mutationFn: () => {
      const input: unknown = JSON.parse(draft);
      if (!input || typeof input !== "object" || Array.isArray(input))
        throw Error("Proposal must be a JSON object");
      return api<Snapshot>({
        url: base + "/graph/snapshots",
        method: "POST",
        data: { ...input, expected_revision: latest.data?.revision ?? 0 },
      });
    },
    onSuccess: async () => {
      setDraft("");
      setRevision(null);
      setNotice(
        "Proposal recorded, not verified. A separate graph worker must project it to Neo4j.",
      );
      await cache.invalidateQueries({ queryKey: key });
    },
    onError: (e) =>
      setNotice(
        e instanceof SyntaxError
          ? "Invalid JSON. No proposal was submitted."
          : errorMessage(e),
      ),
  });
  const project = useMutation({
    mutationFn: () =>
      api<Snapshot>({
        url: base + `/graph/snapshots/${latest.data!.revision}/project`,
        method: "POST",
        params: { rebuild: latest.data?.status === "READY" },
      }),
    onSuccess: async () => {
      setNotice("Projection requested; this does not verify any assertion.");
      await cache.invalidateQueries({ queryKey: key });
    },
    onError: (e) => setNotice(errorMessage(e)),
  });
  const editable =
    canWrite &&
    isLatest &&
    !archived &&
    config.data?.enabled &&
    !latest.isPending &&
    !latest.isError;
  return (
    <section className="processing-panel graph-panel">
      <h3>Knowledge graph · version {versionNumber}</h3>
      <p className="graph-warning">
        Human-asserted · UNVERIFIED. Citations establish source linkage, not
        that a party role or relationship is correct. No AI extraction, legal
        validation or approval.
      </p>
      {config.isPending ? (
        <p>Checking graph configuration…</p>
      ) : config.isError ? (
        <p role="alert">{errorMessage(config.error)}</p>
      ) : !config.data.enabled ? (
        <p className="processing-note">
          Graph projection is disabled in server configuration.
        </p>
      ) : (
        <p className="processing-note">
          Neo4j projection; separate durable worker. No cross-document entity
          merging.
        </p>
      )}
      {latest.isPending ? (
        <p>Loading graph proposal…</p>
      ) : latest.isError ? (
        <p role="alert">{errorMessage(latest.error)}</p>
      ) : latest.data ? (
        <>
          <p className="processing-note">
            Latest proposal revision {latest.data.revision} ·{" "}
            {latest.data.status} · attempt {latest.data.attempts}
            {latest.data.error_code && ` · ${latest.data.error_code}`}
          </p>
          <p className="processing-note">
            Rationale: {latest.data.assertions.rationale}
          </p>
        </>
      ) : (
        <p className="processing-note">
          No graph proposal exists. Parsed text has not been converted into
          verified entities.
        </p>
      )}
      <button
        className="secondary-button"
        onClick={() => setCatalog((v) => !v)}
      >
        {catalog ? "Hide source chunks" : "Inspect source chunk IDs"}
      </button>
      {catalog && (
        <div>
          {chunks.isPending ? (
            <p>Loading source chunks…</p>
          ) : chunks.isError ? (
            <p role="alert">{errorMessage(chunks.error)}</p>
          ) : (
            chunks.data?.map((chunk) => (
              <details className="graph-source" key={chunk.id}>
                <summary>
                  Segment / page {chunk.page_number} · {chunk.id}
                </summary>
                <pre className="parsed-text">{chunk.text}</pre>
              </details>
            ))
          )}
          <div className="pagination">
            <button
              className="secondary-button"
              disabled={offset === 0}
              onClick={() => setOffset((v) => Math.max(0, v - 5))}
            >
              Previous chunks
            </button>
            <button
              className="secondary-button"
              disabled={
                chunks.isPending ||
                chunks.isError ||
                (chunks.data?.length ?? 0) < 5
              }
              onClick={() => setOffset((v) => v + 5)}
            >
              Next chunks
            </button>
          </div>
        </div>
      )}
      {editable && (
        <form
          className="retrieval-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (
              window.confirm(
                "Record these manual, unverified assertions with source provenance? This is not approval and sends the projection to the configured Neo4j service.",
              )
            )
              publish.mutate();
          }}
        >
          <label>
            New manual graph proposal (JSON)
            <textarea
              className="graph-editor"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              required
              maxLength={200000}
              placeholder={
                '{"nodes": [], "edges": [], "rationale": "Explain the proposed source-backed relationships"}'
              }
            />
          </label>
          <p className="processing-note">
            Each node needs id, kind, exact source name and evidence_chunk_ids.
            Each edge needs id, kind, source, target and evidence_chunk_ids. Use
            actual chunk IDs above. Names must occur in cited text. Max 100
            nodes / 200 edges. No verified/confidence fields are accepted.
          </p>
          <details>
            <summary>Allowed schema and relationship directions</summary>
            <p className="processing-note">
              Kinds: {config.data?.node_kinds.join(", ")}
            </p>
            {Object.entries(config.data?.relationships ?? {}).map(
              ([kind, ends]) => (
                <p className="processing-note" key={kind}>
                  {ends[0]} → {kind} → {ends[1]}
                </p>
              ),
            )}
          </details>
          {latest.data && (
            <button
              type="button"
              className="secondary-button"
              onClick={() =>
                setDraft(JSON.stringify(latest.data!.assertions, null, 2))
              }
            >
              Copy latest proposal for a new revision
            </button>
          )}
          <button
            className="primary-button"
            disabled={publish.isPending || !draft.trim()}
          >
            {publish.isPending ? "Recording…" : "Record unverified proposal"}
          </button>
        </form>
      )}
      {editable &&
        latest.data &&
        !["QUEUED", "RUNNING"].includes(latest.data.status) && (
          <button
            className="secondary-button"
            disabled={project.isPending}
            onClick={() => {
              if (
                window.confirm(
                  "Request this graph projection again? Rebuilds temporarily hide the current generation; assertions remain unverified.",
                )
              )
                project.mutate();
            }}
          >
            {latest.data.status === "READY"
              ? "Rebuild Neo4j projection"
              : "Retry failed projection"}
          </button>
        )}
      {latest.data && (
        <>
          <button
            className="secondary-button"
            onClick={() => setHistoryOpen((v) => !v)}
          >
            {historyOpen ? "Hide graph revisions" : "Inspect graph revisions"}
          </button>
          {historyOpen &&
            (history.isPending ? (
              <p>Loading revisions…</p>
            ) : history.isError ? (
              <p role="alert">{errorMessage(history.error)}</p>
            ) : (
              <label className="processing-note">
                Graph revision (newest 20)
                <select
                  value={revision ?? "latest"}
                  onChange={(e) => {
                    setRevision(
                      e.target.value === "latest"
                        ? null
                        : Number(e.target.value),
                    );
                    setRoot("");
                    citation.reset();
                  }}
                >
                  <option value="latest">Latest proposal</option>
                  {history.data?.map((s) => (
                    <option key={s.id} value={s.revision}>
                      Revision {s.revision} · {s.status} · unverified
                    </option>
                  ))}
                </select>
              </label>
            ))}
        </>
      )}
      {ready && (
        <>
          <div className="retrieval-filters graph-controls">
            <label>
              Neighborhood root
              <select value={root} onChange={(e) => setRoot(e.target.value)}>
                <option value="">Full source graph</option>
                {selected?.assertions.nodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.id} · {n.kind}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Undirected hops
              <select
                value={hops}
                onChange={(e) => setHops(Number(e.target.value))}
              >
                <option>1</option>
                <option>2</option>
                <option>3</option>
              </select>
            </label>
          </div>
          {graph.isPending ? (
            <p>Reading Neo4j projection…</p>
          ) : graph.isError ? (
            <p role="alert">{errorMessage(graph.error)}</p>
          ) : (
            graph.data && (
              <div>
                <p className="processing-note">
                  Neo4j revision {graph.data.revision} ·{" "}
                  {graph.data.nodes.length} nodes / {graph.data.edges.length}{" "}
                  relationships · {graph.data.traversal}. Still unverified.
                </p>
                <div className="graph-nodes">
                  {graph.data.nodes.map((node) => (
                    <article className="graph-node" key={node.id}>
                      <span className="processing-note">
                        {node.kind} · {node.id}
                      </span>
                      <strong>{node.name}</strong>
                      {node.evidence_chunk_ids.map((id) => (
                        <button
                          className="secondary-button"
                          key={id}
                          disabled={citation.isPending}
                          onClick={() => citation.mutate(id)}
                        >
                          Inspect evidence · {id.slice(0, 8)}
                        </button>
                      ))}
                    </article>
                  ))}
                </div>
                <ul className="graph-edges">
                  {graph.data.edges.map((edge) => (
                    <li key={edge.id}>
                      <strong>{edge.source}</strong> → {edge.kind} →{" "}
                      <strong>{edge.target}</strong>
                      {edge.evidence_chunk_ids.map((id) => (
                        <button
                          className="secondary-button"
                          key={id}
                          disabled={citation.isPending}
                          onClick={() => citation.mutate(id)}
                        >
                          Evidence
                        </button>
                      ))}
                    </li>
                  ))}
                </ul>
              </div>
            )
          )}
        </>
      )}
      {citation.isError && <p role="alert">{errorMessage(citation.error)}</p>}
      {citation.data && !archived && (
        <div>
          <p className="processing-note">
            Cited original version {citation.data.version_number} ·{" "}
            {citation.data.page_number_kind === "PHYSICAL"
              ? "Page"
              : "Logical segment (not printed page)"}{" "}
            {citation.data.page} · offsets [{citation.data.start_offset},{" "}
            {citation.data.end_offset}) Unicode code points. Source linkage
            only, not relationship verification.
          </p>
          <pre className="parsed-text">{citation.data.evidence}</pre>
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
