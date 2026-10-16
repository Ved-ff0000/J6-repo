import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { errorMessage, roles, useAuth, type User } from "./auth";

type Credentials = {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  organization_name: string;
  organization_slug: string;
};
export function AuthScreen({ mode }: { mode: "login" | "register" }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
    reset,
  } = useForm<Credentials>();
  const signup = mode === "register";
  return (
    <section className="panel identity-panel">
      <div className="identity-icon">
        <ShieldCheck size={26} />
      </div>
      <div className="eyebrow">EVIDENCE-BACKED. HUMAN-CONTROLLED.</div>
      <h1>{signup ? "Create your workspace" : "Welcome back"}</h1>
      <p>
        {signup
          ? "Register a new organization and its first administrator."
          : "Sign in to your organization's workspace."}
      </p>
      <form
        onSubmit={handleSubmit(async (values) => {
          try {
            const data = signup
              ? values
              : { email: values.email, password: values.password };
            await auth.authenticate(mode, data);
            reset();
            navigate("/settings");
          } catch (error) {
            setError("root", { message: errorMessage(error) });
          }
        })}
      >
        {signup && (
          <>
            <div className="form-pair">
              <label>
                First name
                <input
                  autoComplete="given-name"
                  {...register("first_name", {
                    required: true,
                    maxLength: 100,
                  })}
                />
              </label>
              <label>
                Last name
                <input
                  autoComplete="family-name"
                  {...register("last_name", { required: true, maxLength: 100 })}
                />
              </label>
            </div>
            <label>
              Organization name
              <input
                {...register("organization_name", {
                  required: true,
                  maxLength: 200,
                })}
              />
            </label>
            <label>
              Workspace slug
              <input
                placeholder="acme-properties"
                pattern="[a-z0-9]+(-[a-z0-9]+)*"
                minLength={3}
                maxLength={80}
                {...register("organization_slug", { required: true })}
              />
            </label>
          </>
        )}
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            maxLength={254}
            {...register("email", { required: true })}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            minLength={signup ? 12 : 1}
            maxLength={128}
            {...register("password", { required: true })}
          />
        </label>
        {signup && (
          <small>
            Use 12–128 characters. No platform role can be selected at
            registration.
          </small>
        )}
        {Object.keys(errors).some((key) => key !== "root") && (
          <div role="alert" className="form-error">
            Complete all required fields with valid values.
          </div>
        )}
        {errors.root && (
          <div role="alert" className="form-error">
            {errors.root.message}
          </div>
        )}
        <button className="primary-button" disabled={isSubmitting}>
          {isSubmitting
            ? "Connecting…"
            : signup
              ? "Create organization"
              : "Sign in"}
        </button>
      </form>
      <p>
        {signup ? "Already have a workspace?" : "New organization?"}{" "}
        <Link to={signup ? "/login" : "/register"}>
          {signup ? "Sign in" : "Create a workspace"}
        </Link>
      </p>
      <div className="honesty-note">
        Sessions stay in memory. Reloading the page requires signing in again.
      </div>
    </section>
  );
}

type MemberValues = {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  role: string;
};
export function IdentitySettings() {
  const { identity, api, logout } = useAuth();
  const cache = useQueryClient();
  const [notice, setNotice] = useState("");
  const [offset, setOffset] = useState(0);
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting, errors },
  } = useForm<MemberValues>({ defaultValues: { role: "VIEWER" } });
  const canWrite =
    identity && ["ORG_ADMIN", "SUPER_ADMIN"].includes(identity.user.role);
  const canRead = canWrite || identity?.user.role === "AUDITOR";
  const members = useQuery({
    queryKey: ["members", identity?.organization.id, offset],
    queryFn: () =>
      api<User[]>({ url: "/api/v1/users", params: { limit: 50, offset } }),
    enabled: !!canRead,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, string> }) =>
      api({ url: `/api/v1/users/${id}`, method: "PATCH", data }),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["members"] });
      setNotice("Member access updated. Existing sessions were revoked.");
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  if (!identity)
    return (
      <section className="panel empty">
        <ShieldCheck size={36} />
        <h2>Your workspace starts with a secure identity.</h2>
        <p>
          Sign in or create an organization to manage your membership.
          <br />
          PostgreSQL and Redis must be available.
        </p>
        <Link to="/login">Sign in →</Link>
        <span> · </span>
        <Link to="/register">Create organization →</Link>
        {notice && <p role="status">{notice}</p>}
      </section>
    );
  return (
    <>
      <div className="eyebrow">ORGANIZATION & ACCESS</div>
      <h1>Workspace settings</h1>
      <section className="panel account-panel">
        <div>
          <h2>{identity.organization.name}</h2>
          <p>
            {identity.organization.slug} · {identity.organization.plan} ·{" "}
            {identity.organization.status}
          </p>
          <p>
            Signed in as {identity.user.email} ·{" "}
            <strong>{identity.user.role}</strong>
          </p>
        </div>
        <button
          className="secondary-button"
          onClick={async () => {
            const revoked = await logout();
            setNotice(
              revoked
                ? "Signed out. Session revoked."
                : "Signed out locally. Server revocation could not be confirmed; the session may remain valid until expiry.",
            );
          }}
        >
          Sign out
        </button>
      </section>
      {notice && (
        <p role="status" className="access-notice">
          {notice}
        </p>
      )}
      {canRead && (
        <section className="panel member-panel">
          <h2>Organization members</h2>
          <p>All results are scoped by the backend to your organization.</p>
          {members.isPending ? (
            <p>Loading members…</p>
          ) : members.isError ? (
            <p role="alert">{errorMessage(members.error)}</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Role</th>
                    <th>Status</th>
                    {canWrite && <th>Access</th>}
                  </tr>
                </thead>
                <tbody>
                  {members.data?.map((member) => (
                    <tr key={member.id}>
                      <td>
                        {member.first_name} {member.last_name}
                        <small>{member.email}</small>
                      </td>
                      <td>
                        {canWrite &&
                        member.id !== identity.user.id &&
                        member.role !== "SUPER_ADMIN" ? (
                          <select
                            aria-label={`Role for ${member.email}`}
                            value={member.role}
                            disabled={mutation.isPending}
                            onChange={(event) =>
                              mutation.mutate({
                                id: member.id,
                                data: { role: event.target.value },
                              })
                            }
                          >
                            {roles.map((role) => (
                              <option key={role}>{role}</option>
                            ))}
                          </select>
                        ) : (
                          member.role
                        )}
                      </td>
                      <td>{member.status}</td>
                      {canWrite && (
                        <td>
                          {member.id !== identity.user.id &&
                          member.role !== "SUPER_ADMIN" ? (
                            <button
                              className="secondary-button"
                              disabled={mutation.isPending}
                              onClick={() =>
                                mutation.mutate({
                                  id: member.id,
                                  data: {
                                    status:
                                      member.status === "ACTIVE"
                                        ? "DISABLED"
                                        : "ACTIVE",
                                  },
                                })
                              }
                            >
                              {member.status === "ACTIVE"
                                ? "Disable"
                                : "Enable"}
                            </button>
                          ) : (
                            "Current account"
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pagination">
            <button
              className="secondary-button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              Previous
            </button>
            <span>Page {Math.floor(offset / 50) + 1}</span>
            <button
              className="secondary-button"
              disabled={!members.data || members.data.length < 50}
              onClick={() => setOffset(offset + 50)}
            >
              Next
            </button>
          </div>
        </section>
      )}
      {canWrite && (
        <section className="panel identity-panel member-create">
          <h2>Add a member</h2>
          <p>
            Create an account within this organization. Email invitations are
            not implemented yet; share credentials only through an approved
            secure channel.
          </p>
          <form
            onSubmit={handleSubmit(async (values) => {
              try {
                await api({
                  url: "/api/v1/users",
                  method: "POST",
                  data: values,
                });
                reset({
                  role: "VIEWER",
                  email: "",
                  password: "",
                  first_name: "",
                  last_name: "",
                });
                void cache.invalidateQueries({ queryKey: ["members"] });
                setNotice("Member account created.");
              } catch (error) {
                setNotice(errorMessage(error));
              }
            })}
          >
            <div className="form-pair">
              <label>
                First name
                <input
                  maxLength={100}
                  {...register("first_name", { required: true })}
                />
              </label>
              <label>
                Last name
                <input
                  maxLength={100}
                  {...register("last_name", { required: true })}
                />
              </label>
            </div>
            <label>
              Email
              <input
                type="email"
                maxLength={254}
                {...register("email", { required: true })}
              />
            </label>
            <label>
              Initial password
              <input
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                {...register("password", { required: true })}
              />
            </label>
            <label>
              Role
              <select {...register("role")}>
                {roles.map((role) => (
                  <option key={role}>{role}</option>
                ))}
              </select>
            </label>
            {Object.keys(errors).length > 0 && (
              <div role="alert" className="form-error">
                Complete all required member fields.
              </div>
            )}
            <button className="primary-button" disabled={isSubmitting}>
              {isSubmitting ? "Creating…" : "Add member"}
            </button>
          </form>
        </section>
      )}
      {!canRead && (
        <section className="panel">
          <h2>Personal membership</h2>
          <p>
            Your role does not grant member-directory or administration access.
          </p>
        </section>
      )}
    </>
  );
}
