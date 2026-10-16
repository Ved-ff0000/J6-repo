import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  ConflictPanel,
  ComparisonView,
  type ComparisonReport,
} from "./conflicts";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Comparison service unavailable",
}));
const api = vi.fn();
const runId = "00000000-0000-4000-8000-000000000001";
const other = "00000000-0000-4000-8000-000000000002";
const third = "00000000-0000-4000-8000-000000000003";
const source = {
  run_id: runId,
  document_id: "doc",
  version_id: "v1",
  version_number: 1,
  checksum: "a".repeat(64),
  version_created_at: "2026-10-16T12:00:00Z",
  parent_document_id: null,
  document_type: "LEASE",
};
const field = {
  group: "renewals",
  field: "duration",
  value: "3 years",
  interpretation: "EXPLICIT",
  normalized: { kind: "duration", amount: "3", unit: "years" },
  normalization_status: "NORMALIZED",
  normalization_revision: "literal-normalizer-v1",
  status: "MODEL_PROPOSED_UNVERIFIED",
  confidence: null,
  evidence_text: "<script>Renewal duration: 3 years</script>",
  quote_start: 0,
  quote_end: 24,
  value_start: 18,
  value_end: 25,
  explanation: "Not verified",
  citation: {
    version_number: 1,
    page: 1,
    page_number_kind: "LOGICAL",
    section: null,
  },
  rule_validation: { status: "NOT_RUN" as const, passed: null, rules: [] },
};
const report: ComparisonReport = {
  engine_revision: "proposal-comparison-v1",
  relationship: "DOCUMENT_VERSIONS",
  left_source: source,
  right_source: {
    ...source,
    run_id: other,
    version_id: "v2",
    version_number: 2,
  },
  left_input_sha256: "a".repeat(64),
  right_input_sha256: "b".repeat(64),
  evaluated_at: source.version_created_at,
  outcome: "POTENTIAL_CONFLICTS_DETECTED",
  latest_uploaded_version_id: "v2",
  authority_status: "NOT_ESTABLISHED",
  decision: "REVIEW_REQUIRED",
  completeness_established: false,
  previous_values_overwritten: false,
  findings: [
    {
      group: "renewals",
      field: "duration",
      outcome: "POTENTIAL_CONFLICT",
      reason: "DIFFERENT_NORMALIZED_PROPOSALS",
      left: [field],
      right: [
        {
          ...field,
          value: "5 years",
          normalized: { kind: "duration", amount: "5", unit: "years" },
          evidence_text: "Renewal duration: 5 years",
        },
      ],
    },
  ],
};
function setup() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <ConflictPanel runId={runId} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue({
    identity: {
      user: {
        id: "user",
        organization_id: "org",
        email: "synthetic@example.com",
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
    },
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
});
afterEach(cleanup);
it("preserves both source values with evidence and no authority claim; escapes quoted text", () => {
  render(<ComparisonView report={report} />);
  expect(screen.getByText("3 years")).toBeTruthy();
  expect(screen.getByText("5 years")).toBeTruthy();
  expect(screen.getByText(/Authority: NOT_ESTABLISHED/)).toBeTruthy();
  expect(screen.getByText(/not authoritative/)).toBeTruthy();
  expect(
    screen.getByRole("list", { name: "Compared source versions" }),
  ).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  expect(
    screen.getByText("<script>Renewal duration: 3 years</script>"),
  ).toBeTruthy();
});
it("keeps saved history readable when disabled and never compares automatically", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("config")
      ? { enabled: false }
      : [{ id: "comparison", report, created_at: report.evaluated_at }],
  );
  setup();
  await screen.findByText(/execution is disabled/);
  await screen.findByText(/Saved comparison ·/);
  expect(
    (screen.getByText("Compare stored extractions") as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(api.mock.calls.every(([request]) => request.method !== "POST")).toBe(
    true,
  );
});
it("requires a different valid run UUID and pins original pair and key for retry", async () => {
  const posts: unknown[] = [];
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("config")) return { enabled: true };
    if (request.method === "POST") {
      posts.push(request.data);
      return {
        id: "comparison",
        report,
        created_at: report.evaluated_at,
        replayed: posts.length > 1,
      };
    }
    return [];
  });
  setup();
  await screen.findByText(/No visible comparisons/);
  const input = screen.getByLabelText("Other extraction run UUID");
  await userEvent.type(input, runId);
  expect(
    (screen.getByText("Compare stored extractions") as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await userEvent.clear(input);
  await userEvent.type(input, other);
  await userEvent.click(screen.getByText("Compare stored extractions"));
  await screen.findByText(/No source values or authority were changed/);
  await userEvent.clear(input);
  await userEvent.type(input, third);
  await userEvent.click(screen.getByText("Retry same comparison key"));
  await screen.findByText(/stored snapshot replayed/);
  expect(posts).toHaveLength(2);
  expect(posts[0]).toEqual(posts[1]);
  expect(posts[0]).toMatchObject({ left_run_id: other, right_run_id: runId });
  expect(Object.keys(posts[0] as object).sort()).toEqual([
    "left_run_id",
    "request_key",
    "right_run_id",
  ]);
});
it("reports uncertain failure without inventing conflict-free results", async () => {
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("config")) return { enabled: true };
    if (request.method === "POST") throw new Error("unavailable");
    return [];
  });
  setup();
  await screen.findByText(/No visible comparisons/);
  await userEvent.type(
    screen.getByLabelText("Other extraction run UUID"),
    other,
  );
  await userEvent.click(screen.getByText("Compare stored extractions"));
  await screen.findByText(/commit is uncertain/);
  expect(screen.queryByText("NO_DIFFERENCES_OBSERVED")).toBeNull();
  await waitFor(() =>
    expect(
      (screen.getByText("Retry same comparison key") as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
});
it("does not treat unobserved or empty fields as established deletions", () => {
  render(
    <ComparisonView
      report={{
        ...report,
        outcome: "INDETERMINATE",
        latest_uploaded_version_id: null,
        findings: [
          { ...report.findings[0], outcome: "NOT_OBSERVED", right: [] },
        ],
      }}
    />,
  );
  expect(screen.getByText(/not an addition\/deletion finding/)).toBeTruthy();
  expect(
    screen.queryByText(/Latest uploaded version of this document/),
  ).toBeNull();
});
