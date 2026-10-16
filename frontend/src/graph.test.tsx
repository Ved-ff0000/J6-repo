import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { GraphPanel } from "./graph";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Graph service unavailable",
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
  node_kinds: ["Lease", "Tenant"],
  relationships: { HAS_LEASE: ["Tenant", "Lease"] },
};
const proposal = {
  nodes: [
    {
      id: "lease",
      kind: "Lease",
      name: "<script>Lease Alpha</script>",
      evidence_chunk_ids: ["chunk-one"],
    },
  ],
  edges: [],
  rationale: "Unverified manual relationship proposal for synthetic data.",
};
const snapshot = {
  id: "snapshot",
  revision: 1,
  status: "READY",
  attempts: 1,
  error_code: null,
  assertions: proposal,
};
function setup(canWrite = true) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <GraphPanel
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
it("does not turn parsed source into invented graph entities", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config") ? { ...config, enabled: false } : null,
  );
  setup();
  await screen.findByText(/Graph projection is disabled/);
  expect(screen.getByText(/No graph proposal exists/)).toBeTruthy();
  expect(screen.queryByText("Record unverified proposal")).toBeNull();
  expect(document.querySelector(".graph-node")).toBeNull();
});
it("queues manual assertions without announcing Neo4j completion or approval", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? config
      : request.method === "POST"
        ? { ...snapshot, status: "QUEUED" }
        : null,
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  const editor = await screen.findByLabelText(
    "New manual graph proposal (JSON)",
  );
  await userEvent.click(editor);
  await userEvent.paste(JSON.stringify(proposal));
  await userEvent.click(screen.getByText("Record unverified proposal"));
  await screen.findByText(/Proposal recorded, not verified/);
  expect(
    api.mock.calls.some(
      ([request]) =>
        request.method === "POST" && request.data.expected_revision === 0,
    ),
  ).toBe(true);
  expect(document.querySelector(".graph-node")).toBeNull();
});
it("escapes real projection labels and retrieves cited evidence without asserting truth", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? config
      : request.url.endsWith("/graph/snapshot")
        ? snapshot
        : request.url.includes("/citations/")
          ? {
              evidence: "Synthetic lease source",
              version_number: 1,
              page: 1,
              page_number_kind: "LOGICAL",
              start_offset: 0,
              end_offset: 22,
            }
          : {
              revision: 1,
              nodes: proposal.nodes,
              edges: [],
              traversal: "FULL_SCOPE",
            },
  );
  setup(false);
  await screen.findByText(proposal.nodes[0].name);
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/Human-asserted · UNVERIFIED/)).toBeTruthy();
  expect(
    screen.queryByLabelText("New manual graph proposal (JSON)"),
  ).toBeNull();
  await userEvent.click(screen.getByText("Inspect evidence · chunk-on"));
  await screen.findByText("Synthetic lease source");
  expect(screen.getByText(/Source linkage only/)).toBeTruthy();
});
it("does not display SQL assertions as a successful projection during an outage", async () => {
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("/config")) return config;
    if (request.url.endsWith("/graph/snapshot")) return snapshot;
    throw Error("Neo4j offline");
  });
  setup();
  await screen.findByRole("alert");
  expect(screen.getByText("Graph service unavailable")).toBeTruthy();
  expect(document.querySelector(".graph-node")).toBeNull();
});
