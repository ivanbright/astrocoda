"""Tests for the optional email opt-in.

The invariant that matters most is that nothing here can stop a scaffold: no
prompt in a non-interactive run, no network on a decline, and no exception
escaping a broken endpoint.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from astrocoda import identify
from astrocoda.identify import (
    Identity,
    collect,
    forget,
    load_identity,
    normalise_email,
    save_identity,
    submit,
    unsubscribe,
)

ENDPOINT = "https://example.test/v1/subscribe"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "identity.json"


class Recorder:
    """Stand-in for :func:`submit` that records instead of hitting the network."""

    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.result = result

    def __call__(self, endpoint: str, email: str, *, version: str = "0", source: str = "", **_: object) -> bool:
        self.calls.append((endpoint, email, source))
        return self.result


def answers(*lines: str):
    queue = list(lines)

    def _input(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return _input


def out() -> io.StringIO:
    return io.StringIO()


# --- storage ---------------------------------------------------------------


def test_load_returns_none_when_never_answered(store: Path) -> None:
    assert load_identity(store) is None


def test_save_then_load_roundtrips(store: Path) -> None:
    save_identity(Identity(email="a@example.com"), store)
    loaded = load_identity(store)
    assert loaded is not None
    assert loaded.email == "a@example.com"
    assert loaded.opted_in is True
    assert loaded.asked_at is not None


def test_save_stamps_asked_at_when_omitted(store: Path) -> None:
    save_identity(Identity(declined=True), store)
    assert load_identity(store).asked_at is not None


def test_load_tolerates_corrupt_file(store: Path) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")
    assert load_identity(store) is None


def test_load_tolerates_non_object_json(store: Path) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_identity(store) is None


def test_forget_removes_and_reports(store: Path) -> None:
    assert forget(store) is False
    save_identity(Identity(email="a@example.com"), store)
    assert forget(store) is True
    assert load_identity(store) is None


# --- validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["user@example.com", "  user@example.com  ", "<user@example.com>", "USER@Example.COM"],
)
def test_normalise_accepts(raw: str) -> None:
    assert normalise_email(raw) == raw.strip().strip("<>").lower()


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-an-email", "a@b", "a@.com", "a b@example.com", "@example.com", "a@@example.com"],
)
def test_normalise_rejects(raw: str) -> None:
    assert normalise_email(raw) is None


def test_normalise_rejects_overlong() -> None:
    assert normalise_email("a" * 250 + "@example.com") is None


# --- submit ----------------------------------------------------------------


def test_submit_never_raises_on_unreachable_host() -> None:
    # Reserved TEST-NET-1 address: connection attempt fails fast.
    assert submit("https://192.0.2.1/v1/subscribe", "a@example.com", timeout=0.5) is False


def test_submit_never_raises_on_empty_endpoint() -> None:
    assert submit("", "a@example.com") is False


def test_submit_sends_expected_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["method"] = request.get_method()
        seen["ua"] = request.headers["User-agent"]
        return FakeResponse()

    monkeypatch.setattr(identify.urllib.request, "urlopen", fake_urlopen)

    assert submit(ENDPOINT, "a@example.com", version="1.2.3", source="cli") is True
    assert seen["url"] == ENDPOINT
    assert seen["method"] == "POST"
    assert seen["body"]["email"] == "a@example.com"
    assert seen["body"]["version"] == "1.2.3"
    assert seen["body"]["source"] == "cli"
    assert "astrocoda/1.2.3" in seen["ua"]


def test_submit_treats_non_2xx_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request, timeout=None):
        raise identify.urllib.error.HTTPError("u", 500, "nope", {}, None)

    monkeypatch.setattr(identify.urllib.request, "urlopen", boom)
    assert submit(ENDPOINT, "a@example.com") is False


def test_unsubscribe_uses_same_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        identify.urllib.request,
        "urlopen",
        lambda request, timeout=None: (seen.update(body=json.loads(request.data)), FakeResponse())[1],
    )
    assert unsubscribe(ENDPOINT, "a@example.com") is True
    assert seen["body"]["email"] == "a@example.com"


# --- collect: the paths that must never surprise a user --------------------


def test_non_interactive_run_never_prompts(store: Path) -> None:
    """A piped/CI run must not block on y/N and must send nothing."""
    recorder = Recorder()
    def explode(_prompt: str) -> str:
        raise AssertionError("prompted in a non-interactive run")

    result = collect(
        endpoint=ENDPOINT,
        interactive=False,
        path=store,
        input_fn=explode,
        out=out(),
        submitter=recorder,
    )

    assert result.email is None
    assert result.declined is False
    assert recorder.calls == []


def test_decline_sends_nothing(store: Path) -> None:
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        interactive=True,
        path=store,
        input_fn=answers("n"),
        out=out(),
        submitter=recorder,
    )

    assert result.declined is True
    assert result.email is None
    assert recorder.calls == []


def test_bare_enter_means_decline(store: Path) -> None:
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        interactive=True,
        path=store,
        input_fn=answers(""),
        out=out(),
        submitter=recorder,
    )
    assert result.declined is True
    assert recorder.calls == []


def test_opt_in_submits_and_normalises(store: Path) -> None:
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        interactive=True,
        path=store,
        input_fn=answers("y", "  User@Example.com "),
        out=out(),
        submitter=recorder,
    )

    assert result.email == "user@example.com"
    assert recorder.calls == [(ENDPOINT, "user@example.com", "cli")]


def test_declined_answer_is_remembered_and_not_reasked(store: Path) -> None:
    first = collect(
        endpoint=ENDPOINT, interactive=True, path=store, input_fn=answers("n"), out=out(), submitter=Recorder()
    )
    save_identity(first, store)

    def explode(_prompt: str) -> str:
        raise AssertionError("asked a second time")

    second = collect(endpoint=ENDPOINT, interactive=True, path=store, input_fn=explode, out=out(), submitter=Recorder())
    assert second.declined is True


def test_malformed_answer_at_prompt_degrades_to_decline(store: Path) -> None:
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        interactive=True,
        path=store,
        input_fn=answers("y", "nonsense"),
        out=out(),
        submitter=recorder,
    )
    assert result.declined is True
    assert recorder.calls == []


def test_email_flag_skips_the_prompt(store: Path) -> None:
    recorder = Recorder()

    def explode(_prompt: str) -> str:
        raise AssertionError("prompted despite --email")

    result = collect(
        endpoint=ENDPOINT,
        email_flag="A@Example.com",
        interactive=True,
        path=store,
        input_fn=explode,
        out=out(),
        submitter=recorder,
    )
    assert result.email == "a@example.com"
    assert len(recorder.calls) == 1


def test_invalid_email_flag_declines_without_sending(store: Path) -> None:
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        email_flag="bogus",
        interactive=True,
        path=store,
        input_fn=answers(""),
        out=out(),
        submitter=recorder,
    )
    assert result.email is None
    assert recorder.calls == []


def test_no_email_flag_suppresses_everything(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTROCODA_EMAIL", raising=False)
    recorder = Recorder()

    def explode(_prompt: str) -> str:
        raise AssertionError("prompted despite --no-email")

    result = collect(
        endpoint=ENDPOINT,
        assume_no=True,
        interactive=True,
        path=store,
        input_fn=explode,
        out=out(),
        submitter=recorder,
    )
    assert result.declined is True
    assert recorder.calls == []


def test_env_opt_out_suppresses_the_prompt(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTROCODA_NO_EMAIL", "1")
    monkeypatch.delenv("ASTROCODA_EMAIL", raising=False)
    recorder = Recorder()
    result = collect(endpoint=ENDPOINT, interactive=True, path=store, out=out(), submitter=recorder)
    assert result.declined is True
    assert recorder.calls == []


def test_env_opt_out_beats_email_flag(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTROCODA_NO_EMAIL", "1")
    recorder = Recorder()
    result = collect(
        endpoint=ENDPOINT,
        email_flag="a@example.com",
        assume_no=False,
        interactive=True,
        path=store,
        out=out(),
        submitter=recorder,
    )
    assert result.email is None
    assert recorder.calls == []


def test_env_supplies_email_without_prompting(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTROCODA_EMAIL", "env@example.com")
    monkeypatch.delenv("ASTROCODA_NO_EMAIL", raising=False)
    recorder = Recorder()

    def explode(_prompt: str) -> str:
        raise AssertionError("prompted despite ASTROCODA_EMAIL")

    result = collect(endpoint=ENDPOINT, interactive=True, path=store, input_fn=explode, out=out(), submitter=recorder)
    assert result.email == "env@example.com"
    assert len(recorder.calls) == 1


def test_endpoint_failure_still_records_the_choice(store: Path) -> None:
    result = collect(
        endpoint=ENDPOINT,
        interactive=True,
        path=store,
        input_fn=answers("y", "a@example.com"),
        out=out(),
        submitter=Recorder(result=False),
    )
    assert result.email == "a@example.com"


def test_interrupt_at_prompt_degrades_to_decline(store: Path) -> None:
    def interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    result = collect(
        endpoint=ENDPOINT, interactive=True, path=store, input_fn=interrupt, out=out(), submitter=Recorder()
    )
    assert result.declined is True


def test_unexpected_exporter_error_is_still_caught(store: Path) -> None:
    def weird(_prompt: str) -> str:
        raise RuntimeError("terminal exploded")

    result = collect(
        endpoint=ENDPOINT, interactive=True, path=store, input_fn=weird, out=out(), submitter=Recorder()
    )
    assert result.declined is True


# --- TTY detection ---------------------------------------------------------


def test_is_interactive_false_when_redirected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert identify.is_interactive() is False


def test_is_interactive_handles_objects_without_isatty(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bare:
        pass

    monkeypatch.setattr(sys, "stdin", Bare())
    assert identify.is_interactive() is False
