import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { RetrievalPanel } from "./retrieval";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Retrieval service unavailable",
}));
const api = vi.fn();
const identity = {
  user: {
    id: "one",
    organization_id: "org",
    email: "user@example.com",
    first_name: "A",
    last_name: "B",
    role: "ANALYST",
    status: "ACTIVE",
  },
  organization: {
    id: "org",
    name: "Synthetic",
    slug: "synthetic",
    plan: "FREE",
    status: "ACTIVE",
  },
};
const config = {
  enabled: true,
  embedding: "Lexical hashing v1 (not neural semantic search)",
  profile: "profile",
};
const job = {
  id: "job",
  status: "READY",
  profile: "profile",
  attempts: 1,
  error_code: null,
  chunk_count: 1,
};
const source = {
  chunk_id: "chunk",
  document_id: "doc",
  document_title: "Synthetic lease",
  version_number: 1,
  page: 1,
  page_number_kind: "LOGICAL",
  section: "1. RENT",
  start_offset: 0,
  end_offset: 24,
  evidence: "<script>not instructions</script>",
  parser_revision: "parser-v1",
  original_url: "/api/v1/documents/doc/versions/1/download",
};
function setup(canWrite = true) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RetrievalPanel
        documentId="doc"
        versionNumber={1}
        canWrite={canWrite}
        isLatest
        archived={false}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue({
    identity,
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
});
afterEach(cleanup);
it("labels offline lexical embeddings honestly and does not show fake evidence", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config") ? { ...config, enabled: false } : null,
  );
  setup();
  await screen.findByText(/Retrieval disabled/);
  expect(screen.queryByText("Retrieve source evidence")).toBeNull();
  expect(document.querySelector("pre")).toBeNull();
});
it("records an index request without claiming completion", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? config
      : request.method === "POST"
        ? { ...job, status: "QUEUED" }
        : null,
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await userEvent.click(await screen.findByText("Index parsed source"));
  await screen.findByText(/Index request recorded/);
  expect(
    api.mock.calls.some(
      ([request]) =>
        request.url.endsWith("/index") && request.method === "POST",
    ),
  ).toBe(true);
  expect(screen.queryByText("Retrieve source evidence")).toBeNull();
});
it("retrieves escaped evidence with version, logical segment, offsets and a source recheck", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? config
      : request.url === "/api/v1/search"
        ? {
            embedding: config.embedding,
            profile: "profile",
            searched_versions: 1,
            hits: [{ score: 0.8, citation: source }],
          }
        : request.url.includes("/citations/")
          ? source
          : job,
  );
  setup(false);
  await userEvent.type(
    await screen.findByPlaceholderText("e.g. renewal notice"),
    "rent",
  );
  await userEvent.click(screen.getByText("Retrieve source evidence"));
  await screen.findByText(source.evidence);
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/not printed page/)).toBeTruthy();
  expect(screen.getByText(/Unicode code points/)).toBeTruthy();
  expect(screen.queryByText("Rebuild source index")).toBeNull();
  expect(
    api.mock.calls.some(
      ([request]) =>
        request.data?.document_id === "doc" &&
        request.data?.version_number === 1,
    ),
  ).toBe(true);
  await userEvent.click(screen.getByText("Recheck citation"));
  await screen.findByText(/Citation rechecked against the stored source/);
});
it("distinguishes an empty result from retrieval failure", async () => {
  let fail = false;
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("/config")) return config;
    if (request.url === "/api/v1/search") {
      if (fail) throw Error("outage");
      return {
        embedding: config.embedding,
        profile: "profile",
        searched_versions: 1,
        hits: [],
      };
    }
    return job;
  });
  setup();
  await userEvent.type(
    await screen.findByPlaceholderText("e.g. renewal notice"),
    "renewal",
  );
  await userEvent.click(screen.getByText("Retrieve source evidence"));
  await screen.findByText(/does not prove a clause is absent/);
  fail = true;
  await userEvent.click(screen.getByText("Retrieve source evidence"));
  await screen.findByRole("alert");
  expect(screen.getByText("Retrieval service unavailable")).toBeTruthy();
});
