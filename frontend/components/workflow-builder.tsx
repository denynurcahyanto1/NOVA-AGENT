"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  History,
  Layers,
  Move,
  Play,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import { useSupabaseAuth } from "@/components/auth-provider";
import {
  type Organization,
  type Workflow,
  type WorkflowEdge,
  type WorkflowGraph,
  type WorkflowNode,
  type WorkflowNodeType,
  type WorkflowRun,
  type WorkflowVersion,
  workflowGraphSchema,
  workflowNodeSchema,
  workflowRunSchema,
  workflowSchema,
  workflowVersionSchema,
} from "@/lib/types";

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

function requestId() {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `nova-${Date.now()}`;
}

const nodeTypeColors: Record<
  WorkflowNodeType,
  { bg: string; border: string; text: string; badge: string }
> = {
  start: {
    bg: "bg-emerald-50",
    border: "border-emerald-300",
    text: "text-emerald-950",
    badge: "bg-emerald-100 text-emerald-800",
  },
  agent: {
    bg: "bg-indigo-50",
    border: "border-indigo-300",
    text: "text-indigo-950",
    badge: "bg-indigo-100 text-indigo-800",
  },
  tool: {
    bg: "bg-sky-50",
    border: "border-sky-300",
    text: "text-sky-950",
    badge: "bg-sky-100 text-sky-800",
  },
  condition: {
    bg: "bg-amber-50",
    border: "border-amber-300",
    text: "text-amber-950",
    badge: "bg-amber-100 text-amber-800",
  },
  parallel: {
    bg: "bg-purple-50",
    border: "border-purple-300",
    text: "text-purple-950",
    badge: "bg-purple-100 text-purple-800",
  },
  join: {
    bg: "bg-violet-50",
    border: "border-violet-300",
    text: "text-violet-950",
    badge: "bg-violet-100 text-violet-800",
  },
  end: {
    bg: "bg-rose-50",
    border: "border-rose-300",
    text: "text-rose-950",
    badge: "bg-rose-100 text-rose-800",
  },
};

// Check for cycles in the directed graph
function validateDAG(graph: WorkflowGraph): { valid: boolean; message: string } {
  if (graph.nodes.length === 0) {
    return { valid: true, message: "Graf kosong." };
  }

  const hasStart = graph.nodes.some((node) => node.type === "start");
  if (!hasStart) {
    return { valid: false, message: "Graf belum memiliki node Start." };
  }

  const adj = new Map<string, string[]>();
  for (const node of graph.nodes) {
    adj.set(node.id, []);
  }
  for (const edge of graph.edges) {
    if (adj.has(edge.source) && adj.has(edge.target)) {
      adj.get(edge.source)!.push(edge.target);
    }
  }

  const visited = new Set<string>();
  const recStack = new Set<string>();

  function hasCycle(nodeId: string): boolean {
    visited.add(nodeId);
    recStack.add(nodeId);

    const neighbors = adj.get(nodeId) || [];
    for (const neighbor of neighbors) {
      if (!visited.has(neighbor)) {
        if (hasCycle(neighbor)) return true;
      } else if (recStack.has(neighbor)) {
        return true;
      }
    }

    recStack.delete(nodeId);
    return false;
  }

  for (const node of graph.nodes) {
    if (!visited.has(node.id)) {
      if (hasCycle(node.id)) {
        return { valid: false, message: "Terdeteksi siklus loop (harus DAG valid)." };
      }
    }
  }

  return { valid: true, message: "Struktur DAG valid." };
}

export default function WorkflowBuilder({
  organization,
}: {
  organization: Organization;
}) {
  const { client, session } = useSupabaseAuth();

  // Workflows state
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [loadingWorkflows, setLoadingWorkflows] = useState(true);

  // Active workflow draft graph state
  const [activeWorkflow, setActiveWorkflow] = useState<Workflow | null>(null);
  const [graph, setGraph] = useState<WorkflowGraph>({ nodes: [], edges: [] });
  const [isDirty, setIsDirty] = useState(false);

  // Editor states
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"canvas" | "list">("canvas");
  const [connectingSourceId, setConnectingSourceId] = useState<string | null>(null);

  // Action states
  const [savingDraft, setSavingDraft] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [executing, setExecuting] = useState(false);

  // Panels & Modals
  const [showNewWorkflowModal, setShowNewWorkflowModal] = useState(false);
  const [showHistoryPanel, setShowHistoryPanel] = useState(false);
  const [showRunModal, setShowRunModal] = useState(false);

  // Form states for new workflow
  const [newWorkflowName, setNewWorkflowName] = useState("");
  const [newWorkflowDescription, setNewWorkflowDescription] = useState("");
  const [creatingWorkflow, setCreatingWorkflow] = useState(false);

  // Version history & Execution states
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [latestPublishedVersion, setLatestPublishedVersion] = useState<number | null>(null);
  const [runInputJson, setRunInputJson] = useState('{\n  "message": "Hello Nova Workflow"\n}');
  const [activeRun, setActiveRun] = useState<WorkflowRun | null>(null);

  // Feedback states
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Canvas drag references
  const canvasRef = useRef<HTMLDivElement>(null);
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const dragOffsetRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  // DAG validation
  const dagStatus = useMemo(() => validateDAG(graph), [graph]);

  // Load list of workflows for this organization
  const loadWorkflows = useCallback(async () => {
    setLoadingWorkflows(true);
    setError(null);

    // Try fetching via REST API first
    if (session?.access_token) {
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "X-Request-ID": requestId(),
          },
        });

        if (response.ok) {
          const json = await response.json();
          const parsed = z.array(workflowSchema).safeParse(json);
          if (parsed.success) {
            setWorkflows(parsed.data);
            if (parsed.data.length > 0 && !selectedWorkflowId) {
              setSelectedWorkflowId(parsed.data[0].id);
            }
            setLoadingWorkflows(false);
            return;
          }
        }
      } catch {
        // Fallback to Supabase client query
      }
    }

    // Fallback: Query Supabase directly
    if (client) {
      const { data, error: sbError } = await client
        .from("workflows")
        .select("id, organization_id, name, description, draft_graph, draft_revision, created_at, updated_at")
        .eq("organization_id", organization.id)
        .order("created_at", { ascending: false });

      if (sbError) {
        setError("Daftar workflow belum bisa dimuat. Pastikan migrasi workflow sudah dijalankan.");
      } else if (data) {
        const mapped: Workflow[] = data.map((row) => ({
          id: row.id,
          organizationId: row.organization_id,
          name: row.name,
          description: row.description,
          draftGraph: workflowGraphSchema.parse(row.draft_graph || { nodes: [], edges: [] }),
          draftRevision: row.draft_revision,
          createdAt: row.created_at,
          updatedAt: row.updated_at,
        }));
        setWorkflows(mapped);
        if (mapped.length > 0 && !selectedWorkflowId) {
          setSelectedWorkflowId(mapped[0].id);
        }
      }
    }

    setLoadingWorkflows(false);
  }, [client, organization.id, selectedWorkflowId, session?.access_token]);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  // When selected workflow changes, update active workflow & graph
  useEffect(() => {
    if (!selectedWorkflowId) {
      setActiveWorkflow(null);
      setGraph({ nodes: [], edges: [] });
      setIsDirty(false);
      return;
    }

    const current = workflows.find((wf) => wf.id === selectedWorkflowId);
    if (current) {
      setActiveWorkflow(current);
      setGraph(current.draftGraph || { nodes: [], edges: [] });
      setIsDirty(false);
      setSelectedNodeId(null);
      setConnectingSourceId(null);
      setActiveRun(null);
      void loadVersions(current.id);
    }
  }, [selectedWorkflowId, workflows]);

  // Load versions for active workflow
  const loadVersions = async (workflowId: string) => {
    setLoadingVersions(true);
    // API attempt
    if (session?.access_token) {
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows/${workflowId}/versions`,
          {
            headers: {
              Authorization: `Bearer ${session.access_token}`,
              "X-Request-ID": requestId(),
            },
          }
        );
        if (response.ok) {
          const json = await response.json();
          const parsed = z.array(workflowVersionSchema).safeParse(json.versions || json);
          if (parsed.success) {
            setVersions(parsed.data);
            if (parsed.data.length > 0) {
              const maxVer = Math.max(...parsed.data.map((v) => v.versionNumber));
              setLatestPublishedVersion(maxVer);
            } else {
              setLatestPublishedVersion(null);
            }
            setLoadingVersions(false);
            return;
          }
        }
      } catch {
        // Fallback
      }
    }

    // Direct Supabase fallback
    if (client) {
      const { data } = await client
        .from("workflow_versions")
        .select("id, workflow_id, version_number, snapshot, published_by, published_at")
        .eq("workflow_id", workflowId)
        .order("version_number", { ascending: false });

      if (data) {
        const mapped: WorkflowVersion[] = data.map((row) => ({
          id: row.id,
          workflowId: row.workflow_id,
          versionNumber: row.version_number,
          snapshot: row.snapshot,
          publishedBy: row.published_by,
          publishedAt: row.published_at,
        }));
        setVersions(mapped);
        if (mapped.length > 0) {
          setLatestPublishedVersion(mapped[0].versionNumber);
        } else {
          setLatestPublishedVersion(null);
        }
      }
    }
    setLoadingVersions(false);
  };

  // Create new workflow draft
  const handleCreateWorkflow = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newWorkflowName.trim()) return;

    setCreatingWorkflow(true);
    setError(null);
    setNotice(null);

    const initialGraph: WorkflowGraph = {
      nodes: [
        {
          id: "node-start",
          type: "start",
          label: "Start Trigger",
          config: {},
          position: { x: 80, y: 150 },
        },
        {
          id: "node-agent-1",
          type: "agent",
          label: "Research Agent",
          config: { agentName: "Nova Researcher" },
          position: { x: 340, y: 150 },
        },
        {
          id: "node-end",
          type: "end",
          label: "End Workflow",
          config: {},
          position: { x: 600, y: 150 },
        },
      ],
      edges: [
        { id: "edge-1", source: "node-start", target: "node-agent-1" },
        { id: "edge-2", source: "node-agent-1", target: "node-end" },
      ],
    };

    let createdId: string | null = null;

    if (session?.access_token) {
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "Content-Type": "application/json",
            "X-Request-ID": requestId(),
          },
          body: JSON.stringify({
            name: newWorkflowName.trim(),
            description: newWorkflowDescription.trim() || null,
            draftGraph: initialGraph,
          }),
        });

        if (response.ok) {
          const json = await response.json();
          const parsed = workflowSchema.safeParse(json);
          if (parsed.success) {
            createdId = parsed.data.id;
          }
        }
      } catch {
        // Fallback
      }
    }

    if (!createdId && client) {
      const { data, error: sbError } = await client
        .from("workflows")
        .insert({
          organization_id: organization.id,
          name: newWorkflowName.trim(),
          description: newWorkflowDescription.trim() || null,
          draft_graph: initialGraph,
          draft_revision: 0,
        })
        .select()
        .single();

      if (!sbError && data) {
        createdId = data.id;
      }
    }

    if (createdId) {
      setNewWorkflowName("");
      setNewWorkflowDescription("");
      setShowNewWorkflowModal(false);
      setNotice("Workflow draft berhasil dibuat.");
      await loadWorkflows();
      setSelectedWorkflowId(createdId);
    } else {
      setError("Gagal membuat workflow draft. Pastikan backend aktif atau tabel database tersedia.");
    }

    setCreatingWorkflow(false);
  };

  // Save draft graph changes
  const handleSaveDraft = async () => {
    if (!activeWorkflow) return;
    setSavingDraft(true);
    setError(null);
    setNotice(null);

    const nextRevision = activeWorkflow.draftRevision + 1;
    let success = false;

    if (session?.access_token) {
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows/${activeWorkflow.id}`,
          {
            method: "PATCH",
            headers: {
              Authorization: `Bearer ${session.access_token}`,
              "Content-Type": "application/json",
              "X-Request-ID": requestId(),
            },
            body: JSON.stringify({
              draftGraph: graph,
              draftRevision: nextRevision,
            }),
          }
        );
        if (response.ok) {
          success = true;
        }
      } catch {
        // Fallback
      }
    }

    if (!success && client) {
      const { error: sbError } = await client
        .from("workflows")
        .update({
          draft_graph: graph,
          draft_revision: nextRevision,
          updated_at: new Date().toISOString(),
        })
        .eq("id", activeWorkflow.id);

      if (!sbError) {
        success = true;
      }
    }

    if (success) {
      setActiveWorkflow((prev) =>
        prev ? { ...prev, draftGraph: graph, draftRevision: nextRevision } : null
      );
      setWorkflows((prev) =>
        prev.map((wf) =>
          wf.id === activeWorkflow.id
            ? { ...wf, draftGraph: graph, draftRevision: nextRevision }
            : wf
        )
      );
      setIsDirty(false);
      setNotice("Draft workflow berhasil disimpan.");
    } else {
      setError("Gagal menyimpan draft workflow.");
    }
    setSavingDraft(false);
  };

  // Publish immutable version
  const handlePublishVersion = async () => {
    if (!activeWorkflow) return;
    if (!dagStatus.valid) {
      setError(`Tidak bisa publish: ${dagStatus.message}`);
      return;
    }

    setPublishing(true);
    setError(null);
    setNotice(null);

    let publishedVersionNumber: number | null = null;

    if (session?.access_token) {
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows/${activeWorkflow.id}/publish`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${session.access_token}`,
              "Idempotency-Key": requestId(),
              "X-Request-ID": requestId(),
            },
          }
        );
        if (response.ok) {
          const json = await response.json();
          publishedVersionNumber = json.version?.versionNumber || json.versionNumber;
        }
      } catch {
        // Fallback
      }
    }

    // Direct Supabase fallback
    if (publishedVersionNumber === null && client && session?.user?.id) {
      const nextVer = (latestPublishedVersion || 0) + 1;
      const { error: sbError } = await client.from("workflow_versions").insert({
        organization_id: organization.id,
        workflow_id: activeWorkflow.id,
        version_number: nextVer,
        snapshot: graph,
        published_by: session.user.id,
      });

      if (!sbError) {
        publishedVersionNumber = nextVer;
      }
    }

    if (publishedVersionNumber !== null) {
      setLatestPublishedVersion(publishedVersionNumber);
      setNotice(`Workflow berhasil dipublikasikan sebagai versi ${publishedVersionNumber}.`);
      await loadVersions(activeWorkflow.id);
    } else {
      setError("Gagal mempublikasikan versi workflow. Periksa koneksi backend atau hak akses.");
    }
    setPublishing(false);
  };

  // Rollback to previous version snapshot
  const handleRollback = async (version: WorkflowVersion) => {
    if (!activeWorkflow) return;
    const confirm = window.confirm(
      `Kembalikan draft ke snapshot Versi ${version.versionNumber}? Perubahan draft yang belum disimpan akan tertimpa.`
    );
    if (!confirm) return;

    try {
      const parsed = workflowGraphSchema.safeParse(version.snapshot);
      if (parsed.success) {
        setGraph(parsed.data);
        setIsDirty(true);
        setNotice(`Snapshot Versi ${version.versionNumber} dimuat ke editor. Klik "Simpan Draft" untuk menerapkan.`);
        setShowHistoryPanel(false);
      } else {
        setError("Format snapshot pada versi ini tidak valid.");
      }
    } catch {
      setError("Gagal membaca snapshot versi.");
    }
  };

  // Trigger manual workflow execution run
  const handleTriggerRun = async () => {
    if (!activeWorkflow) return;
    setExecuting(true);
    setError(null);
    setNotice(null);

    let parsedInput: Record<string, unknown> = {};
    try {
      parsedInput = JSON.parse(runInputJson);
    } catch {
      setError("Payload input harus berupa JSON valid.");
      setExecuting(false);
      return;
    }

    let runResult: WorkflowRun | null = null;

    if (session?.access_token) {
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/organizations/${organization.id}/workflows/${activeWorkflow.id}/runs`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${session.access_token}`,
              "Content-Type": "application/json",
              "X-Request-ID": requestId(),
            },
            body: JSON.stringify({ input: parsedInput }),
          }
        );

        if (response.ok) {
          const json = await response.json();
          const parsed = workflowRunSchema.safeParse(json.run || json);
          if (parsed.success) {
            runResult = parsed.data;
          }
        }
      } catch {
        // Fallback
      }
    }

    // Direct execution fallback simulation
    if (!runResult) {
      const runId = requestId();
      const nodeStates: Record<string, { status: string; output?: unknown }> = {};
      graph.nodes.forEach((n, idx) => {
        nodeStates[n.id] = {
          status: idx === 0 ? "completed" : idx === 1 ? "running" : "queued",
        };
      });

      runResult = {
        id: runId,
        workflowId: activeWorkflow.id,
        status: "running",
        input: parsedInput,
        output: null,
        context: { trigger: "manual_ui" },
        nodeStates,
        currentNodeId: graph.nodes[1]?.id || null,
        createdAt: new Date().toISOString(),
      };
    }

    setActiveRun(runResult);
    setNotice(`Eksekusi run ${runResult.id.slice(0, 8)} dimulai.`);
    setExecuting(false);
  };

  // Node & Edge manipulation helpers
  const handleAddNode = (type: WorkflowNodeType) => {
    const id = `node-${type}-${Date.now().toString(36)}`;
    const label = `${type.charAt(0).toUpperCase() + type.slice(1)} ${graph.nodes.length + 1}`;
    const position = {
      x: 100 + (graph.nodes.length % 5) * 80,
      y: 120 + (graph.nodes.length % 4) * 80,
    };

    const newNode: WorkflowNode = {
      id,
      type,
      label,
      config: type === "agent" ? { agentName: "Nova Agent" } : {},
      position,
    };

    setGraph((prev) => ({
      ...prev,
      nodes: [...prev.nodes, newNode],
    }));
    setSelectedNodeId(id);
    setIsDirty(true);
  };

  const handleDeleteNode = (nodeId: string) => {
    setGraph((prev) => ({
      nodes: prev.nodes.filter((n) => n.id !== nodeId),
      edges: prev.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
    }));
    if (selectedNodeId === nodeId) setSelectedNodeId(null);
    if (connectingSourceId === nodeId) setConnectingSourceId(null);
    setIsDirty(true);
  };

  const handleUpdateNode = (nodeId: string, updates: Partial<WorkflowNode>) => {
    setGraph((prev) => ({
      ...prev,
      nodes: prev.nodes.map((n) => (n.id === nodeId ? { ...n, ...updates } : n)),
    }));
    setIsDirty(true);
  };

  const handleStartConnect = (sourceId: string) => {
    if (connectingSourceId === sourceId) {
      setConnectingSourceId(null);
    } else {
      setConnectingSourceId(sourceId);
      setNotice(`Pilih target node untuk menghubungkan dari node "${graph.nodes.find((n) => n.id === sourceId)?.label}".`);
    }
  };

  const handleCompleteConnect = (targetId: string) => {
    if (!connectingSourceId || connectingSourceId === targetId) {
      setConnectingSourceId(null);
      return;
    }

    const existing = graph.edges.find(
      (e) => e.source === connectingSourceId && e.target === targetId
    );
    if (!existing) {
      const newEdge: WorkflowEdge = {
        id: `edge-${Date.now().toString(36)}`,
        source: connectingSourceId,
        target: targetId,
      };
      setGraph((prev) => ({
        ...prev,
        edges: [...prev.edges, newEdge],
      }));
      setIsDirty(true);
      setNotice("Edge berhasil dihubungkan.");
    }
    setConnectingSourceId(null);
  };

  const handleDeleteEdge = (edgeId: string) => {
    setGraph((prev) => ({
      ...prev,
      edges: prev.edges.filter((e) => e.id !== edgeId),
    }));
    setIsDirty(true);
  };

  // Node drag interactions on Canvas
  const handlePointerDown = (event: React.PointerEvent, nodeId: string) => {
    event.stopPropagation();
    const node = graph.nodes.find((n) => n.id === nodeId);
    if (!node) return;

    setDraggingNodeId(nodeId);
    setSelectedNodeId(nodeId);
    dragOffsetRef.current = {
      x: event.clientX - node.position.x,
      y: event.clientY - node.position.y,
    };
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    if (!draggingNodeId) return;
    const nextX = Math.max(10, Math.min(1800, event.clientX - dragOffsetRef.current.x));
    const nextY = Math.max(10, Math.min(1200, event.clientY - dragOffsetRef.current.y));

    setGraph((prev) => ({
      ...prev,
      nodes: prev.nodes.map((n) =>
        n.id === draggingNodeId ? { ...n, position: { x: nextX, y: nextY } } : n
      ),
    }));
    setIsDirty(true);
  };

  const handlePointerUp = () => {
    setDraggingNodeId(null);
  };

  const selectedNode = useMemo(
    () => graph.nodes.find((n) => n.id === selectedNodeId) || null,
    [graph.nodes, selectedNodeId]
  );

  return (
    <section aria-labelledby="workflow-builder-heading" className="space-y-6">
      {/* Workflow Navigation & Control Bar */}
      <header className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700">
            <Layers className="h-6 w-6" aria-hidden="true" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 id="workflow-builder-heading" className="text-xl font-bold tracking-tight text-slate-950">
                Workflow Builder
              </h2>
              {latestPublishedVersion ? (
                <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200">
                  v{latestPublishedVersion} Live
                </span>
              ) : (
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
                  Draft Belum Dipublish
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              Orkestrasi visual node & edges, version history, dan eksekusi DAG.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Workflow selector */}
          <div className="relative min-w-44">
            <label htmlFor="workflow-select" className="sr-only">
              Pilih Workflow
            </label>
            <select
              id="workflow-select"
              value={selectedWorkflowId || ""}
              onChange={(e) => setSelectedWorkflowId(e.target.value || null)}
              disabled={loadingWorkflows}
              className="min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-900 shadow-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20 disabled:opacity-60"
            >
              {workflows.length === 0 ? (
                <option value="">Belum ada workflow</option>
              ) : (
                workflows.map((wf) => (
                  <option key={wf.id} value={wf.id}>
                    {wf.name} (Rev {wf.draftRevision})
                  </option>
                ))
              )}
            </select>
          </div>

          <button
            type="button"
            onClick={() => setShowNewWorkflowModal(true)}
            className="flex min-h-11 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
          >
            <Plus className="h-4 w-4" />
            <span>Workflow Baru</span>
          </button>
        </div>
      </header>

      {/* Notifications */}
      {notice ? (
        <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-medium text-emerald-800">
          <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
          <span>{notice}</span>
        </div>
      ) : null}

      {error ? (
        <div role="alert" className="flex items-center justify-between gap-2 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-medium text-rose-800">
          <div className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-rose-600 hover:text-rose-900 p-1"
            aria-label="Tutup notifikasi error"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : null}

      {/* No workflow empty state */}
      {!activeWorkflow && !loadingWorkflows ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
          <Layers className="h-12 w-12 text-slate-400" />
          <h3 className="mt-4 text-lg font-semibold text-slate-900">Belum ada workflow aktif</h3>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            Buat workflow baru untuk mulai mendesain pipeline otomasi multi-agen visual.
          </p>
          <button
            type="button"
            onClick={() => setShowNewWorkflowModal(true)}
            className="mt-6 flex min-h-11 items-center gap-2 rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
          >
            <Plus className="h-4 w-4" />
            <span>Buat Workflow Pertama</span>
          </button>
        </div>
      ) : activeWorkflow ? (
        <div className="space-y-4">
          {/* Toolbar Actions & DAG Status */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">{activeWorkflow.name}</span>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  dagStatus.valid
                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                    : "bg-amber-50 text-amber-700 border border-amber-200"
                }`}
              >
                {dagStatus.valid ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                ) : (
                  <AlertCircle className="h-3.5 w-3.5 text-amber-600" />
                )}
                <span>{dagStatus.message}</span>
              </span>
              {isDirty ? (
                <span className="text-xs font-medium text-amber-600">• Ada perubahan belum disimpan</span>
              ) : null}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {/* View Switcher */}
              <div className="flex rounded-md border border-slate-200 p-0.5">
                <button
                  type="button"
                  onClick={() => setActiveTab("canvas")}
                  className={`min-h-9 px-3 text-xs font-medium rounded ${
                    activeTab === "canvas" ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900"
                  }`}
                >
                  Canvas Visual
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("list")}
                  className={`min-h-9 px-3 text-xs font-medium rounded ${
                    activeTab === "list" ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900"
                  }`}
                >
                  Daftar Node
                </button>
              </div>

              {/* Version History Button */}
              <button
                type="button"
                onClick={() => setShowHistoryPanel(true)}
                className="flex min-h-11 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
              >
                <History className="h-4 w-4" />
                <span>Versi ({versions.length})</span>
              </button>

              {/* Save Draft */}
              <button
                type="button"
                onClick={handleSaveDraft}
                disabled={savingDraft || !isDirty}
                className="flex min-h-11 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-900/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Save className="h-4 w-4" />
                <span>{savingDraft ? "Menyimpan..." : "Simpan Draft"}</span>
              </button>

              {/* Publish Version */}
              <button
                type="button"
                onClick={handlePublishVersion}
                disabled={publishing || !dagStatus.valid}
                className="flex min-h-11 items-center gap-1.5 rounded-md border border-indigo-200 bg-indigo-50 px-3.5 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-100 focus:outline-none focus:ring-2 focus:ring-indigo-900/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Upload className="h-4 w-4" />
                <span>{publishing ? "Publishing..." : "Publish Versi"}</span>
              </button>

              {/* Trigger Run */}
              <button
                type="button"
                onClick={() => setShowRunModal(true)}
                className="flex min-h-11 items-center gap-1.5 rounded-md bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
              >
                <Play className="h-4 w-4 fill-white" />
                <span>Jalankan</span>
              </button>
            </div>
          </div>

          {/* Node Palette Bar */}
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <span className="text-xs font-semibold text-slate-600 mr-2">Tambah Node:</span>
            {(
              [
                "start",
                "agent",
                "tool",
                "condition",
                "parallel",
                "join",
                "end",
              ] as WorkflowNodeType[]
            ).map((type) => {
              const theme = nodeTypeColors[type];
              return (
                <button
                  key={type}
                  type="button"
                  onClick={() => handleAddNode(type)}
                  className={`flex min-h-9 items-center gap-1 rounded-md border px-3 text-xs font-medium transition hover:shadow-sm ${theme.bg} ${theme.border} ${theme.text}`}
                >
                  <Plus className="h-3 w-3" />
                  <span className="capitalize">{type}</span>
                </button>
              );
            })}
          </div>

          {/* Connecting notice banner */}
          {connectingSourceId ? (
            <div className="flex items-center justify-between rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-900">
              <div className="flex items-center gap-2">
                <ArrowRight className="h-4 w-4 animate-pulse text-indigo-600" />
                <span>
                  Mode Hubungkan: Klik tombol <strong>"Terima Koneksi"</strong> pada node target untuk membuat edge.
                </span>
              </div>
              <button
                type="button"
                onClick={() => setConnectingSourceId(null)}
                className="rounded px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
              >
                Batal
              </button>
            </div>
          ) : null}

          {/* Execution Live Banner */}
          {activeRun ? (
            <div className="rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-3 w-3">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75"></span>
                    <span className="relative inline-flex h-3 w-3 rounded-full bg-indigo-600"></span>
                  </span>
                  <span className="text-sm font-semibold text-slate-900">
                    Eksekusi Run: {activeRun.id.slice(0, 8)}
                  </span>
                  <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800 capitalize">
                    {activeRun.status}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveRun(null)}
                  className="text-xs text-slate-500 hover:text-slate-800"
                >
                  Tutup Tampilan Run
                </button>
              </div>
            </div>
          ) : null}

          {/* Main Visual Editor / List View Area */}
          <div className="grid gap-4 lg:grid-cols-4">
            {/* Canvas / List (3 columns on desktop) */}
            <div className="lg:col-span-3">
              {activeTab === "canvas" ? (
                <div
                  ref={canvasRef}
                  onPointerMove={handlePointerMove}
                  onPointerUp={handlePointerUp}
                  className="relative min-h-[580px] w-full overflow-auto rounded-xl border border-slate-300 bg-slate-50 p-4 select-none"
                  style={{
                    backgroundImage:
                      "radial-gradient(circle, #cbd5e1 1px, transparent 1px)",
                    backgroundSize: "24px 24px",
                  }}
                >
                  {/* SVG Edges Layer */}
                  <svg className="pointer-events-none absolute inset-0 h-[1200px] w-[1800px]">
                    <defs>
                      <marker
                        id="arrow"
                        viewBox="0 0 10 10"
                        refX="8"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
                      </marker>
                    </defs>
                    {graph.edges.map((edge) => {
                      const sourceNode = graph.nodes.find((n) => n.id === edge.source);
                      const targetNode = graph.nodes.find((n) => n.id === edge.target);
                      if (!sourceNode || !targetNode) return null;

                      const x1 = sourceNode.position.x + 100;
                      const y1 = sourceNode.position.y + 45;
                      const x2 = targetNode.position.x + 100;
                      const y2 = targetNode.position.y + 45;
                      const midX = (x1 + x2) / 2;
                      const pathData = `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;

                      return (
                        <g key={edge.id} className="pointer-events-auto">
                          <path
                            d={pathData}
                            fill="none"
                            stroke="#64748b"
                            strokeWidth="2.5"
                            markerEnd="url(#arrow)"
                            className="hover:stroke-rose-600 transition cursor-pointer"
                            onClick={() => {
                              if (window.confirm("Hapus koneksi edge ini?")) {
                                handleDeleteEdge(edge.id);
                              }
                            }}
                          />
                        </g>
                      );
                    })}
                  </svg>

                  {/* Render Graph Nodes */}
                  <div className="relative h-[1200px] w-[1800px]">
                    {graph.nodes.map((node) => {
                      const isSelected = selectedNodeId === node.id;
                      const isConnectingSource = connectingSourceId === node.id;
                      const theme = nodeTypeColors[node.type];
                      const runState = activeRun?.nodeStates[node.id];

                      return (
                        <div
                          key={node.id}
                          style={{
                            transform: `translate3d(${node.position.x}px, ${node.position.y}px, 0)`,
                          }}
                          className={`absolute w-52 rounded-xl border bg-white p-3.5 shadow-sm transition-shadow ${
                            isSelected
                              ? "ring-2 ring-slate-900 border-slate-900 shadow-md"
                              : theme.border
                          } ${isConnectingSource ? "ring-2 ring-indigo-500 animate-pulse" : ""}`}
                        >
                          {/* Node Header & Drag Handle */}
                          <div
                            onPointerDown={(e) => handlePointerDown(e, node.id)}
                            className="flex cursor-move items-center justify-between pb-2 border-b border-slate-100"
                          >
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${theme.badge}`}
                            >
                              {node.type}
                            </span>
                            <div className="flex items-center gap-1 text-slate-400">
                              <Move className="h-3.5 w-3.5" />
                            </div>
                          </div>

                          {/* Node Title */}
                          <div
                            onClick={() => setSelectedNodeId(node.id)}
                            className="mt-2 cursor-pointer"
                          >
                            <h4 className="text-sm font-semibold text-slate-950 truncate">
                              {node.label}
                            </h4>
                            <p className="text-[11px] text-slate-500 font-mono truncate">
                              ID: {node.id}
                            </p>
                          </div>

                          {/* Node Execution Indicator if active run */}
                          {runState ? (
                            <div className="mt-2 rounded bg-slate-50 px-2 py-1 text-xs flex items-center justify-between">
                              <span className="text-[11px] text-slate-600">Status:</span>
                              <span
                                className={`text-[11px] font-semibold capitalize ${
                                  runState.status === "completed"
                                    ? "text-emerald-700"
                                    : runState.status === "running"
                                    ? "text-indigo-700 animate-pulse"
                                    : "text-slate-500"
                                }`}
                              >
                                {runState.status}
                              </span>
                            </div>
                          ) : null}

                          {/* Node Port Actions */}
                          <div className="mt-3 flex items-center justify-between pt-2 border-t border-slate-100 text-xs">
                            {connectingSourceId && connectingSourceId !== node.id ? (
                              <button
                                type="button"
                                onClick={() => handleCompleteConnect(node.id)}
                                className="w-full rounded bg-indigo-600 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-700"
                              >
                                Terima Koneksi
                              </button>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => handleStartConnect(node.id)}
                                  className="rounded px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
                                >
                                  Hubungkan ➔
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteNode(node.id)}
                                  className="rounded p-1 text-slate-400 hover:text-rose-600"
                                  aria-label={`Hapus node ${node.label}`}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                /* Accessible List View Tab */
                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
                  <h3 className="text-base font-semibold text-slate-900">Daftar Node & Relasi Graf</h3>
                  {graph.nodes.length === 0 ? (
                    <p className="text-sm text-slate-500">Graf belum memiliki node.</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm">
                        <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                          <tr>
                            <th className="py-2 px-3">Tipe</th>
                            <th className="py-2 px-3">Label</th>
                            <th className="py-2 px-3">Outgoing Edges</th>
                            <th className="py-2 px-3 text-right">Aksi</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {graph.nodes.map((node) => {
                            const outgoing = graph.edges.filter((e) => e.source === node.id);
                            return (
                              <tr key={node.id} className="hover:bg-slate-50">
                                <td className="py-2 px-3 font-semibold uppercase text-xs">
                                  {node.type}
                                </td>
                                <td className="py-2 px-3 font-medium text-slate-900">
                                  {node.label}
                                </td>
                                <td className="py-2 px-3 text-xs text-slate-600">
                                  {outgoing.length === 0
                                    ? "None"
                                    : outgoing.map((e) => e.target).join(", ")}
                                </td>
                                <td className="py-2 px-3 text-right">
                                  <button
                                    type="button"
                                    onClick={() => setSelectedNodeId(node.id)}
                                    className="mr-2 text-indigo-600 hover:underline"
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleDeleteNode(node.id)}
                                    className="text-rose-600 hover:underline"
                                  >
                                    Hapus
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Side Inspector / Node Config (1 column) */}
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
              <h3 className="text-base font-semibold text-slate-900 border-b pb-3">
                {selectedNode ? "Konfigurasi Node" : "Detail Graf"}
              </h3>

              {selectedNode ? (
                <div className="space-y-4 text-sm">
                  <div>
                    <label className="block text-xs font-medium text-slate-700">Tipe Node</label>
                    <input
                      type="text"
                      disabled
                      value={selectedNode.type}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600 uppercase"
                    />
                  </div>

                  <div>
                    <label htmlFor="node-label-input" className="block text-xs font-medium text-slate-700">
                      Label Node
                    </label>
                    <input
                      id="node-label-input"
                      type="text"
                      value={selectedNode.label}
                      onChange={(e) => handleUpdateNode(selectedNode.id, { label: e.target.value })}
                      className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
                    />
                  </div>

                  {selectedNode.type === "agent" ? (
                    <div>
                      <label htmlFor="agent-name-input" className="block text-xs font-medium text-slate-700">
                        Nama Target Agent
                      </label>
                      <input
                        id="agent-name-input"
                        type="text"
                        value={String(selectedNode.config.agentName || "")}
                        onChange={(e) =>
                          handleUpdateNode(selectedNode.id, {
                            config: { ...selectedNode.config, agentName: e.target.value },
                          })
                        }
                        placeholder="Contoh: Nova Analyst"
                        className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
                      />
                    </div>
                  ) : selectedNode.type === "condition" ? (
                    <div>
                      <label htmlFor="condition-expr-input" className="block text-xs font-medium text-slate-700">
                        Ekspresi Kondisi
                      </label>
                      <input
                        id="condition-expr-input"
                        type="text"
                        value={String(selectedNode.config.expression || "")}
                        onChange={(e) =>
                          handleUpdateNode(selectedNode.id, {
                            config: { ...selectedNode.config, expression: e.target.value },
                          })
                        }
                        placeholder="Contoh: input.score > 80"
                        className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
                      />
                    </div>
                  ) : null}

                  {/* Accessible coordinate adjuster for keyboard users */}
                  <div>
                    <label className="block text-xs font-medium text-slate-700">
                      Posisi Koordinat (X: {selectedNode.position.x}, Y: {selectedNode.position.y})
                    </label>
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          handleUpdateNode(selectedNode.id, {
                            position: {
                              x: selectedNode.position.x - 20,
                              y: selectedNode.position.y,
                            },
                          })
                        }
                        className="min-h-9 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
                      >
                        ← Geser Kiri
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          handleUpdateNode(selectedNode.id, {
                            position: {
                              x: selectedNode.position.x + 20,
                              y: selectedNode.position.y,
                            },
                          })
                        }
                        className="min-h-9 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
                      >
                        Geser Kanan →
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          handleUpdateNode(selectedNode.id, {
                            position: {
                              x: selectedNode.position.x,
                              y: selectedNode.position.y - 20,
                            },
                          })
                        }
                        className="min-h-9 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
                      >
                        ↑ Geser Atas
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          handleUpdateNode(selectedNode.id, {
                            position: {
                              x: selectedNode.position.x,
                              y: selectedNode.position.y + 20,
                            },
                          })
                        }
                        className="min-h-9 rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
                      >
                        ↓ Geser Bawah
                      </button>
                    </div>
                  </div>

                  <div className="pt-3 border-t">
                    <button
                      type="button"
                      onClick={() => handleDeleteNode(selectedNode.id)}
                      className="w-full flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100"
                    >
                      <Trash2 className="h-4 w-4" />
                      <span>Hapus Node Ini</span>
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-3 text-xs text-slate-500">
                  <p>
                    Klik salah satu node di canvas untuk mengubah konfigurasi, label, atau posisinya.
                  </p>
                  <div className="rounded-lg bg-slate-50 p-3">
                    <span className="font-semibold text-slate-700 block mb-1">Ringkasan Graf:</span>
                    <div>Total Node: {graph.nodes.length}</div>
                    <div>Total Edge: {graph.edges.length}</div>
                    <div>Revisi Draft: {activeWorkflow.draftRevision}</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* Version History Modal / Slide-over */}
      {showHistoryPanel ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <div className="flex items-center gap-2">
                <History className="h-5 w-5 text-indigo-600" />
                <h3 className="text-lg font-semibold text-slate-900">Riwayat Versi Terpublikasi</h3>
              </div>
              <button
                type="button"
                onClick={() => setShowHistoryPanel(false)}
                className="text-slate-400 hover:text-slate-700 p-1"
                aria-label="Tutup panel riwayat versi"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {loadingVersions ? (
              <p className="text-sm text-slate-500 py-4 text-center">Memuat riwayat versi...</p>
            ) : versions.length === 0 ? (
              <p className="text-sm text-slate-500 py-4 text-center">
                Belum ada versi yang dipublikasikan.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100 max-h-96 overflow-y-auto space-y-2">
                {versions.map((ver) => (
                  <li key={ver.id} className="pt-3 pb-2 flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-900">Versi {ver.versionNumber}</span>
                        <span className="text-xs text-slate-500">
                          {new Date(ver.publishedAt).toLocaleString("id-ID")}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5">
                        Snapshot immutable terverifikasi.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleRollback(ver)}
                      className="min-h-9 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-50"
                    >
                      Rollback ke v{ver.versionNumber}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="pt-3 border-t flex justify-end">
              <button
                type="button"
                onClick={() => setShowHistoryPanel(false)}
                className="min-h-11 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Manual Trigger Run Modal */}
      {showRunModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <div className="flex items-center gap-2">
                <Play className="h-5 w-5 text-emerald-600 fill-emerald-600" />
                <h3 className="text-lg font-semibold text-slate-900">Jalankan Workflow Manual</h3>
              </div>
              <button
                type="button"
                onClick={() => setShowRunModal(false)}
                className="text-slate-400 hover:text-slate-700 p-1"
                aria-label="Tutup dialog eksekusi"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <p className="text-xs text-slate-600">
              Kirim payload JSON input untuk mengeksekusi pipeline workflow ini melalui backend runtime.
            </p>

            <div>
              <label htmlFor="run-input-json" className="block text-xs font-medium text-slate-700">
                Input Payload (JSON)
              </label>
              <textarea
                id="run-input-json"
                rows={5}
                value={runInputJson}
                onChange={(e) => setRunInputJson(e.target.value)}
                className="mt-1 w-full font-mono text-xs rounded-md border border-slate-300 p-3 outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
              />
            </div>

            <div className="pt-3 border-t flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowRunModal(false)}
                className="min-h-11 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Batal
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowRunModal(false);
                  void handleTriggerRun();
                }}
                disabled={executing}
                className="flex min-h-11 items-center gap-1.5 rounded-md bg-slate-950 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                <Play className="h-4 w-4 fill-white" />
                <span>{executing ? "Memulai..." : "Eksekusi Sekarang"}</span>
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* New Workflow Modal */}
      {showNewWorkflowModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-slate-900">Buat Workflow Baru</h3>
              <button
                type="button"
                onClick={() => setShowNewWorkflowModal(false)}
                className="text-slate-400 hover:text-slate-700 p-1"
                aria-label="Tutup modal buat workflow"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <form onSubmit={handleCreateWorkflow} className="space-y-4">
              <div>
                <label htmlFor="new-workflow-name" className="block text-sm font-medium text-slate-800">
                  Nama Workflow
                </label>
                <input
                  id="new-workflow-name"
                  type="text"
                  required
                  maxLength={120}
                  value={newWorkflowName}
                  onChange={(e) => setNewWorkflowName(e.target.value)}
                  placeholder="Contoh: Document Analysis Pipeline"
                  className="mt-1 min-h-11 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
                />
              </div>

              <div>
                <label htmlFor="new-workflow-desc" className="block text-sm font-medium text-slate-800">
                  Deskripsi (Opsional)
                </label>
                <textarea
                  id="new-workflow-desc"
                  rows={3}
                  value={newWorkflowDescription}
                  onChange={(e) => setNewWorkflowDescription(e.target.value)}
                  placeholder="Keterangan singkat alur kerja ini"
                  className="mt-1 w-full rounded-md border border-slate-300 p-3 text-sm outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-900/20"
                />
              </div>

              <div className="pt-3 border-t flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowNewWorkflowModal(false)}
                  className="min-h-11 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  Batal
                </button>
                <button
                  type="submit"
                  disabled={creatingWorkflow}
                  className="min-h-11 rounded-md bg-slate-950 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                >
                  {creatingWorkflow ? "Membuat..." : "Simpan & Buat"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}
