# CreditGate frontend

Plain HTML/CSS/vanilla JS — no framework, no build step, no `npm install`.
Runs as its own completely separate process from the backend API.

## Why separate

The backend used to serve this directory itself (mounted under `/app`).
That meant `uvicorn --reload` — which watches the *entire* working
directory by default — restarted the whole backend process on every
frontend-only edit (a `.js`/`.html`/`.css` change with nothing to do with
the API), which re-runs the ~1-minute, 8,000-applicant dataset build on
every save. Splitting them into two processes fixes that structurally:
editing this directory can never again restart the backend, or vice versa.

## Running it

From the repo root, with the backend already running separately
(`uvicorn src.api.main:app --reload`, port 8000):

```bash
python -m http.server 5500 --directory frontend
```

Then open <http://localhost:5500>. The page talks to the backend at
`http://localhost:8000` by default (see `app.js`'s `API` constant) — pass
`?api=http://host:port` in the URL if your backend is running somewhere
else. CORS is already enabled on the backend (`src/api/main.py`) for this.

Any static file server works here — this isn't tied to Python's
`http.server` specifically, it's just dependency-free.
