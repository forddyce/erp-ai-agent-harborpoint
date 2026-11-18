# Harbor Point Order Agent

An agent-native order-desk assistant for Harbor Point Supply Co. A Gemini agent
reads the orders inbox, works the ERP **through tools** (customer/product lookups,
pricing, stock and credit checks), and produces **proposals**. A human ops
reviewer approves, edits or rejects each proposal before anything commits to the
ERP. Every tool call and decision is written to an audit log.

## Stack

- **Agent**: Gemini (`google-genai`, function calling) — default model
  `gemini-3.5-flash-lite`, override with `GEMINI_MODEL`
- **Backend/UI**: FastAPI + Jinja2 (server-rendered review UI)
- **DB**: SQLite (`agent.db`) — ERP tables + emails + proposals + audit_log
- **Deploy**: GitHub Actions → SSH → VPS (`erp` user, systemd, nginx, Let's Encrypt)

## Layout

```
app/
  ingest.py   # CSVs + .eml files -> agent.db
  domain.py   # business rules: pricing, credit, stock, order editability
  tools.py    # read tools + proposal-writing tools exposed to the agent
  agent.py    # Gemini loop, system prompt, inbox runner
  server.py   # review UI: inbox, email detail, decisions, audit, orders
  templates/
tests/        # unittest suite for the business rules
deploy/       # systemd unit + nginx vhost
docs/         # product brief, task breakdown, results, DATA-PACK.md (original spec)
data/ emails/ # the provided data pack (gitignored — obtained separately)
```

## Run locally

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
echo "GEMINI_API_KEY=..." > .env        # plus optional GEMINI_MODEL
venv/bin/python -m app.ingest          # load ERP + inbox into agent.db
venv/bin/python -m app.agent           # process all pending emails
                                       # (--limit N, --all, --model M)
venv/bin/uvicorn app.server:app --port 8787
# open http://127.0.0.1:8787
```

Tests: `venv/bin/python -m unittest discover -s tests`

## Autonomy boundary

| Agent can do alone | Needs human approval |
|---|---|
| classify emails, look up ERP data | create an order (commits inventory) |
| draft replies, mark spam/auto-replies | change or cancel any order |
| flag anything ambiguous for review | send a reply (drafts only — nothing is emailed) |

## Deploy

Push to `main` → `.github/workflows/deploy.yml` rsyncs the app to
`/opt/erp-agent` on the VPS and restarts `erp-agent.service`.
Secrets required in the GitHub repo: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.
The `erp` deploy user may only `systemctl restart erp-agent` via sudoers.

Live: <https://erp.fordgamerprofile.com> (nginx basic auth; credentials shared
out-of-band).
