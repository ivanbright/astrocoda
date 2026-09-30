"""JWT access token issuance on ``POST /api/v1/pipelines/token``."""

from __future__ import annotations

import pytest

from app.api.v1.auth import create_access_token, decode_access_token


def test_token_endpoint_returns_jwt(client, set_current_user):
    user = set_current_user()
    r = client.post("/api/v1/pipelines/token", headers={"X-API-Key": user.api_key})
    assert r.status_code == 200, r.text
    payload = r.json()
    token = payload["access_token"]
    assert token.count(".") == 2
    assert payload["token_type"] == "Bearer"
    assert payload["expires_in"] == 3600


def test_token_claims_are_correct(client, set_current_user):
    user = set_current_user()
    r = client.post("/api/v1/pipelines/token", headers={"X-API-Key": user.api_key})
    claims = decode_access_token(r.json()["access_token"])
    assert claims["sub"] == str(user.id)
    assert claims["typ"] == "access"


def test_tokens_are_unique_per_call(client, set_current_user):
    user = set_current_user()
    headers = {"X-API-Key": user.api_key}
    first = client.post("/api/v1/pipelines/token", headers=headers).json()["access_token"]
    second = client.post("/api/v1/pipelines/token", headers=headers).json()["access_token"]
    assert first != second


def test_create_access_token_returns_expected_lifespan(set_current_user):
    user = set_current_user()
    _, expires_in = create_access_token(user)
    assert expires_in == 3600