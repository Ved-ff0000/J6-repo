import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { LeaseFields, type LeaseResult } from "./lease-extraction";
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
const lease: LeaseResult = {
  schema_version: "lease-extraction-v1",
  document_completeness: "NOT_ESTABLISHED",
  authoritative_lease_created: false,
  groups: [
    { group: "rent", proposed_count: 1, observation: "PROPOSED_VALUES" },
    {
      group: "dates",
      proposed_count: 0,
      observation: "NOT_OBSERVED_IN_CONTEXT",
    },
  ],
  fields: [
    {
      group: "rent",
      field: "base_rent",
      value: "<script>untrusted</script>",
      interpretation: "AMBIGUOUS",
      normalized: null,
      normalization_status: "REQUIRES_REVIEW",
      normalization_revision: "literal-normalizer-v1",
      status: "MODEL_PROPOSED_UNVERIFIED",
      confidence: null,
      evidence_text: "<script>untrusted</script>",
      quote_start: 0,
      quote_end: 26,
      value_start: 0,
      value_end: 26,
      citation: {
        version_number: 1,
        page: 1,
        page_number_kind: "LOGICAL",
        section: null,
      },
      explanation: "Field meaning remains unverified.",
      rule_validation: { status: "NOT_RUN", passed: null, rules: [] },
    },
  ],
};
const record = {
  id: "run",
  extraction_mode: "LEASE_FIELDS",
  status: "REVIEW_REQUIRED",
  stage: "decision",
  created_at: "2026-10-16",
  trace: [],
  calls: [],
  result: {
    classification: { kind: "LEASE" },
    candidates: [],
    confidence: { semantic_score: null, evidence_coverage: 1 },
    source_chunk_count: 1,
    total_chunk_count: 4,
    retrieved_chunk_count: 1,
    decision: "REVIEW_REQUIRED",
    review_queue_created: false,
    lease_extraction: lease,
  },
  replayed: false,
  error_code: null,
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
it("renders escaped raw evidence, ambiguity and unknown absence without fake validation", () => {
  render(<LeaseFields result={lease} />);
  expect(document.querySelector("script")).toBeNull();
  expect(
    screen.getByText(/absence from the lease is not established/),
  ).toBeTruthy();
  expect(screen.getByText(/No normalized value/)).toBeTruthy();
  expect(screen.getByText(/confidence is not measured/)).toBeTruthy();
  expect(screen.getByText(/Rule validation: NOT_RUN/)).toBeTruthy();
});
it("keeps lease mode disabled independently of title/heading mode", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? { enabled: true, lease_extraction_enabled: false, timeout_seconds: 150 }
      : [],
  );
  setup();
  await screen.findByText(
    "No workflow runs recorded. No AI success is assumed.",
  );
  expect(
    (screen.getByText("Lease field proposals (12 groups)") as HTMLOptionElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByText("Run new evidence workflow") as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  expect(api.mock.calls.every(([request]) => request.method !== "POST")).toBe(
    true,
  );
});
it("sends explicit lease mode and retains original mode with the retry key after switching", async () => {
  const posts: { request_key: string; extraction_mode: string }[] = [];
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("/config"))
      return {
        enabled: true,
        lease_extraction_enabled: true,
        timeout_seconds: 150,
      };
    if (request.method === "POST") {
      posts.push(request.data);
      return { ...record, replayed: posts.length > 1 };
    }
    return [];
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await screen.findByText(
    "No workflow runs recorded. No AI success is assumed.",
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Extraction mode"),
    "LEASE_FIELDS",
  );
  await userEvent.click(screen.getByText("Run lease-field extraction"));
  await screen.findByRole("status");
  await userEvent.selectOptions(
    screen.getByLabelText("Extraction mode"),
    "CANDIDATES",
  );
  await userEvent.click(screen.getByText("Safely retry workflow key"));
  await screen.findByText(/recorded result replayed/);
  expect(posts).toHaveLength(2);
  expect(posts[0].extraction_mode).toBe("LEASE_FIELDS");
  expect(posts[1].extraction_mode).toBe("LEASE_FIELDS");
  expect(posts[0].request_key).toBe(posts[1].request_key);
});
