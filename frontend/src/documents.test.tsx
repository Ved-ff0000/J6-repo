import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { DocumentsPage } from "./documents";
import { useAuth } from "./auth";
vi.mock("./auth", () => ({
  useAuth: vi.fn(),
  errorMessage: () => "Service unavailable",
}));
const api = vi.fn();
const identity = {
  user: {
    id: "user-one",
    organization_id: "org-one",
    email: "user@example.com",
    first_name: "Ada",
    last_name: "Admin",
    role: "ORG_ADMIN",
    status: "ACTIVE",
  },
  organization: {
    id: "org-one",
    name: "Synthetic Organization",
    slug: "synthetic",
    plan: "FREE",
    status: "ACTIVE",
  },
};
function renderPage() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <DocumentsPage />
      </MemoryRouter>
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
  api.mockImplementation(async (config) =>
    config.url.endsWith("/policy")
      ? {
          max_upload_bytes: 1024,
          allowed_extensions: [".txt"],
          can_upload: true,
          parsing_enabled: false,
          malware_scanning_enabled: false,
        }
      : { items: [], total: 0, limit: 25, offset: 0 },
  );
});
afterEach(cleanup);
it("does not fetch tenant data for a guest", () => {
  vi.mocked(useAuth).mockReturnValue({
    identity: null,
    api,
    authenticate: vi.fn(),
    logout: vi.fn(),
  });
  renderPage();
  expect(screen.getByText("Your document library is private.")).toBeTruthy();
  expect(api).not.toHaveBeenCalled();
});
it("shows a real empty library and explicit unprocessed/unscanned notice", async () => {
  renderPage();
  await screen.findByText("No documents yet");
  expect(screen.getByText(/Files have not been malware scanned/)).toBeTruthy();
  expect(screen.getByText("Upload document")).toBeTruthy();
});
it("hides upload controls for a read-only member", async () => {
  api.mockImplementation(async (config) =>
    config.url.endsWith("/policy")
      ? {
          max_upload_bytes: 1024,
          allowed_extensions: [".txt"],
          can_upload: false,
        }
      : { items: [], total: 0, limit: 25, offset: 0 },
  );
  renderPage();
  await screen.findByText("No documents yet");
  expect(screen.queryByText("Upload document")).toBeNull();
});
it("checks the advertised file limit before posting a multipart body", async () => {
  const user = userEvent.setup({ applyAccept: false });
  renderPage();
  await user.click(await screen.findByText("Upload document"));
  await user.upload(
    screen.getByLabelText("Document file"),
    new File(["x".repeat(1025)], "large.txt", { type: "text/plain" }),
  );
  // jsdom's native file required-validation does not observe userEvent's FileList.
  // Submit the form event directly; keep real File and FormData for the handler.
  fireEvent.submit(screen.getByText("Upload original").closest("form")!);
  await screen.findByRole("alert");
  expect(screen.getByText(/File exceeds/)).toBeTruthy();
  expect(api.mock.calls.every(([config]) => config.method !== "POST")).toBe(
    true,
  );
});
it("posts a real File in FormData instead of fabricating a success result", async () => {
  const user = userEvent.setup();
  api.mockImplementation(async (config) => {
    if (config.method === "POST") throw new Error("storage offline");
    return config.url.endsWith("/policy")
      ? {
          max_upload_bytes: 1024,
          allowed_extensions: [".txt"],
          can_upload: true,
        }
      : { items: [], total: 0, limit: 25, offset: 0 };
  });
  renderPage();
  await user.click(await screen.findByText("Upload document"));
  await user.upload(
    screen.getByLabelText("Document file"),
    new File(["Synthetic lease"], "lease.txt", { type: "text/plain" }),
  );
  // jsdom's native file required-validation does not observe userEvent's FileList.
  // Submit the form event directly; keep real File and FormData for the handler.
  fireEvent.submit(screen.getByText("Upload original").closest("form")!);
  await waitFor(() =>
    expect(api.mock.calls.some(([config]) => config.method === "POST")).toBe(
      true,
    ),
  );
  const sent = api.mock.calls.find(([config]) => config.method === "POST")![0];
  expect(sent.data).toBeInstanceOf(FormData);
  expect(sent.data.get("file")).toBeInstanceOf(File);
  await screen.findByText("Service unavailable");
  expect(screen.queryByText(/Original retained as version 1/)).toBeNull();
});
