import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "axios";
import { AuthProvider, useAuth } from "./auth";

vi.mock("axios");
const pair = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_in: 900,
  token_type: "bearer",
};
const user = {
  id: "one",
  organization_id: "alpha",
  email: "admin@example.com",
  first_name: "Ada",
  last_name: "Admin",
  role: "ORG_ADMIN",
  status: "ACTIVE",
};
function Probe() {
  const { identity, authenticate, api, logout } = useAuth();
  return (
    <>
      <span>{identity?.user.email ?? "Guest"}</span>
      <button
        onClick={() =>
          void authenticate("login", {
            email: "admin@example.com",
            password: "password",
          })
        }
      >
        Sign in
      </button>
      <button
        onClick={() =>
          void Promise.all([
            api({ url: "/api/v1/users" }),
            api({ url: "/api/v1/users" }),
          ]).catch(() => undefined)
        }
      >
        Load concurrently
      </button>
      <button onClick={() => void logout()}>Sign out</button>
    </>
  );
}
function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(axios.get).mockImplementation(async (url) => ({
    data:
      url === "/api/v1/auth/me"
        ? user
        : {
            id: "alpha",
            name: "Alpha",
            slug: "alpha",
            plan: "FREE",
            status: "ACTIVE",
          },
  }));
  vi.mocked(axios.post).mockResolvedValue({ data: pair });
});
afterEach(cleanup);
describe("memory-only identity", () => {
  it("loads the real identity and clears it on logout without persisting tokens", async () => {
    const browser = setup();
    expect(screen.getByText("Guest")).toBeTruthy();
    await browser.click(screen.getByText("Sign in"));
    await screen.findByText("admin@example.com");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    vi.mocked(axios.request).mockResolvedValue({ data: null });
    await browser.click(screen.getByText("Sign out"));
    await screen.findByText("Guest");
    expect(axios.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "/api/v1/auth/logout",
        headers: { Authorization: "Bearer access-one" },
      }),
    );
  });
  it("single-flights simultaneous refreshes instead of replaying a rotated token", async () => {
    const browser = setup();
    await browser.click(screen.getByText("Sign in"));
    await screen.findByText("admin@example.com");
    const unauthorized = { isAxiosError: true, response: { status: 401 } };
    vi.mocked(axios.isAxiosError).mockReturnValue(true);
    vi.mocked(axios.request).mockImplementation(async (config) => {
      if (config.headers?.Authorization === "Bearer access-one")
        throw unauthorized;
      return { data: [] };
    });
    vi.mocked(axios.post).mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return {
        data: {
          ...pair,
          access_token: "access-two",
          refresh_token: "refresh-two",
        },
      };
    });
    await browser.click(screen.getByText("Load concurrently"));
    await waitFor(() => expect(axios.request).toHaveBeenCalledTimes(4));
    expect(axios.post).toHaveBeenCalledTimes(2); // one login, one refresh
    expect(axios.post).toHaveBeenLastCalledWith("/api/v1/auth/refresh", {
      refresh_token: "refresh-one",
    });
    expect(screen.getByText("admin@example.com")).toBeTruthy();
  });
  it("clears identity when the server rejects refresh", async () => {
    const browser = setup();
    await browser.click(screen.getByText("Sign in"));
    await screen.findByText("admin@example.com");
    vi.mocked(axios.isAxiosError).mockReturnValue(true);
    vi.mocked(axios.request).mockRejectedValue({ response: { status: 401 } });
    vi.mocked(axios.post).mockRejectedValue({ response: { status: 401 } });
    await browser.click(screen.getByText("Load concurrently"));
    await screen.findByText("Guest");
  });
});
