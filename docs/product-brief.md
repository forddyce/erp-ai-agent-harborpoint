# Product brief — Harbor Point Order Agent

## User

The **order-desk / customer-service team** at Harbor Point Supply Co., a small
B2B distributor. Every weekday morning they face a mixed inbox: clean purchase
orders, vague "the usual please" requests, order changes, complaints, questions,
spam. Today each email is triaged and keyed into the ERP by hand.

## Problem

Order intake is manual, slow and error-prone — pricing (contract vs list), credit
checks and stock availability all live in different screens. The failure mode
isn't just speed: a keyed-in order that breaks a credit limit or promises
out-of-stock product is a real business cost.

## What we built

An agent that works *inside the ERP through tools*, plus a review UI:

1. **Ingest**: 40 inbox emails + the ERP snapshot into one SQLite DB.
2. **Agent** (Gemini, function calling): per email it identifies the customer,
   classifies the request (new order / change / cancel / question / spam /
   auto-reply), looks up real data, runs the order through every business rule
   (`validate_order`), then files a **proposal** — never a committed order.
3. **Review UI**: inbox list → per-email detail showing the original email, the
   agent's summary, its full tool-call trace, and each proposal with priced
   lines, stock and credit checks inline. Approve & commit / approve with JSON
   edits / reject with a note.
4. **Audit trail**: every tool call (args + result), every proposal, every human
   decision with reviewer name, timestamp and committed order reference.

## What the agent may do alone vs. needs approval

| Autonomous | Human approval |
|---|---|
| Read anything in the ERP (customers, products, prices, stock, orders) | **Create an order** — allocates inventory |
| Classify + extract line items | **Change / cancel an order** |
| Mark spam, auto-replies, duplicates as no-action | **Send a reply** — the agent drafts, humans send |
| Flag ambiguous/risky emails with a specific question | |

Rationale: everything reversible and read-only is autonomous; anything that
moves money, allocates stock or touches a customer waits for a person. This
also means a wrong agent guess costs a click, not a phone call.

## What we chose NOT to build (scope decisions)

- **Actually sending email** — proposals for replies are drafts; sending is out
  of scope for a demo and risky without an outbound mail system.
- **New-customer onboarding** — unknown senders are flagged, not auto-created.
- **Payment/invoice processing** — invoice questions get a drafted reply only.
- **Partial shipment/backorder automation** — stock shortages are surfaced in
  the proposal (with incoming-supply dates) for a human to decide.

## Product decisions not covered by the README

- **Vague orders** ("the usual"): the agent looks up order history and proposes
  its best reconstruction, flagged in the reasoning — never silently guessed.
- **Discontinued SKUs**: agent substitutes `replaced_by` in the proposal and
  notes the substitution for review.
- **Failing checks ≠ rejected proposal**: a proposal that fails credit/stock is
  still filed with the failures highlighted — the human owns the override call,
  e.g. a good customer over limit might still get released.
- **One proposal per delivery address** when a mail orders to multiple sites.
- **Sender identity**: matching email domain + known contact → confident; domain
  match but unknown person → proceed with a note; no match → flag_for_review.

## Success metrics in production

- **Straight-through rate**: % of order emails approved with zero edits (target:
  start ~50%, grow as the team trusts it).
- **Time per email**: minutes from open to approved vs. manual baseline.
- **Error escape rate**: committed orders later needing correction (target 0
  credit breaches, 0 wrong-SKU commits).
- **Flag precision**: % of flagged emails that genuinely needed a human (low
  false-positive = agent isn't crying wolf).
- **Override patterns**: which checks humans override most → where policy or
  data should change.
