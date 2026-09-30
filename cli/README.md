# Astrocoda CLI

A small, free CLI that downloads and verifies the boilerplate, so you get a
signed, known-good tree without unzipping anything by hand.

There is **no account and no license key**. The project is MIT licensed and the
scaffolder is ungated.

## Install and run

```bash
uvx astrocoda-cli init myapp
cd myapp
astrocoda up
```

That is the whole thing. `init` downloads the published release, verifies it
against the seller's signature, and scaffolds a project with a fresh `.env`.

To install it as a normal command instead:

```bash
pip install astrocoda-cli
astrocoda init myapp
```

## The one optional question

During `init` the CLI asks:

```
Share your email to get update news? [y/N]
```

This is entirely optional in both directions:

- **Decline** — press Enter or `n` and nothing is sent. There is no request at
  all. The scaffold is byte-for-byte identical.
- **Agree** — the address is POSTed to the seller's list so you hear about
  releases. `astrocoda optout` removes it, and `astrocoda status` shows what is
  currently stored.
- **Scripted or piped** — you are never prompted at all. The CLI only asks when
  both stdin and stdout are a terminal, so CI and `uvx` in a pipeline cannot
  hang on a `y/N`.

Silence is the default everywhere, including the flags:

| Flag / variable | Effect |
|---|---|
| `--no-email` | never ask, send nothing |
| `--email you@example.com` | skip the question and share this address |
| `ASTROCODA_NO_EMAIL=1` | same as `--no-email`, for scripts and CI |
| `ASTROCODA_EMAIL=you@example.com` | same as `--email` |

Declining is remembered, so you are not asked again on the next `init`.

## How `init` stays secure (signed templates)

`astrocoda init` is a supply-chain check, not a copy job. Before a single file is
written it verifies the template against a **signed manifest** the seller
produced and shipped inside the release:

```json
{ "version": "0.1.0", "files": { "app/main.py": "<sha256>", "...": "..." },
  "signature": "<base64url ed25519 over the canonical manifest>" }
```

When you run `init`:

- the CLI finds `<template>/astrocoda.manifest.json` — **no manifest means it
  refuses to scaffold**;
- it verifies the manifest's signature with the seller's embedded public key —
  a manifest re-signed by anyone else is rejected;
- it recomputes the hash of **every listed file** — one changed byte aborts;
- it copies **only the listed files** (a whitelist) — an attacker who drops an
  extra file into the template does not get it shipped.

This is the part that matters, and it is unaffected by whether you shared an
email. Nobody gating your scaffold is not the same as nobody verifying it.

Because the manifest ships **inside** the release, you can point `init` at any
copy or mirror of the template (a zip, a private clone, a USB stick) and get
exactly the tree the seller signed.

## Commands

| Command | What it does |
|---|---|
| `init <name>` | verify the signed release, scaffold a project, generate `.env` |
| `up [--dir DIR]` | `docker compose up --build -d` |
| `identify [email]` | share an address for update news (optional) |
| `status` | show the version and what this machine has shared |
| `optout [--local-only]` | forget the stored address and unsubscribe |

`init` options:

| Option | Default | Meaning |
|---|---|---|
| `--source PATH` | downloaded release | scaffold from a local template directory |
| `--version VERSION` | the CLI's own version | which release to install |
| `--offline` | off | never download; use a release already in the cache |
| `--release-url URL` | the GitHub release | override the base URL (must contain `{version}`) |

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ASTROCODA_HOME` | `~/.astrocoda` | where `identity.json` and the release cache live |
| `ASTROCODA_TEMPLATE` | the repo the CLI shipped with | default `init` template |
| `ASTROCODA_PUBLIC_KEY` | `<package>/license_public.pem` | replacement seller public key |
| `ASTROCODA_RELEASE_BASE_URL` | the GitHub release | where signed releases are fetched from |
| `ASTROCODA_OPTIN_URL` | `https://astrocoda.dev/v1/subscribe` | where an opt-in is sent |
| `ASTROCODA_NO_EMAIL` | unset | set to `1` to suppress the question |
| `ASTROCODA_EMAIL` | unset | supply an address without being asked |

Self-hosting the opt-in endpoint? Point `ASTROCODA_OPTIN_URL` at your own. It
needs to accept `POST` with `{email, version, source, ts}` and answer any 2xx.
The CLI treats anything else — including a timeout — as "no" and carries on.

## Requirements

The CLI requires only `cryptography`; nothing else is imported from the
boilerplate it deploys. `up` additionally needs Docker.

## Tests

The suite generates a throwaway keypair per test run, so a fresh checkout with
no signing key anywhere can still verify everything (manifest signing, tamper
rejection, scaffold refusal). The email opt-in tests never touch the network.
