# Security Policy

## Credentials

The only credential Scriptorium handles is your **Stash API key**, needed when
authentication is enabled on your Stash instance.

Pass it as the `STASH_API_KEY` environment variable. It is never written to
disk, never logged, and never appears on the status page or in `/json`.

If you keep it in a compose file, remember that file is readable by anyone who
can read your compose directory. Docker's `env_file`, or a secret manager if
you have one, keeps it out of the file you are most likely to paste into a
forum post when asking for help.

## Network

Scriptorium makes outbound requests to:

- **Your Stash instance** (default `http://stash:9999`) — GraphQL only.
- **Your Ollama instance**, if `OLLAMA_URL` is set — translation only.
- **huggingface.co**, once per model, to download the Whisper weights.

Nothing is sent to any analytics service or other third party. Subtitle text
never leaves your network unless you point `OLLAMA_URL` at something remote.

## The status page

The page on port 8088 is **unauthenticated**. It exposes scene titles, file
paths and the log, and its `POST` endpoints can start a poll or pause the
worker.

- Cross-site `POST` requests are rejected, so a page you visit in a browser
  cannot drive the worker.
- There is no authentication on `GET`. Do not publish port 8088 to the
  internet. If you need remote access, put it behind your own reverse proxy
  with auth, or reach it over a VPN.
- Binding is controlled by `HTTP_HOST`. It defaults to `0.0.0.0` because the
  container's port must be reachable from the host to be published at all;
  restrict exposure with Docker's port mapping rather than by binding wider
  than you publish.

## Filesystem

The worker writes subtitle files next to your videos, in the directory Stash
reports for each scene. It never deletes or modifies a video, and it will not
overwrite an existing subtitle unless you set `REGENERATE`.

It runs as root inside the container so that it can write into media
directories owned by arbitrary users, which is the same posture as the
official Stash image. If you would rather it did not, set `user:` in your
compose file to a UID that can write to your library.

## Reporting a vulnerability

Open a GitHub issue, or email the maintainer directly if you would rather not
discuss it in public. There is no bug bounty program.
