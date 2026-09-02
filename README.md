# Docmind

Docmind is an app that let's you talk with your documents (PDFs, images, screenshots). It also runs a scheduled bulk job that summarices everything you uploded overnight.

Docmind can be used by anyone who knows how to install apps from github.

## SDLC phases:
- Design: use very simple GUI components
- ld: PoC first approach, quickly try things at first
- Test: simple unit tests first
- deploy: run on own machine first
- maintain: check that libraries are up to date

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/). It handles Python itself — `.python-version` pins 3.13, and uv downloads it if you don't have it.

Clone and install dependencies into a local `.venv`:

```sh
git clone <repo-url> docmind
cd docmind
uv sync
```

Run the dev server (auto-reload and the debugger are on):

```sh
uv run docmind
```

Then open http://localhost:8000. There's also a `GET /healthz` endpoint that returns `{"status": "ok"}`.

Set `PORT` to use a different port:

```sh
PORT=8080 uv run docmind
```

If you prefer the Flask CLI — e.g. to bind a different host:

```sh
uv run flask --app docmind:create_app run --debug --host 0.0.0.0 --port 8000
```

> **Don't use port 5000 on macOS.** AirPlay Receiver (Control Center) already listens
> there, so requests to `localhost:5000` get answered by it — you'll see a bare
> `403 Forbidden` with `Server: AirTunes` instead of reaching Flask. Either keep the
> 8000 default or turn off System Settings → General → AirDrop & Handoff → AirPlay Receiver.

### Layout

```
src/docmind/
    __init__.py          create_app + the `docmind` console script
    app.py               Flask application factory and routes
    templates/           Jinja templates
```

### Dependencies

Add one with `uv add <package>` (use `uv add --dev <package>` for tooling such as test runners). Both commands update `pyproject.toml` and `uv.lock`; commit the lockfile so everyone gets the same versions. `uv lock --upgrade` refreshes them for the maintenance phase.
