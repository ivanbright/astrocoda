# Contributing

Thanks for looking. This is a working backend, not a demo, so the bar for a
change is that it stays working.

## Getting set up

```bash
git clone <your-fork> astrocoda
cd astrocoda
python bootstrap.py          # writes .env with a generated SECRET_KEY
make setup                   # same thing
```

You do not need Docker to work on the code, and you do not need any API keys to
run the tests.

## Running the tests

```bash
pip install -r requirements-dev.txt
make test                    # or: python -m pytest
```

The suite is hermetic on purpose — no Postgres, Redis, Qdrant or model provider
is started or contacted, and external services are replaced by in-memory fakes.
That is a deliberate design constraint: contributors on a plane should be able
to run it. Please keep new tests hermetic too. If a test needs a real service,
it belongs in a separate opt-in path, not in the default run.

## What a good pull request looks like

- **The tests pass on the version in `requirements.txt`**, and stay hermetic.
- **It matches the surrounding style.** Type annotations on every public
  function, Pydantic models for anything crossing a boundary, `async`/`await`
  all the way down in request and worker paths. No `requests`, no blocking I/O
  in an async context, no thread pool.
- **It does not make the request path slower.** The trigger endpoint enqueues
  and returns; the LLM work belongs in the worker. A change that makes a request
  wait on a model call is a regression even if it is faster to write.
- **It keeps `EMBEDDING_DIMENSIONS` honest.** If you change the embedding
  model, the dimensions and the Qdrant collection name change with it.
- **Comments explain why, not what.** This codebase tries to say the non-obvious
  thing in a sentence rather than narrate the code.

## Working in this repository vs. running `init`

`astrocoda.manifest.json` is committed and it is **signed**. If you edit a file
that the manifest lists, `astrocoda init` will refuse to scaffold your tree and
tell you a hash does not match.

That is the tamper check working, not a bug in your setup. Contributors work
directly in the checkout — run the tests, run the app, edit the files. You do
not need to re-run `init` on your own working copy, and you should not try to
re-sign the manifest; the signing key is not in this repository by design.

## Commit and PR conventions

Commit messages in the imperative, with a short subject and a body when the
reasoning is not obvious:

```
Handle empty Qdrant API keys without dropping the header

An empty QDRANT_API_KEY makes the server demand auth with an empty
key, so coercing "" to None produced a 401 on every request.
```

Open a PR against `main`. Describe the behaviour you changed and how you tested
it. Small, focused PRs get reviewed quickly; drive-by refactors bundled into
feature work do not.

## Reporting bugs

Open an issue with the smallest reproduction you can manage: what you did, what
you expected, what happened, and the output of `python -m pytest` and
`docker compose ps` if the stack is involved. Security problems go through
[SECURITY.md](SECURITY.md) instead.

## Code of conduct

Be straightforward and courteous. Assume good faith, critique the code rather
than the person, and take disagreements to the technical merits.

## License

By contributing you agree that your work is licensed under the
[MIT License](LICENSE), the same terms as the project.
