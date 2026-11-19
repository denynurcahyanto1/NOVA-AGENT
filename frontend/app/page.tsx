"use client";

import { useCallback, useEffect, useState } from "react";
import { z } from "zod";

import DashboardPlaceholder from "@/components/dashboard-placeholder";
import AgentDashboard from "@/components/agent-dashboard";
import { useSupabaseAuth } from "@/components/auth-provider";
import { organizationSchema, type Organization } from "@/lib/types";

const organizationListSchema = z.array(organizationSchema);

function AuthForm() {
  const { configured, signIn, signUp } = useSupabaseAuth();
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setBusy(true);

    const result =
      mode === "sign-in"
        ? await signIn(email.trim(), password)
        : await signUp(email.trim(), password);

    if (result.error) {
      setMessage(result.error);
    } else if (mode === "sign-up" && result.requiresEmailConfirmation) {
      setMessage("Akun dibuat. Cek email untuk mengonfirmasi akun sebelum masuk.");
    }

    setBusy(false);
  }

  if (!configured) {
    return (
      <section
        aria-labelledby="auth-config-title"
        className="mx-auto w-full max-w-lg rounded-xl border border-amber-300 bg-amber-50 p-6"
      >
        <h1 id="auth-config-title" className="text-xl font-semibold text-amber-950">
          Supabase belum dikonfigurasi
        </h1>
        <p className="mt-2 text-sm text-amber-900">
          Isi NEXT_PUBLIC_SUPABASE_URL dan NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY di frontend/.env.local.
        </p>
      </section>
    );
  }

  const isSignIn = mode === "sign-in";

  return (
    <section
      aria-labelledby="auth-title"
      className="mx-auto w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8"
    >
      <h1 id="auth-title" className="text-2xl font-semibold tracking-tight text-slate-950">
        {isSignIn ? "Masuk ke NOVA AGENT" : "Buat akun NOVA AGENT"}
      </h1>
      <p className="mt-2 text-sm text-slate-600">
        {isSignIn
          ? "Masuk untuk melihat organisasi yang bisa kamu akses."
          : "Buat akun untuk mulai menyiapkan organisasi pertamamu."}
      </p>

      <form onSubmit={handleSubmit} className="mt-6 space-y-4">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-slate-800">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 text-base outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-slate-800">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={isSignIn ? "current-password" : "new-password"}
            minLength={6}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 text-base outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
          />
        </div>

        {message ? (
          <p role="alert" className="text-sm text-rose-700">
            {message}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="min-h-11 w-full rounded-md bg-slate-950 px-4 py-2 font-medium text-white transition hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900/30 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Memproses..." : isSignIn ? "Masuk" : "Daftar"}
        </button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(isSignIn ? "sign-up" : "sign-in");
          setMessage(null);
        }}
        className="mt-4 min-h-11 w-full rounded-md px-4 py-2 text-sm font-medium text-slate-700 underline underline-offset-4 hover:text-slate-950 focus:outline-none focus:ring-2 focus:ring-slate-900/30"
      >
        {isSignIn ? "Belum punya akun? Daftar" : "Sudah punya akun? Masuk"}
      </button>
    </section>
  );
}

function OrganizationList() {
  const { client, user, signOut } = useSupabaseAuth();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [selectedOrganization, setSelectedOrganization] = useState<Organization | null>(null);

  const loadOrganizations = useCallback(async () => {
    if (!client || !user) {
      setOrganizations([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    const { data, error: queryError } = await client
      .from("organizations")
      .select("id,name,slug,created_at")
      .order("name", { ascending: true });

    if (queryError) {
      setError("Organisasi belum bisa dimuat. Coba lagi.");
      setLoading(false);
      return;
    }

    const parsed = organizationListSchema.safeParse(data);
    if (!parsed.success) {
      setError("Data organisasi dari Supabase tidak sesuai kontrak.");
      setLoading(false);
      return;
    }

    setOrganizations(parsed.data);
    setLoading(false);
  }, [client, user]);

  useEffect(() => {
    void loadOrganizations();
  }, [loadOrganizations]);

  async function handleSignOut() {
    setSigningOut(true);
    const result = await signOut();
    if (result.error) {
      setError(result.error);
    }
    setSigningOut(false);
  }

  if (loading) {
    return (
      <section aria-labelledby="organization-loading-title" aria-busy="true" className="mx-auto w-full max-w-4xl">
        <h1 id="organization-loading-title" className="text-2xl font-semibold text-slate-950">
          Organisasi
        </h1>
        <p role="status" className="mt-4 text-sm text-slate-600">
          Memuat organisasi...
        </p>
      </section>
    );
  }

  if (error) {
    return (
      <section aria-labelledby="organization-error-title" className="mx-auto w-full max-w-4xl">
        <h1 id="organization-error-title" className="text-2xl font-semibold text-slate-950">
          Organisasi
        </h1>
        <div role="alert" className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-rose-900">
          <p>{error}</p>
          <button
            type="button"
            onClick={() => void loadOrganizations()}
            className="mt-4 min-h-11 rounded-md border border-rose-300 px-4 py-2 text-sm font-medium hover:bg-rose-100 focus:outline-none focus:ring-2 focus:ring-rose-900/20"
          >
            Coba lagi
          </button>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="organization-title" className="mx-auto w-full max-w-4xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-600">NOVA AGENT</p>
          <h1 id="organization-title" className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">
            {selectedOrganization ? selectedOrganization.name : "Organisasi kamu"}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {selectedOrganization
              ? "Workspace organisasi yang sedang dipilih."
              : "Pilih organisasi untuk melanjutkan ke workspace."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {selectedOrganization ? (
            <button
              type="button"
              onClick={() => setSelectedOrganization(null)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-800 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
            >
              Ganti organisasi
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleSignOut}
            disabled={signingOut}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-800 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-900/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {signingOut ? "Keluar..." : "Keluar"}
          </button>
        </div>
      </div>

      <div className="mt-8">
        {selectedOrganization ? (
          <AgentDashboard organization={selectedOrganization} />
        ) : organizations.length === 0 ? (
          <DashboardPlaceholder />
        ) : (
          <ul aria-label="Daftar organisasi" className="grid gap-4 sm:grid-cols-2">
            {organizations.map((organization) => (
              <li key={organization.id}>
                <button
                  type="button"
                  onClick={() => setSelectedOrganization(organization)}
                  className="min-h-32 w-full rounded-lg border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-slate-400 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
                >
                  <span className="block text-lg font-semibold text-slate-950">{organization.name}</span>
                  <span className="mt-1 block text-sm text-slate-600">Slug: {organization.slug}</span>
                  <span className="mt-4 block text-sm font-medium text-slate-800">Buka workspace</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export default function Home() {
  const { loading, user } = useSupabaseAuth();

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8">
        <p role="status" className="text-sm text-slate-600">
          Memeriksa sesi...
        </p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-900 sm:px-6 lg:px-8">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] w-full items-center justify-center">
        {user ? <OrganizationList /> : <AuthForm />}
      </div>
    </main>
  );
}
