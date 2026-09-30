"""Auth and trigger behaviour for ``POST /api/v1/pipelines/trigger``."""

from __future__ import annotations

from app.database.db import PipelineRunStatus
from app.api.v1.pipelines import PIPELINE_TASK_NAME, QUEUE_NAME


def test_trigger_without_api_key_is_401(client):
    r = client.post("/api/v1/pipelines/trigger", json={"text": "hello"})
    assert r.status_code == 401
    assert "detail" in r.json()


def test_trigger_with_empty_text_is_422(client, set_current_user):
    user = set_current_user()
    r = client.post(
        "/api/v1/pipelines/trigger",
        json={"text": ""},
        headers={"X-API-Key": user.api_key},
    )
    assert r.status_code == 422


def test_trigger_with_inactive_user_is_403(client, set_current_user):
    user = set_current_user(active=False)
    r = client.post(
        "/api/v1/pipelines/trigger",
        json={"text": "nope"},
        headers={"X-API-Key": user.api_key},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Inactive subscription. Please activate billing."


def test_trigger_with_active_user_queues_job(client, set_current_user, arq_pool, fake_session):
    user = set_current_user()
    text = "Astrocoda turns slow LLM work into a background job."
    r = client.post(
        "/api/v1/pipelines/trigger",
        json={"text": text},
        headers={"X-API-Key": user.api_key},
    )
    assert r.status_code == 202, r.text

    body = r.json()
    assert body["job_id"]
    assert body["status"] == PipelineRunStatus.QUEUED

    assert len(arq_pool.calls) == 1
    args, kwargs = arq_pool.calls[0]
    assert args[0] == PIPELINE_TASK_NAME
    assert args[1] == str(user.id)
    assert args[2] == text
    assert args[3] == body["job_id"]
    assert kwargs["_job_id"] == body["job_id"]
    assert kwargs["_queue_name"] == QUEUE_NAME

    assert len(fake_session.added) == 1
    run = fake_session.added[0]
    assert run.job_id == body["job_id"]
    assert run.user_id == user.id
    assert run.status == PipelineRunStatus.QUEUED
    assert run.total_characters == len(text)


def test_trigger_when_queue_rejects_duplicate_is_409(client, set_current_user, arq_pool):
    user = set_current_user()
    arq_pool.available = False
    r = client.post(
        "/api/v1/pipelines/trigger",
        json={"text": "dup"},
        headers={"X-API-Key": user.api_key},
    )
    assert r.status_code == 409


def test_trigger_response_shape(client, set_current_user, fake_session):
    user = set_current_user()
    r = client.post(
        "/api/v1/pipelines/trigger",
        json={"text": "hello"},
        headers={"X-API-Key": user.api_key},
    )
    body = r.json()
    assert set(body) == {"job_id", "status", "accepted_at", "message"}