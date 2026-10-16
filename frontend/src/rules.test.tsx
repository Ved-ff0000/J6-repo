import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { RulePanel, RuleReportView, type RuleReport } from "./rules";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Rule service unavailable",
}));
const api = vi.fn();
const report: RuleReport = {
  outcome: "INDETERMINATE",
  input_sha256: "a".repeat(64),
  policy_sha256: "b".repeat(64),
  evaluated_at: "2026-10-16T12:00:00Z",
  legal_compliance_established: false,
  decision: "REVIEW_REQUIRED",
  policy: {
    enabled: true,
    engine_revision: "deterministic-rules-v1",
    rules: [
      {
        id: "LG-001",
        name: "Exact source",
        version: "1.0.0",
        severity: "CRITICAL",
        enabled: true,
        description: "<script>not executable</script>",
        conditions: { operation: "EXACT_SOURCE", scope: "FIELD" },
        actions: ["ANNOTATE", "REQUIRE_REVIEW"],
        created_at: "2026-10-16",
        updated_at: "2026-10-16",
      },
    ],
  },
  findings: [
    {
      rule_id: "LG-001",
      rule_version: "1.0.0",
      severity: "CRITICAL",
      outcome: "PASS",
      reason: "EXACT_SOURCE_LINK",
      field_indices: [0],
      chunk_ids: ["chunk"],
    },
  ],
  field_outcomes: [
    { index: 0, status: "PASS", passed: true, rules: ["LG-001@1.0.0"] },
  ],
};
function setup(inline = false) {
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
      <RulePanel runId="run-one" inline={inline ? report : null} />
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
it("renders pinned outcomes without legal approval and escapes descriptions", () => {
  render(<RuleReportView report={{ ...report, outcome: "PASS" }} />);
  expect(screen.getByText("Local rule outcome: PASS")).toBeTruthy();
  expect(screen.getByText(/not legal compliance/)).toBeTruthy();
  expect(screen.getByText(/Even PASS remains REVIEW_REQUIRED/)).toBeTruthy();
  expect(screen.getByText(/LG-001@1.0.0 · CRITICAL/)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/<script>not executable/)).toBeTruthy();
});
it("preserves inline history with rules disabled and never posts automatically", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("catalog") ? { policy: { enabled: false } } : [],
  );
  setup(true);
  await screen.findByText(/execution is disabled/);
  expect(
    (screen.getByText("Evaluate current rule snapshot") as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText("Local rule outcome: INDETERMINATE")).toBeTruthy();
  expect(api.mock.calls.every(([request]) => request.method !== "POST")).toBe(
    true,
  );
});
it("creates a local evaluation without consent/model fields and replays the same key", async () => {
  let saved = false;
  const posts: { request_key: string }[] = [];
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("catalog")) return { policy: { enabled: true } };
    if (request.method === "POST") {
      posts.push(request.data);
      saved = true;
      return {
        id: "evaluation",
        report,
        created_at: report.evaluated_at,
        replayed: posts.length > 1,
      };
    }
    return saved
      ? [
          {
            id: "evaluation",
            report,
            created_at: report.evaluated_at,
            replayed: false,
          },
        ]
      : [];
  });
  setup();
  await screen.findByText("No separate rule evaluations recorded.");
  await userEvent.click(screen.getByText("Evaluate current rule snapshot"));
  await screen.findByText(/no model calls or approval/);
  await screen.findByText(/Saved evaluation/);
  await userEvent.click(screen.getByText("Retry same rule key"));
  await screen.findByText(/stored snapshot replayed/);
  expect(posts).toHaveLength(2);
  expect(posts[0]).toEqual(posts[1]);
  expect(Object.keys(posts[0])).toEqual(["request_key"]);
});
it("fails visibly without inventing a rule pass and exposes a same-key retry", async () => {
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("catalog")) return { policy: { enabled: true } };
    if (request.method === "POST") throw new Error("unavailable");
    return [];
  });
  setup();
  await screen.findByText("No separate rule evaluations recorded.");
  await userEvent.click(screen.getByText("Evaluate current rule snapshot"));
  await screen.findByText(/commit is uncertain/);
  expect(screen.queryByText("Local rule outcome: PASS")).toBeNull();
  await waitFor(() =>
    expect(
      (screen.getByText("Retry same rule key") as HTMLButtonElement).disabled,
    ).toBe(false),
  );
});
