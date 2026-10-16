import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkspacePage } from "./workspace";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Workspace service unavailable",
}));
const api = vi.fn();
const identity = {
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
};
const task = {
  id: "task",
  run_id: "run",
  document_id: "doc",
  version_id: "version",
  field_index: 0,
  status: "OPEN",
  revision: 1,
  created_at: "2026-10-16",
  original: {
    group: "renewals",
    field: "duration",
    value: "3 years",
    interpretation: "EXPLICIT",
    quote_start: 0,
    quote_end: 25,
    evidence_text: "<script>Renewal: 3 years</script>",
    citation: {
      chunk_id: "chunk",
      version_number: 1,
      page_number_kind: "LOGICAL",
      page: 1,
    },
  },
};
function setup(
  kind:
    | "reviews"
    | "audit"
    | "analytics"
    | "intelligence"
    | "notifications"
    | "integrations"
    | "billing",
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkspacePage kind={kind} />
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
it("requires a meaningful reason and saves a separate human field decision", async () => {
  let saved = false;
  api.mockImplementation(async (request) => {
    if (request.method === "POST") {
      saved = true;
      return { id: "decision", action: request.data.action };
    }
    return saved ? [] : [task];
  });
  setup("reviews");
  await screen.findByText("3 years");
  const button = screen.getByText("Save field decision") as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(document.querySelector("script")).toBeNull();
  await userEvent.type(
    screen.getByLabelText("Review reason"),
    "Inspected exact original source.",
  );
  await userEvent.click(button);
  await screen.findByText(/This is not lease approval/);
  const post = api.mock.calls.find(
    ([request]) => request.method === "POST",
  )![0];
  expect(post.url).toBe("/api/v1/reviews/task/decision");
  expect(post.data.expected_revision).toBe(1);
  expect(post.data.action).toBe("ACCEPT");
  expect(post.data.request_key).toBeTruthy();
});
it("does not allow viewer review actions or requests", () => {
  vi.mocked(useAuth).mockReturnValue({
    identity: { ...identity, user: { ...identity.user, role: "VIEWER" } },
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
  setup("reviews");
  expect(screen.getByText(/does not have review access/)).toBeTruthy();
  expect(api).not.toHaveBeenCalled();
});
it("does not display an invented successful extraction when none exists", async () => {
  api.mockResolvedValue([]);
  setup("intelligence");
  await screen.findByText(/No completed latest-source lease extractions/);
  expect(screen.queryByText("3 years")).toBeNull();
});
it("renders empty actual analytics without inventing accuracy or confidence", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("deadlines")
      ? { items: [], scope: "Reviewed dates only" }
      : {
          documents: {},
          processing: {},
          workflows: {},
          reviews: {},
          decisions: {},
          comparison_records: 0,
          review_acceptance_rate: null,
          ai_accuracy: null,
          calibrated_confidence: null,
          quality_note: "Acceptance is not AI accuracy.",
        },
  );
  setup("analytics");
  await screen.findByText(/AI accuracy: not measured/);
  expect(screen.getByText(/No eligible human-reviewed dates/)).toBeTruthy();
});
it("makes simulation status explicit rather than pretending external synchronization", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("integrations")
      ? [
          {
            id: "mock",
            target: "salesforce_mock",
            status: "SIMULATED",
            result: { warning: "No real Salesforce record changed." },
          },
        ]
      : [],
  );
  setup("integrations");
  await screen.findByText("salesforce_mock · SIMULATED");
  expect(screen.getByText(/SIMULATED does not mean/)).toBeTruthy();
});
it("fails visibly when workspace infrastructure is unavailable", async () => {
  api.mockRejectedValue(new Error("unavailable"));
  setup("billing");
  await screen.findByRole("alert");
  expect(screen.queryByText("0")).toBeNull();
});
