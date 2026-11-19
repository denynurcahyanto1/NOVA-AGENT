# Tenant Isolation Integration Test Plan

## Objective

Verify that the `GET /api/v1/organizations/{id}` endpoint enforces tenant isolation through:
1. User permissions based on organization membership
2. Non-enumerating responses for unauthorized access
3. Prevention of organization ID substitution attacks
4. Protection against service-role key usage for user-scoped reads

## Test Environment

- **Scope**: Supabase development/staging project
- **Users**: Two distinct users (User A, User B)
- **Organizations**: Two distinct organizations (Org A, Org B)
- **Test Data**: Pre-populated organization memberships ensuring User A → Org A, User B → Org B

## Test Cases

### 1. Valid Access (200 OK)
- **Description**: User accesses their permitted organization
- **Steps**:
  1. User A authenticates with valid JWT containing `sub=user-a`
  2. User A requests Org A (`ORG_A_ID`)
  3. Verify response includes organization details
  4. Verify response contains `id`, `name`, `slug`, `createdAt` fields
- **Expected**: HTTP 200, organization data returned

### 2. Cross-Organization Access (404)
- **Description**: User attempts to access organization they don't belong to
- **Steps**:
  1. User A authenticates with valid JWT containing `sub=user-a`
  2. User A requests Org B (`ORG_B_ID`)
  3. Verify response returns 404 (not 403)
  4. Verify error message is "Organization not found"
- **Expected**: HTTP 404, non-enumerating response to prevent reconnaissance

### 3. ID Substitution Attack (404)
- **Description**: User attempts to access organization using another user's ID
- **Steps**:
  1. User A authenticates with valid JWT containing `sub=user-a`
  2. User A requests Org A ID that belongs to User B (`ORG_B_ID`)
  3. Verify response returns 404
- **Expected**: HTTP 404, non-enumerating response

### 4. Service-Role Key Protection
- **Description**: Ensure backend never uses service-role keys for user-scoped reads
- **Steps**:
  1. Monitor HTTP headers during user-scoped organization read
  2. Verify `apikey` header contains publishable key, not service-role key
  3. Verify no `service_role` string appears in headers
- **Expected**: No service-role key usage detected

### 5. Unauthenticated Access (401)
- **Description**: Missing or invalid token rejected before database access
- **Steps**:
  1. Request without Authorization header
  2. Request with malformed Authorization header
  3. Verify 401 Unauthorized response
- **Expected**: HTTP 401, "Authentication required" error

## Test Implementation

- **Tools**: FastAPI TestClient, MockRLSSupabaseClient, Test JWT generators
- **Isolation**: Each test uses dedicated authenticated user context
- **Verification**: Direct assertions on HTTP status codes and response content
- **Non-enumeration**: Confirm 404 responses do not leak organization existence

## Success Criteria

- All test cases pass without failure
- No service-role key usage detected in user-scoped reads
- Cross-tenant access returns 404 (not 403) to prevent enumeration
- Organization data includes required fields with correct casing (`createdAt` camelCase)
- JWT validation covers all required claims with proper rejection of invalid tokens