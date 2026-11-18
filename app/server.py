"""Ops-team UI: inbox of processed emails, proposals with approve/edit/reject,
full audit trail."""
import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import domain
from .db import audit, get_db

app = FastAPI(title="Harbor Point Order Agent")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _j(x):
    try:
        return json.loads(x or "{}")
    except Exception:
        return {}


@app.get("/")
def inbox(request: Request):
    with get_db() as con:
        emails = [dict(r) for r in con.execute("""
            SELECT e.*,
              (SELECT COUNT(*) FROM proposals p WHERE p.email_id=e.email_id AND p.status='pending') n_pending
            FROM emails e ORDER BY e.email_id""").fetchall()]
    return templates.TemplateResponse(request, "inbox.html", {"emails": emails})


@app.get("/emails/{email_id}")
def email_detail(request: Request, email_id: str):
    with get_db() as con:
        e = dict(con.execute("SELECT * FROM emails WHERE email_id=?",
                             (email_id,)).fetchone())
        props = []
        for p in con.execute(
                "SELECT * FROM proposals WHERE email_id=? ORDER BY proposal_id",
                (email_id,)).fetchall():
            d = dict(p)
            d["payload"] = _j(d["payload"])
            props.append(d)
        log = [dict(r) for r in con.execute(
            "SELECT * FROM audit_log WHERE email_id=? ORDER BY id", (email_id,)).fetchall()]
    return templates.TemplateResponse(request, "email.html",
                                       {"e": e, "proposals": props, "log": log})


@app.post("/proposals/{pid}/decision")
def decide(request: Request, pid: int, decision: str = Form(...),
           note: str = Form(""), reviewer: str = Form("ops")):
    with get_db() as con:
        p = con.execute("SELECT * FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
        payload = _j(p["payload"])
        error = None
        if decision in ("approve", "edited_approve"):
            try:
                ref = _commit(con, p["type"], payload, decision,
                              _j(note) if note.strip().startswith("{") else None)
                status = "committed"
                con.execute(
                    "UPDATE proposals SET status=?, decided_at=datetime('now'), "
                    "decided_by=?, decision_note=?, committed_ref=? WHERE proposal_id=?",
                    (status, reviewer, note or None, str(ref), pid))
                audit(con, f"human:{reviewer}", f"{decision}_{p['type']}",
                      email_id=p["email_id"], proposal_id=pid,
                      detail={"note": note, "committed_ref": str(ref)})
            except Exception as ex:
                error = f"{type(ex).__name__}: {ex}"
                audit(con, f"human:{reviewer}", "commit_error",
                      email_id=p["email_id"], proposal_id=pid, detail={"error": error})
        else:  # reject
            con.execute(
                "UPDATE proposals SET status='rejected', decided_at=datetime('now'), "
                "decided_by=?, decision_note=? WHERE proposal_id=?",
                (reviewer, note or None, pid))
            audit(con, f"human:{reviewer}", "reject",
                  email_id=p["email_id"], proposal_id=pid, detail={"note": note})
        con.commit()
        if error:
            return RedirectResponse(
                f"/emails/{p['email_id']}?error={error}", status_code=303)
        return RedirectResponse(f"/emails/{p['email_id']}", status_code=303)


def _commit(con, ptype, payload, decision, edited):
    if ptype == "create_order":
        lines = (edited or {}).get("lines") or [
            {"sku": l["sku"], "qty": l["qty"], "unit_price": l["unit_price"]}
            for l in payload["lines"]]
        return domain.commit_order(
            con, payload["customer_id"], payload["ship_to_id"],
            payload.get("customer_po"), lines,
            requested_date=payload.get("requested_date"))
    if ptype == "change_order":
        return domain.commit_order_change(con, payload["so_number"],
                                          (edited or {}).get("changes") or payload["changes"])
    if ptype == "cancel_order":
        return domain.commit_cancel_order(con, payload["so_number"])
    if ptype == "reply":
        return "reply-approved"  # drafts only; nothing is actually emailed
    return "ok"


@app.get("/queue")
def queue(request: Request):
    with get_db() as con:
        rows = [dict(r) for r in con.execute("""
            SELECT e.email_id, e.sender_name, e.sender_email, e.subject, e.agent_notes,
                   GROUP_CONCAT(p.type || ' #' || p.proposal_id) pending_props,
                   MAX(a.id) IS NOT NULL AS flagged
            FROM emails e
            LEFT JOIN proposals p ON p.email_id=e.email_id AND p.status='pending'
            LEFT JOIN audit_log a ON a.email_id=e.email_id AND a.action='flag_for_review'
            GROUP BY e.email_id
            HAVING COUNT(p.proposal_id) > 0 OR flagged
            ORDER BY flagged DESC, e.email_id""").fetchall()]
    return templates.TemplateResponse(request, "queue.html", {"rows": rows})


@app.get("/audit")
def audit_view(request: Request):
    with get_db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()]
    return templates.TemplateResponse(request, "audit.html", {"rows": rows})


@app.get("/orders")
def orders_view(request: Request):
    with get_db() as con:
        orders = []
        for o in con.execute("SELECT * FROM sales_orders ORDER BY so_number").fetchall():
            d = dict(o)
            d["lines"] = [dict(l) for l in con.execute(
                "SELECT * FROM sales_order_lines WHERE so_number=?",
                (o["so_number"],)).fetchall()]
            orders.append(d)
    return templates.TemplateResponse(request, "orders.html", {"orders": orders})
