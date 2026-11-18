# Task breakdown — specs given to the coding agent

Raw, as actually worked. Each item: goal + acceptance criteria.

## T1 — Data pipeline
Load the data pack into a controllable SQLite DB (`agent.db`).
- Schema: 9 ERP tables typed (TEXT columns cast: money→REAL, qty→REAL,
  flags→INT) + agent tables (`emails`, `proposals`, `audit_log`).
- `emails` parses real .eml files (From name/addr split, subject, date, body
  incl. multipart); ids = filename stem.
- **Acceptance**: `python -m app.ingest` prints "40 emails, 30 products";
  reruns are idempotent (DELETE + reload).

## T2 — Domain layer (`app/domain.py`)
Encode the README's business rules as pure functions on a db connection.
- `price_for`: contract price iff order_date within validity, else list.
- `credit_check`: CREDIT_HOLD blocks; open_balance + order_total > credit_limit
  blocks.
- `validate_order_lines`: per-line checks — known SKU, not DISCONTINUED (surface
  `replaced_by`), whole selling units, price, stock availability (attach
  incoming supply on shortage), plus credit check on the total.
- `check_order_edit`: SHIPPED=not editable, PARTIALLY_SHIPPED=unshipped qty
  only, OPEN=full.
- `commit_order` (allocates inventory, next SO number), `commit_order_change`,
  `commit_cancel_order` (releases allocation; PARTIALLY_SHIPPED → reduce to
  shipped qty).
- **Acceptance**: unittest suite covers every rule incl. edge cases
  (14 tests, all passing).

## T3 — Agent tools (`app/tools.py`)
Gemini-callable functions on a per-email `Tools` object. Every call is logged
to `audit_log` with args + result.
- Reads: search_customer, get_customer, search_product, check_inventory
  (+incoming supply), get_price, find_orders, validate_order.
- Writes — proposals only, nothing commits: propose_create_order (re-runs
  validate server-side), propose_change_order, propose_cancel_order,
  propose_reply, flag_for_review, mark_no_action.
- **Acceptance**: write tools create `proposals` rows only; ERP tables are
  untouched until a human approves via the UI.

## T4 — Agent loop (`app/agent.py`)
Gemini chat with automatic function calling, temperature 0, system prompt that
fixes the decision policy (identity match rules, classification → action map,
"when in doubt flag", one proposal per ship-to). Retry w/ backoff on 429
(free-tier 5 RPM). Per-email final text = ops summary stored on the email.
- **Acceptance**: `python -m app.agent` walks all 40 emails; each ends
  processed/skipped/error with proposals or a flag; tool trace in audit_log.

## T5 — Review UI (`app/server.py` + Jinja)
- `/` inbox: status, pending-proposal count, agent notes.
- `/emails/{id}`: original email, agent summary, proposals rendered by type
  (priced line table w/ issues for orders, diff JSON for changes, draft for
  replies), approve / approve-with-JSON-edits / reject form, full tool trace.
- `/audit`: global log. `/orders`: live ERP order state (proof of commits).
- **Acceptance**: approving a create_order proposal returns SO-##### and the
  order + allocation appear under /orders; rejection needs a reviewer name.

## T6 — Deploy
systemd `erp-agent` (uvicorn 127.0.0.1:8790, user `erp`), nginx vhost
erp.fordgamerprofile.com → proxy + basic auth, certbot SSL, GitHub Actions:
rsync → pip install → migrate (ingest if no db) → `sudo -n systemctl restart`.
- **Acceptance**: push to main deploys; https://erp.fordgamerprofile.com returns
  401 without creds, 200 with; service restarts cleanly.

## T7 — Results
Run all 40 emails, eyeball every output, classify right/wrong/flagged
appropriately → `docs/results.md`.
