import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileUp,
  Download,
  Files,
  Archive,
  RotateCcw,
  RefreshCw,
} from "lucide-react";
import { format } from "date-fns";
import { errorMessage, useAuth } from "./auth";
import { ProcessingPanel } from "./processing";

export type Version = {
  id: string;
  version_number: number;
  filename: string;
  mime_type: string;
  checksum: string;
  file_size: number;
  uploaded_by: string;
  page_count: number | null;
  scan_status: string;
  created_at: string;
};
export type DocumentRecord = {
  id: string;
  title: string;
  current_version: number;
  status: string;
  classification: string;
  parent_document_id: string | null;
  created_at: string;
  updated_at: string;
  metadata: {
    document_type: string;
    property_name: string | null;
    external_reference: string | null;
    notes: string | null;
  };
  latest_version: Version;
};
type Page = {
  items: DocumentRecord[];
  total: number;
  limit: number;
  offset: number;
};
type Policy = {
  max_upload_bytes: number;
  allowed_extensions: string[];
  can_upload: boolean;
  parsing_enabled: boolean;
  malware_scanning_enabled: boolean;
};
const types = [
  "UNSPECIFIED",
  "LEASE",
  "AMENDMENT",
  "RENEWAL",
  "TERMINATION",
  "EXHIBIT",
  "ADDENDUM",
  "RENT_SCHEDULE",
  "OTHER",
];
const date = (value: string) => format(new Date(value), "dd MMM yyyy, HH:mm");
const size = (bytes: number) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export function DocumentsPage() {
  const { identity } = useAuth();
  if (!identity)
    return (
      <section className="panel empty">
        <Files size={36} />
        <h2>Your document library is private.</h2>
        <p>
          Sign in to upload and manage documents within your organization.
          <br />
          Only synthetic documents should be used during development.
        </p>
        <Link to="/login">Sign in →</Link>
      </section>
    );
  return (
    <DocumentWorkspace
      key={`${identity.organization.id}-${identity.user.id}`}
    />
  );
}
function DocumentWorkspace() {
  const { identity, api } = useAuth();
  const cache = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [notice, setNotice] = useState("");
  const prefix = ["documents", identity!.organization.id];
  const policy = useQuery({
    queryKey: [...prefix, "policy"],
    queryFn: () => api<Policy>({ url: "/api/v1/documents/policy" }),
    retry: false,
  });
  const documents = useQuery({
    queryKey: [...prefix, "list", offset, search, status],
    queryFn: () =>
      api<Page>({
        url: "/api/v1/documents",
        params: {
          limit: 25,
          offset,
          q: search || undefined,
          status: status || undefined,
        },
      }),
    retry: false,
  });
  async function refresh() {
    await cache.invalidateQueries({ queryKey: prefix });
  }
  return (
    <>
      <div className="eyebrow">PRIVATE ORIGINALS · EVIDENCE STARTS HERE</div>
      <div className="heading">
        <div>
          <h1>Document library</h1>
          <p>
            {identity!.organization.name} · Original files and immutable version
            history
          </p>
        </div>
        {policy.data?.can_upload && (
          <button
            className="primary-button"
            onClick={() => setShowUpload((value) => !value)}
          >
            <FileUp size={15} />{" "}
            {showUpload ? "Close upload" : "Upload document"}
          </button>
        )}
      </div>
      <div className="document-warning">
        <strong>Originals, not AI decisions.</strong> Parsing and AI extraction
        are not enabled. Files have not been malware scanned. Only upload
        synthetic documents in this development build.
      </div>
      {policy.isError && (
        <p role="alert" className="form-error">
          {errorMessage(policy.error)}
        </p>
      )}
      {notice && (
        <p role="status" className="access-notice">
          {notice}
        </p>
      )}
      {showUpload && policy.data?.can_upload && (
        <UploadForm
          policy={policy.data}
          documents={documents.data?.items ?? []}
          onDone={async (document) => {
            setNotice(
              "Original retained as version 1. Parsing can be requested separately; no AI extraction was run.",
            );
            setSelected(document.id);
            setShowUpload(false);
            await refresh();
          }}
        />
      )}
      <div className="document-layout">
        <section className="panel document-list">
          <div className="document-toolbar">
            <input
              aria-label="Search document titles"
              placeholder="Search document titles…"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label="Filter by document status"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              <option>UPLOADED</option>
              <option>PROCESSING</option>
              <option>PARSING</option>
              <option>PARSED</option>
              <option>FAILED</option>
              <option>ARCHIVED</option>
            </select>
            <button
              aria-label="Refresh documents"
              className="secondary-button"
              onClick={() => void refresh()}
            >
              <RefreshCw size={14} />
            </button>
          </div>
          {documents.isPending ? (
            <p>Loading documents…</p>
          ) : documents.isError ? (
            <p role="alert" className="form-error">
              {errorMessage(documents.error)}
            </p>
          ) : documents.data.items.length === 0 ? (
            <div className="document-empty">
              <Files size={32} />
              <h2>
                {search || status
                  ? "No matching documents"
                  : "No documents yet"}
              </h2>
              <p>
                {search || status
                  ? "Change the search or status filter."
                  : "Upload a synthetic PDF, DOCX or UTF-8 text file to begin."}
              </p>
            </div>
          ) : (
            <div className="document-rows">
              {documents.data.items.map((document) => (
                <button
                  key={document.id}
                  className={`document-row ${selected === document.id ? "selected" : ""}`}
                  onClick={() => setSelected(document.id)}
                >
                  <Files size={20} />
                  <div>
                    <strong>{document.title}</strong>
                    <span>
                      {document.latest_version.filename} ·{" "}
                      {size(document.latest_version.file_size)} · v
                      {document.current_version}
                    </span>
                    <small>{date(document.created_at)}</small>
                  </div>
                  <span
                    className={`document-status ${document.status === "ARCHIVED" ? "archived" : ""}`}
                  >
                    {document.status}
                  </span>
                </button>
              ))}
            </div>
          )}
          <div className="pagination">
            <span>{documents.data?.total ?? 0} documents</span>
            <button
              className="secondary-button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >
              Previous
            </button>
            <button
              className="secondary-button"
              disabled={!documents.data || offset + 25 >= documents.data.total}
              onClick={() => setOffset(offset + 25)}
            >
              Next
            </button>
          </div>
        </section>
        {selected ? (
          <DocumentDetail
            key={selected}
            id={selected}
            policy={policy.data}
            refresh={refresh}
          />
        ) : (
          <section className="panel document-empty">
            <Files size={32} />
            <h2>A clear record of every original.</h2>
            <p>
              Select a document to view its metadata, status and version
              history. Extraction evidence will arrive in later phases.
            </p>
          </section>
        )}
      </div>
    </>
  );
}
function UploadForm({
  policy,
  documents,
  onDone,
}: {
  policy: Policy;
  documents: DocumentRecord[];
  onDone: (record: DocumentRecord) => Promise<void>;
}) {
  const { api } = useAuth();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = (form.elements.namedItem("file") as HTMLInputElement)
      .files?.[0];
    if (!(file instanceof File) || !file.size) {
      setError("Choose a non-empty document.");
      return;
    }
    data.set("file", file);
    if (file.size > policy.max_upload_bytes) {
      setError(
        `File exceeds the ${size(policy.max_upload_bytes)} upload limit.`,
      );
      return;
    }
    for (const [key, value] of [...data.entries()])
      if (typeof value === "string" && !value.trim()) data.delete(key);
    setPending(true);
    try {
      const document = await api<DocumentRecord>({
        url: "/api/v1/documents",
        method: "POST",
        data,
      });
      form.reset();
      await onDone(document);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="panel document-upload">
      <h2>Retain a new original</h2>
      <p>
        PDF, DOCX, TXT · Maximum {size(policy.max_upload_bytes)} · New uploads
        are not analyzed or malware cleared.
      </p>
      <form onSubmit={(event) => void submit(event)}>
        <label className="file-picker">
          Document file
          <input
            name="file"
            aria-label="Document file"
            type="file"
            required
            accept={policy.allowed_extensions.join(",")}
          />
        </label>
        <div className="form-pair">
          <label>
            Title (optional)
            <input name="title" maxLength={255} />
          </label>
          <label>
            User-supplied type
            <select name="document_type">
              {types.map((type) => (
                <option key={type}>{type}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-pair">
          <label>
            Property label
            <input name="property_name" maxLength={200} />
          </label>
          <label>
            External reference
            <input name="external_reference" maxLength={120} />
          </label>
        </div>
        <label>
          Related parent document (optional)
          <select name="parent_document_id">
            <option value="">No parent</option>
            {documents.map((document) => (
              <option key={document.id} value={document.id}>
                {document.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Notes
          <textarea name="notes" maxLength={2000} rows={2} />
        </label>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button className="primary-button" disabled={pending}>
          {pending ? "Uploading…" : "Upload original"}
        </button>
      </form>
    </section>
  );
}
function DocumentDetail({
  id,
  policy,
  refresh,
}: {
  id: string;
  policy: Policy | undefined;
  refresh: () => Promise<void>;
}) {
  const { identity, api } = useAuth();
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [inspectedVersion, setInspectedVersion] = useState<number | null>(null);
  const queryKey = ["documents", identity!.organization.id];
  const detail = useQuery({
    queryKey: [...queryKey, "detail", id],
    queryFn: () => api<DocumentRecord>({ url: `/api/v1/documents/${id}` }),
    retry: false,
  });
  const history = useQuery({
    queryKey: [...queryKey, "versions", id, offset],
    queryFn: () =>
      api<Version[]>({
        url: `/api/v1/documents/${id}/versions`,
        params: { limit: 25, offset },
      }),
    retry: false,
  });
  const change = useMutation({
    mutationFn: ({
      path,
      data,
      method,
    }: {
      path: string;
      data?: unknown;
      method?: string;
    }) =>
      api<DocumentRecord>({
        url: `/api/v1/documents/${id}${path}`,
        method: method ?? "POST",
        data,
      }),
    onSuccess: async () => {
      await refresh();
      setNotice(
        "Document record updated. Original version bytes are unchanged.",
      );
    },
    onError: async (error) => {
      setNotice(errorMessage(error));
      await refresh();
    },
  });
  if (detail.isPending)
    return (
      <section className="panel">
        <p>Loading document…</p>
      </section>
    );
  if (detail.isError)
    return (
      <section className="panel">
        <p role="alert">{errorMessage(detail.error)}</p>
      </section>
    );
  const document = detail.data;
  async function download(version: Version) {
    if (
      !window.confirm(
        "This original has NOT been malware scanned. Download only if you trust its source. Continue?",
      )
    )
      return;
    setDownloading(true);
    try {
      const blob = await api<Blob>({
        url: `/api/v1/documents/${id}/versions/${version.version_number}/download`,
        responseType: "blob",
      });
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = version.filename;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setDownloading(false);
    }
  }
  async function uploadVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = (form.elements.namedItem("file") as HTMLInputElement)
      .files?.[0];
    if (!(file instanceof File) || !file.size) {
      setNotice("Choose a non-empty file.");
      return;
    }
    data.set("file", file);
    if (policy && file.size > policy.max_upload_bytes) {
      setNotice("File exceeds the configured upload limit.");
      return;
    }
    data.set("expected_version", String(document.current_version));
    try {
      await change.mutateAsync({ path: "/versions", data });
      form.reset();
    } catch {
      /* mutation reports the error; do not fake success */
    }
  }
  return (
    <section className="panel document-detail">
      <div className="panel-title">
        <h2>{document.title}</h2>
        <span className="tiny-pill">VERSION {document.current_version}</span>
      </div>
      <p className="panel-description">
        {document.status} · {document.classification}
      </p>
      <dl>
        <dt>Document ID</dt>
        <dd>{document.id}</dd>
        <dt>Latest original</dt>
        <dd>
          {document.latest_version.filename} ·{" "}
          {size(document.latest_version.file_size)}
        </dd>
        <dt>Uploaded</dt>
        <dd>{date(document.created_at)}</dd>
        <dt>Classification / scan</dt>
        <dd>Not classified · {document.latest_version.scan_status}</dd>
        <dt>SHA-256 checksum</dt>
        <dd className="checksum">{document.latest_version.checksum}</dd>
        {document.parent_document_id && (
          <>
            <dt>Parent document</dt>
            <dd>{document.parent_document_id}</dd>
          </>
        )}
      </dl>
      {notice && (
        <p role="status" className="access-notice">
          {notice}
        </p>
      )}
      <ProcessingPanel
        key={inspectedVersion ?? document.current_version}
        documentId={id}
        versionNumber={inspectedVersion ?? document.current_version}
        isLatest={
          (inspectedVersion ?? document.current_version) ===
          document.current_version
        }
        archived={document.status === "ARCHIVED"}
        canWrite={policy?.can_upload ?? false}
        enabled={policy?.parsing_enabled ?? false}
      />
      <h3>User-supplied metadata</h3>
      {policy?.can_upload &&
      ["UPLOADED", "PARSED", "FAILED"].includes(document.status) ? (
        <form
          key={document.updated_at}
          className="metadata-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = Object.fromEntries(new FormData(event.currentTarget));
            void change.mutate({ path: "", method: "PATCH", data });
          }}
        >
          <label>
            Title
            <input
              name="title"
              required
              maxLength={255}
              defaultValue={document.title}
            />
          </label>
          <label>
            Type
            <select
              name="document_type"
              defaultValue={document.metadata.document_type}
            >
              {types.map((type) => (
                <option key={type}>{type}</option>
              ))}
            </select>
          </label>
          <label>
            Property label
            <input
              name="property_name"
              maxLength={200}
              defaultValue={document.metadata.property_name ?? ""}
            />
          </label>
          <label>
            External reference
            <input
              name="external_reference"
              maxLength={120}
              defaultValue={document.metadata.external_reference ?? ""}
            />
          </label>
          <label>
            Notes
            <textarea
              name="notes"
              maxLength={2000}
              defaultValue={document.metadata.notes ?? ""}
            />
          </label>
          <button className="secondary-button" disabled={change.isPending}>
            Save metadata
          </button>
        </form>
      ) : (
        <dl>
          <dt>Type</dt>
          <dd>{document.metadata.document_type}</dd>
          <dt>Property</dt>
          <dd>{document.metadata.property_name || "Not supplied"}</dd>
          <dt>Reference</dt>
          <dd>{document.metadata.external_reference || "Not supplied"}</dd>
          <dt>Notes</dt>
          <dd>{document.metadata.notes || "Not supplied"}</dd>
        </dl>
      )}
      <div className="version-section">
        <h3>Original version history</h3>
        {history.isError ? (
          <p role="alert">{errorMessage(history.error)}</p>
        ) : history.isPending ? (
          <p>Loading versions…</p>
        ) : (
          history.data.map((version) => (
            <div className="version-row" key={version.id}>
              <div>
                <strong>
                  v{version.version_number} · {version.filename}
                </strong>
                <span>
                  {date(version.created_at)} · {size(version.file_size)}
                </span>
              </div>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setInspectedVersion(version.version_number)}
              >
                Inspect text
              </button>
              <button
                aria-label={`Download version ${version.version_number}`}
                disabled={downloading}
                className="secondary-button"
                onClick={() => void download(version)}
              >
                <Download size={14} />
              </button>
            </div>
          ))
        )}
        <div className="pagination">
          <button
            className="secondary-button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 25))}
          >
            Previous
          </button>
          <button
            className="secondary-button"
            disabled={!history.data || history.data.length < 25}
            onClick={() => setOffset(offset + 25)}
          >
            Next
          </button>
        </div>
      </div>
      {policy?.can_upload && (
        <div className="document-actions">
          {["UPLOADED", "PARSED", "FAILED"].includes(document.status) && (
            <form onSubmit={(event) => void uploadVersion(event)}>
              <label>
                Upload a new version
                <input
                  name="file"
                  type="file"
                  required
                  accept={policy.allowed_extensions.join(",")}
                />
              </label>
              <p>
                Requires current version {document.current_version}; earlier
                originals remain unchanged.
              </p>
              <button className="primary-button" disabled={change.isPending}>
                Add version
              </button>
            </form>
          )}
          <button
            className="secondary-button"
            disabled={change.isPending}
            onClick={() => {
              if (
                window.confirm(
                  document.status === "ARCHIVED"
                    ? "Restore this document with its retained processing status?"
                    : "Archive this document? All originals will be retained.",
                )
              )
                change.mutate({
                  path:
                    document.status === "ARCHIVED" ? "/restore" : "/archive",
                });
            }}
          >
            {document.status === "ARCHIVED" ? (
              <RotateCcw size={14} />
            ) : (
              <Archive size={14} />
            )}{" "}
            {document.status === "ARCHIVED"
              ? "Restore document"
              : "Archive document"}
          </button>
        </div>
      )}
    </section>
  );
}
