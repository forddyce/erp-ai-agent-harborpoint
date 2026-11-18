"""Gemini agent: reads each inbox email, works through ERP tools, and leaves
proposals (or flags/skips) for human review. Nothing commits without approval."""
import os
import time

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL, SNAPSHOT_DATE
from .db import audit, get_db
from .tools import Tools

SYSTEM = f"""You are an order-processing agent for Harbor Point Supply Co., a B2B
distributor of cleaning/paper/glove/takeout supplies. Today is {SNAPSHOT_DATE}
(snapshot date). You process the orders inbox by calling tools.

For each email, decide what it is and what should happen:

1. IDENTIFY THE SENDER. Use search_customer on the sender's name/domain/signature.
   - Known contact → proceed for that customer.
   - Email domain matches a customer but person isn't a listed contact → proceed,
     but mention it in your reasoning.
   - No customer match at all → flag_for_review; do NOT create orders for
     unknown companies.

2. CLASSIFY and act:
   - NEW ORDER → extract SKU + qty lines (whole selling units only: CS=case,
     EA=each). Resolve vague items with search_product; pick the closest match
     and say why. If genuinely ambiguous, flag_for_review instead of guessing.
     Then call validate_order, then propose_create_order. Use the default
     ship-to address unless the email says otherwise. Capture the customer's PO
     number and requested delivery date if given.
   - ORDER CHANGE / CANCEL → find_orders first, then propose_change_order or
     propose_cancel_order. Respect edit rules: SHIPPED can't change,
     PARTIALLY_SHIPPED only reduces unshipped qty. If the request violates the
     rule, propose what IS allowed and note the rest needs a reply.
   - QUESTIONS (quote, status, invoice, complaint) → look up what you can with
     read tools, then propose_reply with a professional draft answer. For
     complaints you cannot resolve from data, draft an apology + escalation note.
   - SPAM / AUTO-REPLY / marketing → mark_no_action.
   - AMBIGUOUS or RISKY → flag_for_review with a specific question for the ops
     team. When in doubt, flag — do not guess on quantities, SKUs, addresses,
     or money.

3. RULES:
   - Never invent SKUs or prices — only what tools return.
   - Discontinued SKU → propose the replacement SKU instead, and say so.
   - If validate_order reports issues (credit, stock, unknown SKU), still
     submit the proposal — flag the issues in reasoning and let a human decide.
   - "The usual" / repeat orders → find_orders to infer their typical items;
     flag_for_review if you can't reconstruct it confidently.
   - Multiple orders/addresses in one email → one propose_create_order per
     delivery address.
   - Always explain your reasoning in the proposal's reasoning field: what you
     matched, what you assumed, what a human should double-check.

When you are done acting on the email, end with a one-paragraph plain-text
summary for the ops team.
"""


def process_email(con, email_row, model=GEMINI_MODEL, api_key=None):
    """Run the agent on one email row. Returns the agent's final summary text."""
    client = genai.Client(
        api_key=api_key or GEMINI_API_KEY,
        http_options=types.HttpOptions(api_version="v1beta"),
    )
    tools = Tools(con, email_row["email_id"])

    prompt = (
        f"Process this inbox email.\n\n"
        f"Email-ID: {email_row['email_id']}\n"
        f"From: {email_row['sender_name']} <{email_row['sender_email']}>\n"
        f"Date: {email_row['date']}\n"
        f"Subject: {email_row['subject']}\n\n"
        f"{email_row['body']}"
    )
    chat = client.chats.create(
        model=model,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=tools.all(),
            temperature=0,
        ),
    )
    resp = _send_with_retry(chat, prompt)
    summary = (resp.text or "").strip()
    return summary


def _retry_delay(err):
    import re
    m = re.search(r"retry in ([\d.]+)s", str(err))
    return min(float(m.group(1)) + 2, 70) if m else 20


def _send_with_retry(chat, prompt, attempts=6):
    for i in range(attempts):
        try:
            return chat.send_message(prompt)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                d = _retry_delay(e)
                print(f"    (rate limited, retry {i+1}/{attempts} in {d:.0f}s)")
                time.sleep(d)
            else:
                raise
    return chat.send_message(prompt)


def run_inbox(limit=None, only_pending=True, sleep_s=1.0, model=GEMINI_MODEL):
    """Process all pending emails sequentially."""
    with get_db() as con:
        q = "SELECT * FROM emails"
        if only_pending:
            q += " WHERE status='pending'"
        q += " ORDER BY email_id"
        rows = con.execute(q).fetchall()
        if limit:
            rows = rows[:limit]
        for row in rows:
            eid = row["email_id"]
            print(f"--- {eid}: {row['subject']}")
            try:
                summary = process_email(con, row, model=model)
                # keep 'skipped' status set by mark_no_action
                cur = con.execute("SELECT status FROM emails WHERE email_id=?",
                                  (eid,)).fetchone()[0]
                status = "skipped" if cur == "skipped" else "processed"
                con.execute(
                    "UPDATE emails SET status=?, agent_notes=COALESCE(agent_notes, ?), "
                    "processed_at=datetime('now') WHERE email_id=?",
                    (status, summary, eid))
                audit(con, "agent", "email_done", email_id=eid, detail={"summary": summary})
                con.commit()
                print(f"    -> {summary[:160]}")
            except Exception as e:
                con.execute(
                    "UPDATE emails SET status='error', agent_notes=? WHERE email_id=?",
                    (f"{type(e).__name__}: {e}", eid))
                audit(con, "system", "email_error", email_id=eid,
                      detail={"error": f"{type(e).__name__}: {e}"})
                con.commit()
                print(f"    !! {e}")
            time.sleep(sleep_s)  # pace for free-tier RPM limits


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--all", action="store_true")
    p.add_argument("--model", default=GEMINI_MODEL)
    args = p.parse_args()
    run_inbox(limit=args.limit, only_pending=not args.all, model=args.model)
