# QA Remediation Plan — GET /api/v1/organizations/{id}

**Source:** NOVA QA Report (2026-11-19) — Verdict: **DO NOT MERGE**

---

## Priority 1 — JWT Compatibility (NOVA BACKEND + NOVA SECURITY)

**Issues:**
- `auth.py` only allows `ES256`/`RS256` (asymmetric). Supabase supports legacy/default `HS256`. On an HS256 project, all valid user access returns `401`.
- No configuration or preflight enforcing asymmetric keys.
- Tests mock `_verify_sync` — no real cryptographic JWT validation proven.

**Required:**
1. Audit Supabase project signing-key strategy (NOVA SECURITY).
2. Decide: require asymmetric (ES256/RS256) OR add safe HS256 verification path.
3. Document chosen strategy in `docs/API_SPECIFICATION.md` and `docs/SECURITY.md`.
4. Implement validation for: signature, issuer, audience (`authenticated`), `exp`, `iat`, `sub`, algorithm allowlist.
5. Reject unsupported algorithms securely.
6. Add **real cryptographic JWT tests** (not mock) using test tokens from a configured Supabase project.

**Files:** `backend/app/auth.py`, `backend/tests/test_auth_integration.py` (new), `docs/SECURITY.md` (new)

---

## Priority 2 — Error Handling (NOVA BACKEND)

**Issues:**
- `organizations.py:36` — `response.json()` outside `try/except` → `ValueError` on malformed JSON → unhandled `500`.
- `httpx.Timeout` and `httpx.NetworkError` not explicitly caught.
- Unhandled `500` lacks `X-Request-ID` header (body has `requestId`).
- Internal/credential details must not leak.

**Required:**
1. Wrap `response.json()` in `try/except` → map `ValueError`/`json.JSONDecodeError` to `503`.
2. Catch `httpx.Timeout`, `httpx.NetworkError`, `httpx.HTTPError` explicitly → all map to `503`.
3. Ensure `X-Request-ID` header present on **all** error responses (verify middleware covers exception handler responses).
4. Redact all upstream/internal details in error bodies.

**Files:** `backend/app/organizations.py`, `backend/app/main.py` (verify middleware), `backend/tests/test_error_handling.py` (new/extend)

---

## Priority 3 — API Contract (NOVA BACKEND + NOVA ARCHITECT)

**Issues:**
- Route lacks `response_model` — OpenAPI schema not locked at HTTP boundary.
- Response uses `created_at` (snake_case) but spec requires `camelCase` (`createdAt`).
- Minimal projection is correct (`id`, `name`, `slug`, `created_at`) but field name violates convention.

**Required:**
1. Add `response_model=OrganizationResponse` to route.
2. Change `created_at` → `createdAt` in `OrganizationResponse` and ensure serialization uses camelCase.
3. Update `API_SPECIFICATION.md` with exact response schema.
4. Verify OpenAPI output matches spec.

**Files:** `backend/app/organizations.py`, `backend/app/main.py`, `docs/API_SPECIFICATION.md`

---

## Priority 4 — Tenant Isolation (NOVA SECURITY)

**Issues:**
- Migration exists but not applied to any hosted project.
- No integration test with two users + two orgs proving RLS enforcement.
- Current tests mock `read_organization` → 404, not real RLS/Data API verification.
- Must never use `service_role` key for user-scoped reads.

**Required:**
1. Audit migration `supabase/migrations/20261118210537_initial_organizations_memberships.sql` for correctness.
2. Document RLS policies and required PostgREST behavior.
3. Prepare integration test plan: two users, two orgs, cross-org access attempts.
4. Execute on Supabase dev/staging project (requires user approval + credentials via secure secret management).
5. Prove: owner reads own org → 200; other user reads org → 404; ID substitution → 404; no service-role key used.

**Files:** `supabase/migrations/20261118210537_initial_organizations_memberships.sql`, `backend/tests/test_tenant_isolation_integration.py` (new), `docs/TENANT_ISOLATION_TEST_PLAN.md` (new)

---

## Priority 5 — Test Environment (NOVA QA + NOVA BACKEND)

**Issues:**
- `pytest` collection fails: `ModuleNotFoundError: No module named 'jwt'` (and `pydantic_settings`).
- No reproducible dependency installation path documented.
- Mock tests exist but real integration tests (JWT crypto, RLS, network errors) missing.

**Required:**
1. Document Python dependency installation (uv/pip/poetry) in `LOCAL_DEVELOPMENT.md`.
2. Provide `requirements.txt` or ensure `pyproject.toml` `[project.optional-dependencies] dev` is installable.
3. Run `pytest` to green locally.
4. Add tests for:
   - Valid JWT + allowed org (real crypto if possible)
   - Invalid JWT
   - Expired JWT
   - User without org access
   - Cross-org access
   - Supabase unavailable / timeout / network error
   - Upstream 401/403/5xx
   - Malformed JSON from upstream
5. Clearly separate **mock unit tests** from **real integration tests** (separate files/markers).

**Files:** `backend/pyproject.toml`, `docs/LOCAL_DEVELOPMENT.md`, `backend/tests/test_app.py`, `backend/tests/test_auth_integration.py`, `backend/tests/test_tenant_isolation_integration.py`, `backend/tests/test_error_handling.py`

---

## Coordination Rules (Non-Negotiable)

1. **No cloud project changes / no SQL execution** without explicit user approval.
2. **No merge** until all acceptance criteria met.
3. **No "FIXED" claim** without evidence (test output, logs, screenshots).
4. **NOVA SECURITY** performs independent review.
5. **NOVA QA** runs regression tests after fixes.
6. **Log all changed files and test results** in this document.
7. **Credentials** requested via secure secret management — never pasted in channel.

---

## Final Acceptance Checklist

- [ ] JWT strategy decided, documented, implemented, real-crypto tested
- [ ] All error paths → safe 503, `X-Request-ID` header always present, no leaks
- [ ] `response_model` added, camelCase enforced, OpenAPI matches spec
- [ ] Migration applied to dev/staging, RLS proven with 2 users × 2 orgs
- [ ] `pytest` runs green locally; mock vs integration tests separated
- [ ] All QA findings status: **FIXED** + **VERIFIED**

**Status before all criteria met: NOT READY FOR MERGE**