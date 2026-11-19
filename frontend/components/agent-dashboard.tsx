"use client";

import { useCallback, useEffect, useState } from "react";
import { z } from "zod";

import type { Organization } from "@/lib/types";
import { useSupabaseAuth } from "@/components/auth-provider";

const agentRowSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  draft_config: z.record(z.string(), z.unknown()),
  draft_revision: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
});

const agentRowListSchema = z.array(agentRowSchema);
const createdAgentSchema = z.object({
  id: z.string().uuid(),
  organizationId: z.string().uuid(),
  name: z.string().min(1),
  draftConfig: z.record(z.string(), z.unknown()),
  draftRevision: z.number().int().nonnegative(),
  createdAt: z.string(),
  updatedAt: z.string(),
});
const publishResponseSchema = z.object({
  version: z.object({
    id: z.string().uuid(),
    versionNumber: z.number().int().positive(),
    publishedAt: z.string(),
  }),
});

type Agent = z.infer<typeof agentRowSchema>;

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

function requestId() {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `nova-${Date.now()}`;
}

async function getApiError(response: Response) {
  if (response.status === 404) return "Agent atau organisasi tidak ditemukan.";
  if (response.status === 409) return "Publikasi bentrok karena ada versi baru. Muat ulang lalu coba lagi.";
  if (response.status === 401 || response.status === 403) return "Sesi tidak punya akses ke aksi ini.";
  return "API agent belum bisa dihubungi. Pastikan backend aktif dan konfigurasi frontend benar.";
}

export default function AgentDashboard({ organization }: { organization: Organization }) {
  const { client, session } = useSupabaseAuth();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [publishedVersions, setPublishedVersions] = useState<Record<string, number>>({});
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    if (!client) {
      setError("Supabase belum dikonfigurasi.");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    const { data, error: queryError } = await client
      .from("agents")
      .select("id,name,draft_config,draft_revision,created_at,updated_at")
      .eq("organization_id", organization.id)
      .order("name", { ascending: true });

    if (queryError) {
      setError("Daftar agent belum bisa dimuat. Pastikan migration agent sudah diterapkan.");
      setLoading(false);
      return;
    }

    const parsed = agentRowListSchema.safeParse(data);
    if (!parsed.success) {
      setError("Data agent dari Supabase tidak sesuai kontrak.");
      setLoading(false);
      return;
    }

    setAgents(parsed.data);
    setLoading(false);
  }, [client, organization.id]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  async function createAgent(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.access_token || !name.trim()) return;

    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/organizations/${organization.id}/agents`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
          "X-Request-ID": requestId(),
        },
        body: JSON.stringify({ name: name.trim(), draftConfig: {} }),
      });

      if (!response.ok) {
        setError(await getApiError(response));
        return;
      }

      const parsed = createdAgentSchema.safeParse(await response.json());
      if (!parsed.success) {
        setError("Respons pembuatan agent tidak sesuai kontrak.");
        return;
      }

      const created: Agent = {
        id: parsed.data.id,
        name: parsed.data.name,
        draft_config: parsed.data.draftConfig,
        draft_revision: parsed.data.draftRevision,
        created_at: parsed.data.createdAt,
        updated_at: parsed.data.updatedAt,
      };
      setAgents((current) => [...current, created].sort((left, right) => left.name.localeCompare(right.name)));
      setName("");
      setNotice("Draft agent berhasil dibuat.");
    } catch {
      setError("API agent belum bisa dihubungi. Pastikan backend aktif dan konfigurasi frontend benar.");
    } finally {
      setCreating(false);
    }
  }

  async function publishAgent(agentId: string) {
    if (!session?.access_token) return;

    setBusyAgentId(agentId);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/organizations/${organization.id}/agents/${agentId}/publish`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "Idempotency-Key": requestId(),
            "X-Request-ID": requestId(),
          },
        },
      );

      if (!response.ok) {
        setError(await getApiError(response));
        return;
      }

      const parsed = publishResponseSchema.safeParse(await response.json());
      if (!parsed.success) {
        setError("Respons publikasi agent tidak sesuai kontrak.");
        return;
      }

      setPublishedVersions((current) => ({
        ...current,
        [agentId]: parsed.data.version.versionNumber,
      }));
      setNotice(`Agent berhasil dipublikasikan sebagai versi ${parsed.data.version.versionNumber}.`);
    } catch {
      setError("API agent belum bisa dihubungi. Pastikan backend aktif dan konfigurasi frontend benar.");
    } finally {
      setBusyAgentId(null);
    }
  }

  return (
    <section aria-labelledby="agent-dashboard-title" className="space-y-6">
      <div className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 id="agent-dashboard-title" className="text-xl font-semibold text-slate-950">
            Agent draft
          </h2>
          <p className="mt-1 max-w-xl text-sm text-slate-600">
            Kelola draft agent di organisasi ini. Publish membuat snapshot versi yang immutable.
          </p>
        </div>
        <form onSubmit={createAgent} className="flex w-full flex-col gap-2 sm:max-w-sm sm:flex-row">
          <label htmlFor="agent-name" className="sr-only">
            Nama agent baru
          </label>
          <input
            id="agent-name"
            name="agentName"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Nama agent baru"
            maxLength={120}
            required
            className="min-h-11 min-w-0 flex-1 rounded-md border border-slate-300 px-3 text-base outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
          />
          <button
            type="submit"
            disabled={creating}
            className="min-h-11 rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {creating ? "Membuat..." : "Buat draft"}
          </button>
        </form>
      </div>

      {notice ? (
        <p role="status" className="text-sm text-emerald-700">
          {notice}
        </p>
      ) : null}

      {error ? (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
          <p>{error}</p>
          <button
            type="button"
            onClick={() => void loadAgents()}
            className="mt-3 min-h-11 rounded-md border border-rose-300 px-4 py-2 font-medium hover:bg-rose-100 focus:outline-none focus:ring-2 focus:ring-rose-900/20"
          >
            Muat ulang
          </button>
        </div>
      ) : null}

      {loading ? (
        <p role="status" aria-live="polite" className="text-sm text-slate-600">
          Memuat agent...
        </p>
      ) : agents.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-600">
          Belum ada agent draft. Gunakan form di atas untuk membuat draft pertama.
        </div>
      ) : (
        <ul aria-label="Daftar agent draft" className="grid gap-4 md:grid-cols-2">
          {agents.map((agent) => {
            const publishedVersion = publishedVersions[agent.id];
            const publishing = busyAgentId === agent.id;
            return (
              <li key={agent.id} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="text-lg font-semibold text-slate-950">{agent.name}</h3>
                    <p className="mt-1 text-sm text-slate-600">Draft revisi {agent.draft_revision}</p>
                  </div>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                    {publishedVersion ? `Versi ${publishedVersion}` : "Belum dipublish"}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => void publishAgent(agent.id)}
                  disabled={publishing}
                  className="mt-5 min-h-11 w-full rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-800 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-900/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {publishing ? "Mempublikasikan..." : "Publish versi"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
