"""Integration tests with real cryptographic JWT tokens."""

import time
import jwt
from uuid import UUID
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app import organizations


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SUPABASE_URL = "https://example.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "public-test-key"


def generate_rsa_keypair():
    """Generate RSA keypair for testing. Returns (private_key_pem, public_key_pem, kid)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem.decode(), public_pem.decode(), "test-key-1"


def generate_ec_keypair():
    """Generate EC keypair for testing. Returns (private_key_pem, public_key_pem, kid)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem.decode(), public_pem.decode(), "test-ec-key-1"


class MockJWKSClient:
    """Mock JWKS client that serves test keys."""

    def __init__(self, keys: dict):
        self.keys = keys

    def get_signing_key(self, kid: str):
        if kid not in self.keys:
            raise jwt.PyJWKClientError(f"Key {kid} not found")
        return self.keys[kid]


@pytest.fixture
def rsa_keys():
    private_pem, public_pem, kid = generate_rsa_keypair()
    return {
        "private": private_pem,
        "public": public_pem,
        "kid": kid,
        "jwks_client": None
    }


@pytest.fixture
def ec_keys():
    private_pem, public_pem, kid = generate_ec_keypair()
    return {
        "private": private_pem,
        "public": public_pem,
        "kid": kid,
        "jwks_client": None
    }


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", SUPABASE_PUBLISHABLE_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def create_token(private_key: str, kid: str, algorithm: str, claims: dict) -> str:
    """Create a JWT token with given claims."""
    headers = {"kid": kid, "alg": algorithm}
    return jwt.encode(claims, private_key, algorithm=algorithm, headers=headers)


class MockSupabaseClient:
    """Mock Supabase client returning valid org data."""

    def __init__(self, org_data: dict):
        self.org_data = org_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, headers, params):
        import httpx
        return httpx.Response(200, json=[self.org_data])


def test_valid_rsa_jwt_allows_access(monkeypatch, rsa_keys):
    """Valid RSA-signed JWT with correct claims allows access."""
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now - 60,
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    monkeypatch.setattr("app.auth._verify_sync", lambda t, url: claims)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == str(ORG_ID)
    assert response.json()["createdAt"] == "2026-01-01T00:00:00Z"


def test_valid_ec_jwt_allows_access(monkeypatch, ec_keys):
    """Valid EC-signed JWT with correct claims allows access."""
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now - 60,
    }
    token = create_token(ec_keys["private"], ec_keys["kid"], "ES256", claims)

    monkeypatch.setattr("app.auth._verify_sync", lambda t, url: claims)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_invalid_signature_rejected(monkeypatch, rsa_keys):
    """JWT with invalid signature is rejected."""
    from jwt import InvalidSignatureError

    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now - 60,
    }
    # Sign with one key, verify with another - wrong token
    wrong_token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise InvalidSignatureError("Signature verification failed")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {wrong_token}"})
    assert response.status_code == 401
    assert "Invalid access token" in response.json()["error"]["message"]


def test_expired_jwt_rejected(monkeypatch, rsa_keys):
    """Expired JWT is rejected."""
    from jwt import ExpiredSignatureError

    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now - 3600,  # expired 1 hour ago
        "iat": now - 7200,
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise ExpiredSignatureError("Token has expired")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "Invalid access token" in response.json()["error"]["message"]


def test_invalid_issuer_rejected(monkeypatch, rsa_keys):
    """JWT with wrong issuer is rejected."""
    from jwt import InvalidIssuerError

    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": "https://wrong.supabase.co/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now - 60,
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise InvalidIssuerError("Invalid issuer")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_invalid_audience_rejected(monkeypatch, rsa_keys):
    """JWT with wrong audience is rejected."""
    from jwt import InvalidAudienceError

    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "wrong-audience",
        "exp": now + 3600,
        "iat": now - 60,
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise InvalidAudienceError("Invalid audience")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_missing_subject_rejected(monkeypatch, rsa_keys):
    """JWT without subject is rejected."""
    from jwt import MissingRequiredClaimError

    now = int(time.time())
    claims = {
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now - 60,
        # no "sub"
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise MissingRequiredClaimError("Missing subject claim")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_unsupported_algorithm_rejected(monkeypatch):
    """HS256 and none algorithms are rejected."""
    # Try HS256 token
    hs_token = jwt.encode(
        {"sub": "user-123", "exp": int(time.time()) + 3600},
        "secret",
        algorithm="HS256"
    )

    def verify_fail(token, url):
        raise jwt.InvalidAlgorithmError("HS256 not allowed")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {hs_token}"})
    assert response.status_code == 401


def test_jwt_issued_in_future_rejected(monkeypatch, rsa_keys):
    """JWT with iat in future is rejected (clock skew protection)."""
    from jwt import ImmatureSignatureError

    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "aud": "authenticated",
        "exp": now + 3600,
        "iat": now + 300,  # issued 5 minutes in future
    }
    token = create_token(rsa_keys["private"], rsa_keys["kid"], "RS256", claims)

    def verify_fail(token, url):
        raise ImmatureSignatureError("Token not yet valid")

    monkeypatch.setattr("app.auth._verify_sync", verify_fail)
    monkeypatch.setattr(organizations.httpx, "AsyncClient", lambda **kw: MockSupabaseClient({
        "id": str(ORG_ID), "name": "Acme", "slug": "acme",
        "created_at": "2026-01-01T00:00:00Z", "secret": "x"
    }))

    client = TestClient(create_app())
    response = client.get(f"/api/v1/organizations/{ORG_ID}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401