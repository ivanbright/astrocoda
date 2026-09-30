# Security Policy

## Supported versions

`main` is supported. Released versions receive fixes for 90 days after a newer
release ships.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: **Security → Report a vulnerability** on this
repository. If that is unavailable, email **<mugishaivanbright250@gmail.com>**
with the details and ask for a private reply channel.

Include what an attacker can do, and the steps to reproduce it. You should get
an acknowledgement within 72 hours and an assessment within 7 days.

## What is worth reporting

The pipeline is a backend that runs on your own infrastructure, so the usual
web-application classes of bug mostly do not apply. These do, and we would like
to hear about them:

| Area | Example |
|---|---|
| **Signature verification** | Any input for which `astrocoda login` or `astrocoda init` accepts a key, or a template, that it should reject |
| **Template integrity** | A way to make `init` ship a file that is not listed in the signed manifest, or to alter a listed file without detection |
| **Path handling** | Escaping the scaffold directory, writing outside it, or following a symlink to overwrite a file the manifest does not cover |
| **Webhook handling** | Getting `POST /api/v1/webhooks/stripe` past signature verification, or forging an `is_active` state change |
| **Injection** | Reaching the database, Qdrant, or the LLM prompt with unvalidated input that should have been rejected |
| **Secrets** | Any real credential, key, or `.env` reaching a committed file, image layer, or log line |

A note on scope: `X-API-Key` and the short-lived JWT protect routes inside *your*
deployment, and the sample values in `.env.example` are placeholders. Committing a
real key is a bug in the tooling, not an attack, and we will help you rotate.

## Out of scope

- Findings that require you to already control the host or the environment
  variables.
- Denial of service by flooding your own deployment.
- Missing hardening headers on the sample application.
- Anything the MIT-licensed source code already makes public. The code is
  intentionally open; we cannot fix disclosure of what is published on purpose.
