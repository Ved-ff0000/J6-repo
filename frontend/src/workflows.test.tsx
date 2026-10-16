import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { WorkflowPanel } from "./workflows";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Workflow request unavailable",
}));
const api = vi.fn();
const identity = {
  user: {
    id: "one",
    organization_id: "org",
    email: "user@example.com",
    first_name: "A",
    last_name: "B",
    role: "ORG_ADMIN",
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
  enabled: false,
  revision: "evidence-agent-v1",
  timeout_seconds: 150,
};
const record = {
  id: "run",
  status: "REVIEW_REQUIRED",
  stage: "decision",
  trace: [{ name: "decision", status: "COMPLETED", timestamp: "2026-10-16" }],
  calls: [],
  result: {
    classification: { kind: "LEASE" },
    candidates: [
      {
        kind: "document_title",
        value: "<script>untrusted</script>",
        assertion: "MODEL_PROPOSED_UNVERIFIED",
        quote_start: 0,
        quote_end: 26,
        citation: {
          page: 1,
          page_number_kind: "LOGICAL",
          version_number: 1,
          start_offset: 0,
          evidence: "<script>untrusted</script>",
        },
      },
    ],
    confidence: { semantic_score: null, evidence_coverage: 1 },
    source_chunk_count: 1,
    total_chunk_count: 8,
    retrieved_chunk_count: 1,
    decision: "REVIEW_REQUIRED",
    review_queue_created: false,
  },
  error_code: null,
  created_at: "2026-10-16",
  replayed: false,
};
function setup() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkflowPanel
        documentId="doc"
        versionNumber={1}
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
it("keeps disabled workflows off without spending", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config") ? config : [],
  );
  setup();
  await screen.findByText(/Workflow generation is disabled/);
  expect(
    (screen.getByText("Run new evidence workflow") as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText(/No AI success is assumed/)).toBeTruthy();
  expect(api.mock.calls.every(([request]) => request.method !== "POST")).toBe(
    true,
  );
});
it("does not fetch workflow results for a non-admin", () => {
  vi.mocked(useAuth).mockReturnValue({
    identity: { ...identity, user: { ...identity.user, role: "ANALYST" } },
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
  setup();
  expect(api).not.toHaveBeenCalled();
  expect(screen.queryByText("Bounded evidence workflow")).toBeNull();
});
it("shows escaped unverified evidence without inventing semantic confidence or review tasks", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config") ? config : [record],
  );
  setup();
  await screen.findByText(/Sampled 1 of 8/);
  expect(screen.getByText(/Semantic confidence: not measured/)).toBeTruthy();
  expect(screen.getByText(/returned candidates only/)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/does not create a review task/)).toBeTruthy();
});
it("requires document-egress consent and safely reuses the workflow key", async () => {
  const keys: string[] = [];
  let failed = false;
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("/config")) return { ...config, enabled: true };
    if (request.method === "POST") {
      keys.push(request.data.request_key);
      expect(request.data.acknowledge_external_request).toBe(true);
      expect(request.data.source).toBeUndefined();
      if (!failed) {
        failed = true;
        throw Error("unknown");
      }
      return { ...record, replayed: true };
    }
    return [];
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await userEvent.click(await screen.findByText("Run new evidence workflow"));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByText("Safely retry workflow key"));
  await screen.findByText(/recorded result replayed/);
  expect(window.confirm).toHaveBeenCalledWith(
    expect.stringContaining("text from this document"),
  );
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
