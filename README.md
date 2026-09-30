<div align="center">

# ⚡ Astrocoda

**Production-ready Python backend boilerplate for AI data pipelines.**

`FastAPI` · `ARQ` · `SQLModel/PostgreSQL` · `Qdrant` · `OpenAI + Instructor` · `Stripe`

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker--compose-ready-2496ED.svg)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/ivanbright/astrocoda/actions/workflows/ci.yml/badge.svg)](https://github.com/ivanbright/astrocoda/actions/workflows/ci.yml)

</div>

---

## The problem

Every AI product starts with the same four days of work that nobody bills for:

1. An HTTP endpoint that must not time out while a slow model is thinking.
2. A background worker with retries, timeouts and graceful shutdown.
3. A database layer for structured results next to a vector store for embeddings.
4. Billing that actually gates access to the product.

Astrocoda is that infrastructure, written once, typed end to end, and shipped with
a `docker compose up` that starts all five services.

## What you get

| | |
|---|---|
| **Async everywhere** | No `requests`, no blocking I/O, no thread pool. `asyncpg`, `httpx`, `redis.asyncio`, `AsyncQdrantClient`. |
| **Typed** | Every function, route and model is annotated. Pydantic v2 + SQLModel. |
| **Instant responses** | `POST /pipelines/trigger` returns `202 Accepted` in ~1 ms. The model never runs in the request. |
| **Replay safe** | Jobs resume at the first missing chunk after a failure, and a `(run_id, chunk_index)` unique index makes duplicates impossible. |
| **Guaranteed JSON** | Instructor forces the LLM to satisfy a Pydantic model and self-heals bad output. |
| **Billing built in** | Stripe webhooks provision users, mint API keys and gate pipeline access. |
| **One command** | `docker compose up --build` starts PostgreSQL, Redis, Qdrant, the API and the worker. |

---

## Distribution and the verified release channel

This repository is **MIT licensed and complete**. Clone it, `docker compose up`,
and you have the whole thing. Nothing is held back, there is no key to obtain,
and no feature is crippled in the open version.

If you would rather not assemble it yourself, the **verified release channel**
is a convenience on top of the same code:

- a signed, reproducible `astrocoda-0.1.0.zip` with an `astrocoda.manifest.json`
  that `init` verifies before it writes a single file;
- a licence key and a one-command `astrocoda init` / `up` path, so you skip the
  unzip-install-login sequence;
- versioned updates, and a commercial licence for the same code in an
  organisation that needs one.

The signing key for that channel is not in this repository and cannot be
reconstructed from it. That is the only part of the product that is not
reproducible by reading the source — which is exactly why it is the part worth
paying for. The MIT licence above still governs the code either way.

### How the CLI verifies things offline

`cli/` is a small, independently useful tool: it authenticates a template
against a signed manifest, and verifies licences locally. It is MIT licensed,
stands alone, and contains none of the product.

```
buyer:  pip install ./cli
buyer:  astrocoda login <signed-key-string>   # verify signature locally, store
buyer:  astrocoda init <name>                 # verify template vs signed manifest, scaffold
buyer:  astrocoda up                          # re-verify locally, docker compose up
```

The key is `astrocoda_<base64url(payload)>.<base64url(signature)>`; the payload
carries `email`, `plan` and `exp`, and the signature binds them. The CLI holds
only the **public** half of the keypair.

`init` is a supply-chain check rather than a copy job: the template must ship an
`astrocoda.manifest.json` (path → sha256) signed with the matching private key.
The CLI verifies that signature, re-hashes every listed file, and copies only
the signed files — so a tampered, re-signed, or manifest-free template is
refused before anything is written.

Contract, commands and environment: [`cli/README.md`](cli/README.md).

---

## Architecture

```
                        ┌──────────────────────────────────────────┐
   POST /trigger        │  web  (uvicorn)                          │
   X-API-Key  ─────────▶│  1. verify API key      → 401            │
                        │  2. check is_active     → 403            │
                        │  3. write pipeline_runs row              │
                        │  4. enqueue on Redis                     │
                        │  5. 202 { job_id }   ◀── millisecond     │
                        └───────────────┬──────────────────────────┘
                                        │ ARQ
                                        ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  worker  (arq app.workers.tasks.WorkerSettings)                       │
   │                                                                       │
   │   chunk (1000 chars) ─▶ embed ─▶ extract ─▶ persist ─▶ commit         │
   │        │                │         │            │           │          │
   │        │                │         │            │           └─▶ PostgreSQL│
   │        │                │         │            └──────────────▶ Qdrant │
   │        │                │         └── text-embedding-3-small + gpt-4o  │
   │        │                └──────────── text-embedding-3-small (1536)   │
   │        └───────────────── OpenAI embeddings                           │
   └───────────────────────────────────────────────────────────────────────┘
                                        ▲
   POST /webhooks/stripe ────────────────┘  checkout.session.completed → is_active = true
                                           customer.subscription.deleted → is_active = false
```

The API and the worker share `app/core`, `app/database` and `app/workers/schemas`.
The API never imports the worker module, so a slow or broken OpenAI call can never
affect request latency.

---

## Quickstart

### 1. Prepare the environment

```bash
git clone https://github.com/ivanbright/astrocoda.git astrocoda
cd astrocoda
python bootstrap.py
```

`bootstrap.py` copies `.env.example` to `.env`, generates a cryptographically
random `SECRET_KEY`, and prints anything left to fill in. Then edit `.env` and set:

```dotenv
OPENAI_API_KEY=sk-...
STRIPE_WEBHOOK_SECRET=whsec_...
```

Verify the configuration at any time:

```bash
python bootstrap.py --check
```

<details>
<summary>Prefer plain shell commands?</summary>

```bash
cp .env.example .env      # macOS / Linux
copy .env.example .env    # Windows PowerShell
# then edit .env and set OPENAI_API_KEY and STRIPE_WEBHOOK_SECRET
```

</details>

### 2. Start the stack

```bash
docker compose up --build
```

<details>
<summary>macOS / Linux users</summary>

```bash
make setup    # same as python bootstrap.py
make up       # same as docker compose up --build -d
make logs     # tail every container
```

</details>

Five containers come up:

| Service    | Image                | Ports            | Role                            |
|------------|----------------------|------------------|---------------------------------|
| `web`      | built from `Dockerfile` | `8000:8000`    | FastAPI application             |
| `worker`   | built from `Dockerfile` | –              | ARQ background worker           |
| `postgres` | `postgres:15-alpine` | `5432:5432`      | Users, jobs, structured results |
| `redis`    | `redis:7-alpine`     | `6379:6379`      | ARQ queue                       |
| `qdrant`   | `qdrant/qdrant`      | `6333` / `6334`  | 1536-dim cosine vectors         |

### 3. Confirm it works

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "dependencies": { "postgres": "up", "redis": "up", "qdrant": "up" },
  "version": "1.0.0"
}
```

Interactive API docs are at **http://localhost:8000/docs**.

---

## Try the pipeline

New users are created by Stripe. To exercise the pipeline without Stripe, insert
one directly:

```sql
INSERT INTO users (email, api_key, is_active)
VALUES (
  'you@example.com',
  'astro_dev_local_key_0123456789abcdef0123456789abcdef',
  true
)
RETURNING id;
```

Trigger a run:

```bash
curl -X POST http://localhost:8000/api/v1/pipelines/trigger \
  -H "X-API-Key: astro_dev_local_key_0123456789abcdef0123456789abcdef" \
  -H "Content-Type: application/json" \
  -d '{"text":"Astrocoda turns slow LLM work into a background job. It chunks text, embeds each chunk, extracts typed JSON and stores vectors in Qdrant."}'
```

```json
{
  "job_id": "3f1c9b2e-6a54-4f0e-9c2b-0d5f7e1a9c33",
  "status": "queued",
  "accepted_at": "2026-01-14T09:12:44.120Z",
  "message": "Job accepted and queued for background processing."
}
```

Watch it finish:

```bash
curl -H "X-API-Key: astro_dev_local_key_0123456789abcdef0123456789abcdef" \
  http://localhost:8000/api/v1/pipelines/jobs/3f1c9b2e-6a54-4f0e-9c2b-0d5f7e1a9c33
```

```json
{
  "job_id": "3f1c9b2e-6a54-4f0e-9c2b-0d5f7e1a9c33",
  "status": "completed",
  "chunk_count": 1,
  "processed_chunks": 1,
  "persisted_chunks": 1,
  "total_characters": 156,
  "duration_ms": 2140,
  "error_message": null
}
```

The extracted summary, sentiment and keywords now live in `pipeline_chunks`, and
the 1536-dimension vector is in the `astrocoda_chunks` Qdrant collection:

```bash
curl http://localhost:6333/collections/astrocoda_chunks/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit":1,"with_payload":true,"with_vector":false}'
```

---

## Running the tests

The suite is fully hermetic — no Postgres, Redis, Qdrant or outbound AI calls.
Real external services are replaced with in-memory fakes, so it runs anywhere:

```bash
pip install -r requirements-dev.txt
make test            # or: python -m pytest
```

Coverage: authentication and `trigger` authz/validation, Stripe signature
rejection, JWT issuance, `chunk_text` splitting, the `ExtractedInsight`
schema contract, and the vector dimension guard.

---

## API reference

All pipeline routes require the `X-API-Key` header. Routes accept an optional
`Authorization: Bearer <jwt>` minted by `POST /pipelines/token`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/pipelines/trigger` | `X-API-Key` | Queue a document. `202` + `job_id`. |
| `GET` | `/api/v1/pipelines/jobs/{job_id}` | `X-API-Key` | Execution log for one job. |
| `GET` | `/api/v1/pipelines/jobs?limit=20&offset=0` | `X-API-Key` | Paginated job history. |
| `POST` | `/api/v1/pipelines/token` | `X-API-Key` | Exchange the key for a short lived JWT. |
| `POST` | `/api/v1/webhooks/stripe` | Stripe signature | Subscription lifecycle events. |
| `GET` | `/health` | — | PostgreSQL / Redis / Qdrant health. |
| `GET` | `/` | — | Service metadata. |

### Error contract

| Status | When |
|---|---|
| `400` | Stripe signature missing or invalid. |
| `401` | `X-API-Key` missing, unknown, or a bad/expired JWT. |
| `403` | `Inactive subscription. Please activate billing.` |
| `404` | Job does not exist for the authenticated account. |
| `409` | A job with that id already exists. |
| `422` | Payload failed schema validation. |
| `503` | Redis or a dependency is unreachable. |
| `500` | Unhandled error. Details are logged, never returned. |

---

## Stripe setup

### 1. Create the webhook endpoint

Point Stripe at your deployment:

```
https://your-domain.com/api/v1/webhooks/stripe
```

Subscribe to:

- `checkout.session.completed` — provisions the user, sets `is_active = true`
- `customer.subscription.updated` — keeps access in sync with the subscription state
- `customer.subscription.deleted` — sets `is_active = false`

Set the returned `whsec_...` value as `STRIPE_WEBHOOK_SECRET`. Verification uses
`stripe.Webhook.construct_event` against the **raw** request body, so a forged
payload is rejected with `400` before any database work happens.

### 2. Local development

```bash
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

Copy the printed `whsec_...` into `.env` and restart the stack.

### 3. Pass a pre-provisioned API key

If you create the checkout session with `client_reference_id`, Astrocoda uses that
value as the account's API key instead of minting one — useful when your frontend
generates the key before payment:

```python
import stripe

session = stripe.checkout.Session.create(
    mode="subscription",
    line_items=[{"price": "price_...", "quantity": 1}],
    client_reference_id=f"astro_{secrets.token_urlsafe(32)}",
    success_url="https://your-app.com/welcome",
    cancel_url="https://your-app.com/pricing",
)
```

### 4. Completing a checkout by hand

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/stripe \
  -H "Content-Type: application/json" \
  -H "stripe-signature: t=...,v1=..." \
  -d @checkout-completed.json
```

The response reports the outcome without ever echoing the API key:

```json
{
  "received": true,
  "event_type": "checkout.session.completed",
  "event_id": "evt_1P...",
  "status": "processed",
  "user_id": "6c2f...",
  "email": "you@example.com",
  "is_active": true
}
```

The new account's `api_key` is in the `users` table.

---

## How the pipeline works

`process_ai_pipeline_task` in `app/workers/tasks.py`:

1. **Chunk** — split the document into ~1000 character windows, cut at whitespace.
2. **Embed** — `text-embedding-3-small` over a sliding window of 16 chunks per
   request, so the HTTP round trip is amortised without loading every vector at once.
3. **Extract** — Instructor asks `gpt-4o-mini` for an `ExtractedInsight`
   (`summary`, `sentiment`, `keywords`) and retries up to 3 times until the
   response validates.
4. **Persist** — upsert the vector into Qdrant with a filterable payload, insert
   the structured record into `pipeline_chunks`, then commit.
5. **Repeat** — each chunk is committed as soon as it is durable, and
   `pipeline_runs.processed_chunks` advances. A crash mid-document resumes at the
   first chunk that is missing, and already-embedded chunks are never re-billed.

Tune it from the environment: `CHUNK_SIZE`, `EMBEDDING_BATCH_SIZE`,
`OPENAI_CHAT_MODEL`, `OPENAI_EMBEDDING_MODEL`, `INSTRUCTOR_MAX_RETRIES`,
`ARQ_MAX_TRIES`, `ARQ_JOB_TIMEOUT`.

> **Changing models?** `EMBEDDING_DIMENSIONS` must match the model. Switching to
> `text-embedding-3-large` means `3072` **and** a new `VECTOR_COLLECTION_NAME`,
> because Qdrant collections have a fixed vector width.

### Any OpenAI-compatible provider

The worker talks to whatever provider `OPENAI_BASE_URL` points at — unset means
OpenAI itself; set it to `https://openrouter.ai/api/v1` (or DeepSeek, Together,
Groq, ...) to route both embeddings **and** structured extraction through that
gateway. Swap all three together:

```dotenv
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
OPENAI_CHAT_MODEL=openai/gpt-4o-mini
OPENAI_EMBEDDING_MODEL=openai/text-embedding-3-small
```

`OPENAI_API_KEY` is always the key for the provider selected by the base URL.

### Cost awareness

At `gpt-4o-mini` and `text-embedding-3-small`, a 100 KB document (~100 chunks) costs
roughly $0.01. Embeddings dominate; the extraction pass is usually cheaper than the
embedding pass. `CHUNK_SIZE` is the single biggest lever on both cost and quality.

---

## Configuration

Every variable is validated on boot. A missing or malformed value stops the process
with an explicit message instead of failing later at request time.

### Required

| Variable | Example | Description |
|---|---|---|
| `POSTGRES_URI` | `postgresql+asyncpg://astrocoda:pw@postgres:5432/astrocoda` | Async SQLAlchemy DSN. `asyncpg` driver required. |
| `REDIS_URI` | `redis://redis:6379` | ARQ queue DSN. |
| `OPENAI_API_KEY` | `sk-...` | Chat and embedding access. Used for the base provider when `OPENAI_BASE_URL` is unset. |
| `QDRANT_URL` | `http://qdrant:6333` | Vector database base URL. |
| `QDRANT_API_KEY` | *(empty)* | Sent as `Qdrant-Api-Key`. Leave empty for a local instance. |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | Stripe endpoint signing secret. |
| `SECRET_KEY` | 48 random bytes | JWT signing key. `python bootstrap.py --rotate-secret` to change. |

### Optional

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `local` | `local` \| `staging` \| `production`. |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL`. |
| `DEBUG` | `false` | Echo SQL statements. Never enable in production. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma separated, or a JSON array. |
| `API_V1_PREFIX` | `/api/v1` | Mount point for the versioned router. |
| `PROJECT_NAME` | `Astrocoda` | Title shown in the OpenAPI docs. |
| `OPENAI_BASE_URL` | *(unset → `api.openai.com`)* | OpenAI-compatible gateway. Set to `https://openrouter.ai/api/v1`, DeepSeek, Together, etc. |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Extraction model. With OpenRouter use a provider-prefixed id like `openai/gpt-4o-mini`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model. With OpenRouter use `openai/text-embedding-3-small` or `qwen/qwen3-embedding-0.6b`. |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the embedding model. |
| `CHUNK_SIZE` | `1000` | Characters per chunk. |
| `MAX_DOCUMENT_CHARACTERS` | `1000000` | Hard ceiling on the submitted text. |
| `EMBEDDING_BATCH_SIZE` | `16` | Chunks per embedding request. |
| `INSTRUCTOR_MAX_RETRIES` | `3` | Retries when the LLM returns invalid JSON. |
| `LLM_MAX_TOKENS` | `512` | Max tokens per extraction call; keeps cost (and credit limits) predictable. |
| `VECTOR_COLLECTION_NAME` | `astrocoda_chunks` | Qdrant collection name. |
| `ARQ_MAX_TRIES` | `3` | Attempts before the job is failed. |
| `ARQ_JOB_TIMEOUT` | `1800` | Seconds before ARQ cancels a stuck job. |
| `JWT_ALGORITHM` | `HS256` | Access token signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime. |
| `API_KEY_PREFIX` | `astro_` | Prefix for generated API keys. |
| `WEB_PORT` | `8000` | Host port for the API container. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `astrocoda` / … / `astrocoda` | Local database credentials. |

> Inside Docker, `POSTGRES_URI`, `REDIS_URI` and `QDRANT_URL` are rewritten by
> `docker-compose.yml` to the container network. Your `.env` values apply when you
> run the app directly with a virtualenv.

---

## Running without Docker

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Start the infrastructure only, then run the processes yourself:

```bash
docker compose up -d postgres redis qdrant
```

Point `.env` at `localhost` and launch both processes in separate terminals:

```bash
# terminal 1 - API
uvicorn app.main:app --reload --port 8000

# terminal 2 - worker
arq app.workers.tasks.WorkerSettings
```

Edit `.env` so the DSNs use `localhost` instead of the service names:

```dotenv
POSTGRES_URI=postgresql+asyncpg://astrocoda:astrocoda_dev_password@localhost:5432/astrocoda
REDIS_URI=redis://localhost:6379
QDRANT_URL=http://localhost:6333
```

---

## Data model

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | Primary key. |
| `email` | `str` | Unique, indexed. |
| `stripe_customer_id` | `str?` | Unique per Stripe customer, indexed. |
| `is_active` | `bool` | Gates pipeline access. `false` → `403`. |
| `api_key` | `str` | Unique, indexed. Sent as `X-API-Key`. |
| `created_at` | `datetime` | UTC, indexed. |

### `pipeline_runs` — execution log

`id`, `job_id` (unique), `user_id` (FK), `status` (`queued` → `processing` →
`completed` / `failed`), `chunk_count`, `processed_chunks`, `total_characters`,
`duration_ms`, `error_message`, `created_at`, `started_at`, `completed_at`.

### `pipeline_chunks` — structured results

`id`, `run_id` (FK), `user_id` (FK), `chunk_index`, `vector_point_id`, `summary`,
`sentiment`, `keywords` (JSON array), `created_at`. Unique on `(run_id, chunk_index)`.

---

## Project layout

```
astrocoda/
├── app/
│   ├── main.py                     FastAPI app, lifespan, CORS, health
│   ├── api/v1/
│   │   ├── auth.py                 X-API-Key + JWT dependencies
│   │   ├── pipelines.py            Trigger, job status, token exchange
│   │   └── webhooks.py             Stripe signature verification + state sync
│   ├── core/config.py              Pydantic BaseSettings, validated env
│   ├── database/
│   │   ├── db.py                   SQLModel engine, models, session dependency
│   │   └── vector.py               Async Qdrant lifecycle, upsert helper
│   └── workers/
│       ├── tasks.py                ARQ WorkerSettings + process_ai_pipeline_task
│       └── schemas.py              ExtractedInsight, the LLM contract
├── bootstrap.py                    One command environment setup
├── cli/                            The `astrocoda` CLI (signed-key license + scaffold)
│   ├── astrocoda/                  package: login/init/up + Ed25519 verification
│   └── pyproject.toml              console script `astrocoda`
├── docker-compose.yml              postgres, redis, qdrant, web, worker
├── Dockerfile                      Single image, two roles
├── Makefile                        Convenience targets
├── requirements.txt
├── requirements-dev.txt            Shell + pytest for the test suite
├── pyproject.toml                  pytest configuration
├── tests/                          Hermetic pytest suite (see "Running the tests")
├── .env.example
└── README.md
```

---

## Operations

```bash
docker compose ps                     # container status
docker compose logs -f worker         # tail the worker
docker compose logs -f web            # tail the API
docker compose exec web bash          # shell inside the API container
docker compose up -d --scale worker=4 # scale horizontally, same queue
docker compose down                   # stop, keep data
docker compose down -v                # stop and delete volumes
```

Scaling is horizontal by default: extra workers join the same ARQ queue and ARQ
guarantees a job is executed by exactly one worker.

### Production checklist

- [ ] Rotate `SECRET_KEY` and `POSTGRES_PASSWORD` out of `.env.example` defaults.
- [ ] Use a managed PostgreSQL, Redis and Qdrant, or restrict port exposure.
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`.
- [ ] Restrict `CORS_ORIGINS` to your real dashboard origin.
- [ ] Terminate TLS in front of the API and keep `stripe-signature` intact.
- [ ] Set `ARQ_JOB_TIMEOUT` above your slowest expected document.
- [ ] Alert on `pipeline_runs.status = 'failed'` and on `/health` returning `503`.
- [ ] Back up PostgreSQL (`users`, `pipeline_runs`, `pipeline_chunks`) and the Qdrant volume.

---

## FAQ

**Does the trigger endpoint wait for OpenAI?**
No. It validates, writes a row, enqueues and returns `202`. Only the worker talks to OpenAI.

**What happens if OpenAI returns malformed JSON?**
Instructor retries up to `INSTRUCTOR_MAX_RETRIES` times, then the attempt fails,
the run is marked `failed` with the error message, and ARQ retries the job.

**Is a replayed job safe?**
Yes. Chunk rows are unique per `(run_id, chunk_index)`, embeddings for persisted
chunks are skipped, and Qdrant point ids are new UUIDs per write.

**Can I change the queue name?**
`QUEUE_NAME` in `app/api/v1/pipelines.py` and `queue_name` in
`app/workers/tasks.WorkerSettings` must match.

**How do I add a different extraction schema?**
Edit `ExtractedInsight` in `app/workers/schemas.py`. Instructor derives the tool
definition from the model, so the field descriptions become part of the prompt.
Add matching columns to `PipelineChunk` in `app/database/db.py` if you want them
queryable.

**Where do I add rate limiting or idempotency keys?**
`app/api/v1/pipelines.py` is the single entry point for job creation, and the
ARQ pool is already available there as a dependency.

---

## License

**MIT.** Ship it, resell it, modify it, use it in commercial products. The
source is complete and unrestricted — there is no crippled edition and no key
required to use it.

The [verified release channel](#distribution-and-the-verified-release-channel) is
a paid convenience built on this same MIT-licensed code: signed reproducible
archives, a one-command setup path, versioned updates, and a commercial licence
for organisations that need one. Buying it never changes your rights to the code
in this repository.

See [LICENSE](LICENSE). Contributions are covered by the same terms —
see [CONTRIBUTING.md](CONTRIBUTING.md).
