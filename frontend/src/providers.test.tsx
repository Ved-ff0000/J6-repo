import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { ProvidersPage } from "./providers";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Provider request unavailable",
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
  credential_configured: false,
  provider: "openai",
  model: "gpt-4.1-mini",
  profile: "profile",
  embedding: "Lexical hashing v1 (not neural semantic search)",
  probe_limit: 3,
  probe_window_seconds: 600,
  prompt_name: "provider_diagnostic",
  prompt_version: "1.0.0",
};
const run = {
  id: "real-run",
  request_key: "request",
  status: "SUCCEEDED",
  configured_model: "gpt-4.1-mini",
  returned_model: "gpt-4.1-mini-snapshot",
  prompt_name: "provider_diagnostic",
  prompt_version: "1.0.0",
  temperature: 0,
  input_tokens: null,
  output_tokens: null,
  total_tokens: null,
  duration_ms: 125,
  error_code: null,
  output_sha256: "hash",
  created_at: "2026-10-16T00:00:00Z",
  replayed: false,
};
function setup() {
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ProvidersPage />
      </QueryClientProvider>
    </MemoryRouter>,
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
it("shows disabled/not-verified state without making any paid diagnostic", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config") ? config : [],
  );
  setup();
  await screen.findByText("Disabled; no generation requests permitted");
  expect(
    (screen.getByText("Run new synthetic diagnostic") as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText(/No live success is assumed/)).toBeTruthy();
  expect(api.mock.calls.every(([request]) => request.method !== "POST")).toBe(
    true,
  );
});
it("does not fetch provider settings for non-administrators", async () => {
  vi.mocked(useAuth).mockReturnValue({
    identity: { ...identity, user: { ...identity.user, role: "ANALYST" } },
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
  setup();
  await screen.findByText("Administrator access required");
  expect(api).not.toHaveBeenCalled();
});
it("requires confirmation, sends a key and acknowledgement but no prompt or credential", async () => {
  api.mockImplementation(async (request) =>
    request.url.endsWith("/config")
      ? { ...config, enabled: true, credential_configured: true }
      : request.method === "POST"
        ? run
        : [],
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await userEvent.click(
    await screen.findByText("Run new synthetic diagnostic"),
  );
  await screen.findByText(/Synthetic structured-output diagnostic succeeded/);
  const call = api.mock.calls.find(
    ([request]) => request.method === "POST",
  )![0];
  expect(call.data.acknowledge_external_request).toBe(true);
  expect(call.data.request_key).toBeTruthy();
  expect(call.data.prompt).toBeUndefined();
  expect(call.data.api_key).toBeUndefined();
  expect(screen.getByText(/Enabled, not automatically verified/)).toBeTruthy();
});
it("shows unknown usage honestly and safely reuses a key after a request error", async () => {
  const keys: string[] = [];
  let failed = false;
  api.mockImplementation(async (request) => {
    if (request.url.endsWith("/config"))
      return { ...config, enabled: true, credential_configured: true };
    if (request.method === "POST") {
      keys.push(request.data.request_key);
      if (!failed) {
        failed = true;
        throw Error("unknown outcome");
      }
      return { ...run, replayed: true };
    }
    return [run];
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  setup();
  await screen.findByText(/tokens: Not recorded/);
  await userEvent.click(screen.getByText("Run new synthetic diagnostic"));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByText("Safely retry same request key"));
  await screen.findByText(/recorded result replayed; no new call/);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});
