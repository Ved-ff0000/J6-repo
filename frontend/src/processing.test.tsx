import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { ProcessingPanel } from "./processing";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Service unavailable",
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
const job = {
  id: "job-one",
  status: "SUCCEEDED",
  stage: "PARSED",
  progress: 100,
  attempts: 1,
  error_code: null,
  page_number_kind: "LOGICAL",
  segment_count: 1,
  chunk_count: 1,
  character_count: 24,
  parser_revision: "leaseguard-parser-v1",
};
function setup(enabled = true) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ProcessingPanel
        documentId="doc"
        versionNumber={1}
        isLatest
        archived={false}
        canWrite
        enabled={enabled}
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
it("shows no result until a real job exists and explains disabled policy", async () => {
  api.mockResolvedValue(null);
  setup(false);
  await screen.findByText(
    "No processing job has been requested for this original.",
  );
  expect(screen.getByText(/Parsing is disabled/)).toBeTruthy();
  expect(screen.queryByText("Parse this version")).toBeNull();
});
it("renders source as escaped text and labels logical rather than printed pages", async () => {
  api.mockImplementation(async ({ url }) => {
    if (url.endsWith("/job")) return job;
    if (url === "/api/v1/graph/config")
      return { enabled: false, node_kinds: [], relationships: {} };
    if (url === "/api/v1/search/config")
      return {
        enabled: false,
        embedding: "Lexical hashing",
        profile: "profile",
      };
    if (url.endsWith("/graph/snapshot") || url.endsWith("/index")) return null;
    return [
      {
        page_number: 1,
        page_number_kind: "LOGICAL",
        text: '<script>alert("untrusted")</script>',
        headings: [],
        tables: [],
      },
    ];
  });
  setup();
  await screen.findByText('<script>alert("untrusted")</script>');
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/not printed page numbers/)).toBeTruthy();
});
it("shows OCR failure without inventing source text", async () => {
  api.mockResolvedValue({
    ...job,
    status: "FAILED",
    stage: "FAILED",
    progress: 10,
    error_code: "OCR_REQUIRED",
    segment_count: null,
    chunk_count: null,
  });
  setup();
  await screen.findByRole("alert");
  expect(
    screen.getByText(/Scanned content requires an OCR provider/),
  ).toBeTruthy();
  expect(document.querySelector("pre")).toBeNull();
});
it("schedules explicit work but does not announce completion", async () => {
  const browser = userEvent.setup();
  api.mockImplementation(async (config) =>
    config.method === "POST" ? { ...job, status: "QUEUED" } : null,
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await browser.click(await screen.findByText("Parse this version"));
  await screen.findByText(/Request recorded. A separate worker/);
  expect(
    api.mock.calls.some(
      ([config]) => config.url.endsWith("/process") && config.method === "POST",
    ),
  ).toBe(true);
  expect(screen.queryByText("SUCCEEDED")).toBeNull();
});
