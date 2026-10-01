# Contributing

## Cutting a release

The tag is the version. There is nothing to bump: `pyproject.toml` stays at
`0.0.0`, and only a release build learns its number, from the tag. Tag and
push:

```bash
git tag -a v0.8.0 -m "v0.8.0"
git push origin v0.8.0
```

Pushing the tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml):

- **`version`** checks the tag has the shape `vX.Y.Z` and fails the release
  if it does not. The tag reaches the image as the `VERSION` build arg and is
  baked into every generated subtitle's provenance and shown on the status
  page. A tag that is not a version would not fail anything further on — it
  would publish an image tagged `0.8` whose subtitles all claim `0.0.0`.
- **`docker`** builds, attests and publishes the image to `ghcr.io`.
- **`notes`** publishes the GitHub release, with a changelog and the image
  reference to pull.

Anything built without a release tag — dev images, a local `make image`, the
test suite — reports `0.0.0`. That is deliberate: a dev image used to stamp
whatever number the last release had left in the source.

There is no approval gate. With the tag as the only copy of the version
there is no second value to drift from it, and the `version` job catches a
malformed tag on every push, unlike the sibling repos whose gates exist
because CI cannot run their live-service smoke tests.

## Tests

```sh
make check   # lint (ruff) and test (pytest) — the same gate CI applies
make test    # tests only
make lint    # ruff check only
```

Everything runs in a container (`python:3.12-slim`), so a checkout needs
nothing installed but Docker.
