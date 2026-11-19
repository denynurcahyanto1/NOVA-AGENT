# Multi-Agent Orchestration Design

## 1. TaskGraph
Representasi DAG (Directed Acyclic Graph) untuk tugas agen. 
- Node: `Task` (id, status, agent_id, function_call).
- Edge: `Dependency` (parent_task_id -> child_task_id).

## 2. SharedContext
Penyimpanan *state* terpusat untuk tugas.
- Skema: `organization_id` (UUID), `context_data` (JSONB).
- RLS: `organization_id` enforced.

## 3. Dependency Resolution
Logika *Topological Sort* untuk menentukan urutan eksekusi tugas.

## 4. Security & Delegation
- Delegasi: `agent_a` -> `agent_b` hanya jika keduanya dalam satu organisasi.
- RLS: Kebijakan `tasks` tabel menggunakan `organization_memberships` untuk validasi `agent_id` dan `organization_id`.
