"""Stripe signature validation on ``POST /api/v1/webhooks/stripe``."""

from __future__ import annotations


def test_missing_signature_is_400(client):
    r = client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_bad_signature_is_400(client):
    r = client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"id":"evt_1","type":"checkout.session.completed"}',
        headers={"Content-Type": "application/json", "stripe-signature": "t=1,v1=deadbeef"},
    )
    assert r.status_code == 400


def test_bad_signature_does_not_echo_payload(client):
    r = client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"id":"evt_1","type":"checkout.session.completed"}',
        headers={"Content-Type": "application/json", "stripe-signature": "t=1,v1=deadbeef"},
    )
    assert "checkout.session.completed" not in r.text