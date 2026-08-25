# Contributing

## Cutting a release

Bump `version` in `pyproject.toml`, commit it, then tag and push:

```bash
git tag -a v0.8.0 -m "v0.8.0"
git push origin v0.8.0
```

Pushing the tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml):

- **`version`** checks the tag against `pyproject.toml`'s declared version and
  fails the release if they disagree. The version is baked into every
  generated subtitle's provenance and shown on the status page, so a stale
  one mislabels output that outlives the container — this has silently
  drifted twice before, which is why the tag is the referee.
- **`docker`** builds, attests and publishes the image to `ghcr.io`.
- **`notes`** publishes the GitHub release, with a changelog and the image
  reference to pull.

There is no approval gate: the recurring failure mode here is a version
mismatch, and the `version` job already catches that on every tag, unlike
the sibling repos whose gates exist because CI cannot run their live-service
smoke tests.

## Tests

```sh
make check   # lint (ruff) and test (pytest) — the same gate CI applies
make test    # tests only
make lint    # ruff check only
```

Everything runs in a container (`python:3.12-slim`), so a checkout needs
nothing installed but Docker.
