# wheels/ — optional offline package cache

The Docker build prefers pre-fetched Linux wheels in this directory so the
image can be built with no network access.

You do not need to do anything here. The directory ships empty and the builder
falls back to the configured package index, which is what almost every buyer
wants. This placeholder (and its signed manifest entry) exists so the directory
is always present in scaffolded projects — `COPY wheels ./wheels` in the
Dockerfile requires it.

A seller publishing an air-gapped release can drop Linux wheels in here so the
build works offline; because wheels fetched that way are hashed into
`astrocoda.manifest.json` at signing time, they ship byte-exact. The helper that
fetches them is seller tooling and lives outside this tree.