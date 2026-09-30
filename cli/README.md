# Astrocoda CLI

A tiny license-gated CLI so buyers get the boilerplate through a checkout, not
a public clone.

## How it works (offline signed keys)

```
pip install ./cli
astrocoda login <long-key-string>   # verify the signature locally, store it
astrocoda init <name>               # re-verify locally, scaffold from the signed template
astrocoda up                        # re-verify locally, docker compose up
```

- Validation is **local and cryptographic**: a license key is
  `astrocoda_<base64url(payload)>.<base64url(ed25519 signature)>`, where the
  payload is `{"email", "plan", "exp"}`. The seller signs keys with a private
  Ed25519 key they keep; this package ships with only the matching **public**
  key.
- Because `exp` is inside the signed payload, you cannot bump your expiry or
  doctor `credentials.json` without breaking the signature. A forged or tampered
  key fails `LicenseKeyError` with no network involved.
- Nothing phones home. There is no license server to reach, no activation ping,
  and no key to leak server-side. The trade-off: a key cannot be remotely
  revoked, so a leaked key stays valid until its signed expiry (fine for a
  once-per-customer purchase at this price).
- The public key is public. It lets you *verify* keys, never create them — only
  the seller's private signer can mint a new one.

`astrocoda status` prints the email, plan and expiry recorded in your key, so
you always know what you bought and when it lapses.

## Installing the CLI (buyer side)

```bash
pip install ./cli
astrocoda login <the-key-you-were-sent>
```

`login` takes the key as a single positional argument. Your key is stored in
`~/.astrocoda/credentials.json`; nothing else is written to your machine.

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

Because the manifest ships **inside** the release, you can point `init` at any
copy or mirror of the template (the zip, a private clone, a USB stick) and get
exactly the tree the seller signed. Nothing depends on a server.

Typical first run after unzipping the release:

```bash
astrocoda init myapp --source .     # or export ASTROCODA_TEMPLATE=$PWD first
cd myapp
astrocoda up
```

## Commands

| Command | Gated | What it does |
|---|---|---|
| `login <key>` | no | verify the signed key locally, store it in `~/.astrocoda` |
| `status` | no | show stored email / plan / expiry |
| `logout` | no | remove the stored key |
| `init <name> [--source PATH]` | yes | re-verify signature locally, scaffold a project, generate `.env` |
| `up [--dir DIR]` | yes | re-verify signature locally, `docker compose up --build -d` |

Environment:

| Variable | Default | Meaning |
|---|---|---|
| `ASTROCODA_HOME` | `~/.astrocoda` | where `credentials.json` is stored |
| `ASTROCODA_TEMPLATE` | the repo the CLI shipped with | default `init` template |
| `ASTROCODA_PUBLIC_KEY` | `<package>/license_public.pem` | replacement seller public key |

The CLI requires only `cryptography`; nothing else is imported from the
boilerplate it deploys.

## Tests

The suite generates a throwaway keypair per test run, so a fresh checkout with
no signing key anywhere can still verify everything (signing, tamper rejection,
expiry, forged-store rejection, gated commands) with
`python -m pytest tests/test_cli.py`.