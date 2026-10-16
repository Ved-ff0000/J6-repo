import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import axios, { type AxiosRequestConfig } from "axios";
import { z } from "zod";

export const roles = [
  "ORG_ADMIN",
  "REVIEWER",
  "ANALYST",
  "VIEWER",
  "AUDITOR",
] as const;
export type User = {
  id: string;
  organization_id: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  status: string;
};
type Organization = {
  id: string;
  name: string;
  slug: string;
  plan: string;
  status: string;
};
type Identity = { user: User; organization: Organization };
const pairSchema = z.object({
  access_token: z.string(),
  refresh_token: z.string(),
  expires_in: z.number(),
  token_type: z.literal("bearer"),
});
type Pair = z.infer<typeof pairSchema>;
type AuthContextValue = {
  identity: Identity | null;
  authenticate: (
    path: "login" | "register",
    data: Record<string, string>,
  ) => Promise<void>;
  logout: () => Promise<boolean>;
  api: <T>(config: AxiosRequestConfig) => Promise<T>;
};
const AuthContext = createContext<AuthContextValue | null>(null);
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response || error.response.status >= 500)
      return "Service unavailable. Check PostgreSQL, Redis, storage, migrations and any enabled Qdrant/Neo4j or AI provider service.";
    if (error.response.status === 401)
      return "Your credentials are invalid or your session has expired. Please sign in.";
    if (error.response.status === 409)
      return (
        error.response.data?.error?.message ??
        "The request conflicts with existing data. Refresh and try again."
      );
    return (
      error.response.data?.error?.message ??
      "The request could not be completed."
    );
  }
  return "The request could not be completed. Please try again.";
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const cache = useQueryClient();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const tokens = useRef<Pair | null>(null);
  const refreshing = useRef<Promise<Pair> | null>(null);
  const generation = useRef(0);
  const clear = useCallback(() => {
    generation.current++;
    tokens.current = null;
    refreshing.current = null;
    setIdentity(null);
    cache.clear();
  }, [cache]);
  const api = useCallback(
    async <T,>(config: AxiosRequestConfig): Promise<T> => {
      const pair = tokens.current;
      if (!pair) throw new Error("Not signed in");
      const epoch = generation.current;
      const send = (access: string) =>
        axios.request<T>({
          ...config,
          headers: { ...config.headers, Authorization: `Bearer ${access}` },
        });
      try {
        return (await send(pair.access_token)).data;
      } catch (error) {
        if (
          !axios.isAxiosError(error) ||
          error.response?.status !== 401 ||
          epoch !== generation.current
        )
          throw error;
        try {
          // Another request may already have refreshed this access token.
          if (tokens.current?.access_token === pair.access_token) {
            if (!refreshing.current)
              refreshing.current = axios
                .post("/api/v1/auth/refresh", {
                  refresh_token: pair.refresh_token,
                })
                .then((response) => pairSchema.parse(response.data));
            const next = await refreshing.current;
            if (generation.current !== epoch)
              throw new Error("Session changed");
            tokens.current = next;
            refreshing.current = null;
          }
          if (!tokens.current) throw new Error("Session ended");
          return (await send(tokens.current.access_token)).data;
        } catch (refreshError) {
          if (epoch === generation.current) clear();
          throw refreshError;
        }
      }
    },
    [clear],
  );
  async function authenticate(
    path: "login" | "register",
    data: Record<string, string>,
  ) {
    clear();
    const epoch = generation.current;
    const response = await axios.post(`/api/v1/auth/${path}`, data);
    const pair = pairSchema.parse(response.data);
    const authorization = { Authorization: `Bearer ${pair.access_token}` };
    const [user, organization] = await Promise.all([
      axios.get<User>("/api/v1/auth/me", { headers: authorization }),
      axios.get<Organization>("/api/v1/organizations/current", {
        headers: authorization,
      }),
    ]);
    if (epoch !== generation.current) return;
    tokens.current = pair;
    setIdentity({ user: user.data, organization: organization.data });
  }
  async function logout() {
    try {
      await api({ url: "/api/v1/auth/logout", method: "POST" });
      clear();
      return true;
    } catch {
      clear();
      return false;
    }
  }
  return (
    <AuthContext.Provider value={{ identity, authenticate, logout, api }}>
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthProvider is required");
  return context;
}
