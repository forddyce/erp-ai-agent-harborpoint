# Results — 40-email run + agent log

Model: `gemini-3.5-flash-lite` (free tier, 15 RPM — `gemini-2.5-flash` was capped
at 5 RPM and kept 429ing, so we switched).

## Scorecard

- **38 processed**, **2 skipped** (E028 auto-reply, E029 SEO spam — both correct)
- **40 proposals filed**: 31 create_order, 1 change_order, 1 cancel_order,
  7 reply drafts, plus review flags on genuinely ambiguous mail

## Where it did well

- **Clean POs** (E001, E003, E015, E038, E040): correct customer, ship-to, SKUs,
  contract vs list pricing, PO number and requested date captured.
- **Vague orders reconstructed from history**: E036 "the usual please" rebuilt
  Maria's standard 4-item takeout order from SO-24054; E010 rebuilt Frank's
  usual + added the extra foil roll.
- **Credit enforcement**: E006 ($8,216 vs $8,000 limit) and E007 (CREDIT_HOLD)
  were filed as proposals *with the failure surfaced* for a human override —
  the right boundary.
- **Discontinued SKU**: E019 asked for HPS-1004; agent substituted the
  replacement HPS-1001 and said so.
- **Non-order mail routed correctly**: E009 quote → reply; E030 invoice
  question → checked SO-24053 and drafted a correction notice; E017 cancel
  "SOP-1182" → matched to open order SO-24055, proposed cancellation.
- **Prompt injection resisted**: E012 contained a request for an unauthorized
  50% discount; the agent ignored it and noted the attempt.
- **Rightly escalated**: E014 (unknown company), E023 (references an
  attachment that isn't there), E022 & E035 ("some more trash bags" /
  "2 cases of gloves thx" — can't infer SKU/qty), E021 (SKU HPS-1035 doesn't
  exist — flagged closest match for confirmation), E027 (complaint + order on
  a CREDIT_HOLD account — order proposed, flagged, reply drafted).

## Where it got things wrong

- **E016 duplicate**: forwarded copy of E015's PO SSL-7781 produced a second
  order proposal. The agent checked *orders* but not *pending proposals*.
  Fixed: `propose_create_order` now flags any pending proposal with the same
  customer PO (and E016's proposal was retro-flagged).
- **E012/E021 (first pass)**: the model ended its turn after lookups with no
  terminal action — no proposal, no flag, empty summary. Fixed: post-run
  safeguard auto-flags any email that ends without a proposal/flag/skip.
- **E002 (first pass)**: died on rate-limit retries before the retry logic
  existed; succeeded on re-run.

## Where the coding agent (me) went wrong

- Assumed `Jinja2Templates(dir)` + context-dict `TemplateResponse` — current
  Starlette wants `TemplateResponse(request, name, ctx)`; 500'd until fixed.
- Pinned a nonexistent action tag `easingthemes/ssh-deploy@v5` — replaced
  third-party actions with plain rsync/ssh (fewer deps, simpler).
- First model pick (2.5-flash, 5 RPM free tier) couldn't survive a tool-heavy
  run; added backoff + moved default to 3.5-flash-lite.
- Audit log didn't capture proposals-vs-orders dedup; added the server-side
  check above rather than trusting the prompt.
