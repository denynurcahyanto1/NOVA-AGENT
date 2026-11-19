import { z } from "zod";

export const organizationSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  slug: z.string().min(1),
  created_at: z.string(),
});

export type Organization = z.infer<typeof organizationSchema>;

export const workflowNodeTypeSchema = z.enum([
  "start",
  "agent",
  "tool",
  "condition",
  "parallel",
  "join",
  "end",
]);
export type WorkflowNodeType = z.infer<typeof workflowNodeTypeSchema>;

export const workflowNodeSchema = z.object({
  id: z.string().min(1),
  type: workflowNodeTypeSchema,
  label: z.string().min(1),
  config: z.record(z.string(), z.unknown()).default({}),
  position: z
    .object({
      x: z.number(),
      y: z.number(),
    })
    .default({ x: 0, y: 0 }),
});
export type WorkflowNode = z.infer<typeof workflowNodeSchema>;

export const workflowEdgeSchema = z.object({
  id: z.string().min(1),
  source: z.string().min(1),
  target: z.string().min(1),
  condition: z.string().optional(),
});
export type WorkflowEdge = z.infer<typeof workflowEdgeSchema>;

export const workflowGraphSchema = z.object({
  nodes: z.array(workflowNodeSchema).default([]),
  edges: z.array(workflowEdgeSchema).default([]),
});
export type WorkflowGraph = z.infer<typeof workflowGraphSchema>;

export const workflowSchema = z.object({
  id: z.string().uuid(),
  organizationId: z.string().uuid(),
  name: z.string().min(1).max(120),
  description: z.string().nullable().optional(),
  draftGraph: workflowGraphSchema.default({ nodes: [], edges: [] }),
  draftRevision: z.number().int().nonnegative().default(0),
  archivedAt: z.string().nullable().optional(),
  createdAt: z.string(),
  updatedAt: z.string(),
});
export type Workflow = z.infer<typeof workflowSchema>;

export const workflowVersionSchema = z.object({
  id: z.string().uuid(),
  workflowId: z.string().uuid(),
  versionNumber: z.number().int().positive(),
  snapshot: z.unknown(),
  publishedBy: z.string().uuid().optional(),
  publishedAt: z.string(),
});
export type WorkflowVersion = z.infer<typeof workflowVersionSchema>;

export const workflowRunStatusSchema = z.enum([
  "queued",
  "running",
  "paused",
  "completed",
  "failed",
  "cancelled",
]);
export type WorkflowRunStatus = z.infer<typeof workflowRunStatusSchema>;

export const workflowRunSchema = z.object({
  id: z.string().uuid(),
  workflowId: z.string().uuid(),
  workflowVersionId: z.string().uuid().optional(),
  status: workflowRunStatusSchema,
  input: z.record(z.string(), z.unknown()).default({}),
  output: z.record(z.string(), z.unknown()).nullable().optional(),
  context: z.record(z.string(), z.unknown()).default({}),
  nodeStates: z
    .record(
      z.string(),
      z.object({
        status: z.string(),
        output: z.unknown().optional(),
        error: z.string().optional(),
      })
    )
    .default({}),
  currentNodeId: z.string().nullable().optional(),
  error: z.unknown().nullable().optional(),
  startedAt: z.string().nullable().optional(),
  endedAt: z.string().nullable().optional(),
  createdAt: z.string(),
});
export type WorkflowRun = z.infer<typeof workflowRunSchema>;
