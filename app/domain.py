"""ERP business rules: pricing, credit, inventory, order editability."""
import json
import sqlite3
from .config import SNAPSHOT_DATE


def price_for(con, customer_id, sku, order_date=None):
    """Contract price if valid on order_date, else list price. Returns dict."""
    order_date = order_date or SNAPSHOT_DATE
    row = con.execute(
        """SELECT unit_price FROM customer_prices
           WHERE customer_id=? AND sku=? AND valid_from<=? AND valid_to>=?""",
        (customer_id, sku, order_date, order_date),
    ).fetchone()
    if row:
        return {"unit_price": row[0], "price_source": "contract"}
    prod = con.execute("SELECT list_price FROM products WHERE sku=?", (sku,)).fetchone()
    if not prod:
        return None
    return {"unit_price": prod[0], "price_source": "list"}


def credit_check(con, customer_id, order_total):
    """Returns (ok, reason). README: block CREDIT_HOLD and limit breaches."""
    c = con.execute(
        "SELECT name, status, credit_limit, open_balance FROM customers WHERE customer_id=?",
        (customer_id,),
    ).fetchone()
    if not c:
        return False, f"unknown customer {customer_id}"
    name, status, limit_, balance = c
    if status == "CREDIT_HOLD":
        return False, f"{name} is on CREDIT_HOLD"
    if limit_ and balance + order_total > limit_:
        return (False, f"{name}: open_balance {balance:.0f} + order {order_total:.0f} "
                       f"= {balance + order_total:.0f} exceeds credit_limit {limit_:.0f}")
    return True, "ok"


def validate_order_lines(con, customer_id, lines, order_date=None):
    """Check a proposed order: SKU status, whole units, pricing, stock, credit.
    `lines`: [{"sku": ..., "qty": ...}]
    Returns {"lines": [...], "total": ..., "ok": bool, "issues": [...]}.
    Each line gets: unit_price, price_source, line_total, issues[].
    """
    order_date = order_date or SNAPSHOT_DATE
    out_lines, issues, total = [], [], 0.0
    for ln in lines:
        sku, qty = ln["sku"], ln["qty"]
        line_issues = []
        prod = con.execute(
            "SELECT name, sell_uom, list_price, status, replaced_by FROM products WHERE sku=?",
            (sku,),
        ).fetchone()
        priced = {"sku": sku, "qty": qty}
        if not prod:
            line_issues.append(f"unknown SKU {sku}")
            priced.update(name=None, unit_price=None, line_total=None)
        else:
            name, uom, list_price, status, replaced_by = prod
            priced["name"] = name
            priced["sell_uom"] = uom
            if status == "DISCONTINUED":
                line_issues.append(
                    f"{sku} is DISCONTINUED" +
                    (f" (replaced by {replaced_by})" if replaced_by else ""))
                priced["replacement_sku"] = replaced_by or None
            if float(qty) != int(qty):
                line_issues.append(f"qty {qty} not a whole selling unit")
            p = price_for(con, customer_id, sku, order_date)
            if p is None:
                line_issues.append(f"no price for {sku}")
            else:
                priced.update(unit_price=p["unit_price"], price_source=p["price_source"],
                              line_total=round(p["unit_price"] * qty, 2))
                total += priced["line_total"] or 0
            inv = con.execute("SELECT available FROM inventory WHERE sku=?", (sku,)).fetchone()
            if not inv:
                line_issues.append(f"no inventory record for {sku}")
                priced["available"] = None
            else:
                priced["available"] = inv[0]
                if inv[0] < qty:
                    sup = con.execute(
                        """SELECT SUM(qty), MIN(expected_date) FROM incoming_supply
                           WHERE sku=? AND expected_date>=?""", (sku, order_date)).fetchone()
                    priced["incoming"] = {"qty": sup[0] or 0, "expected": sup[1]}
                    line_issues.append(
                        f"only {inv[0]:.0f} {uom} available" +
                        (f", {sup[0]:.0f} expected {sup[1]}" if sup[0] else ""))
        priced["issues"] = line_issues
        issues.extend(line_issues)
        out_lines.append(priced)

    ok, reason = credit_check(con, customer_id, total)
    if not ok:
        issues.append(reason)
    return {"lines": out_lines, "total": round(total, 2), "ok": not issues,
            "issues": issues, "credit": {"ok": ok, "reason": reason}}


EDIT_RULES = {
    "OPEN": "fully editable",
    "PARTIALLY_SHIPPED": "only unshipped quantities can change",
    "SHIPPED": "cannot be edited",
}


def check_order_edit(con, so_number):
    """Return dict describing what may change on this order."""
    so = con.execute("SELECT status, customer_id FROM sales_orders WHERE so_number=?",
                     (so_number,)).fetchone()
    if not so:
        return {"exists": False, "editable": False, "reason": f"no order {so_number}"}
    status, customer_id = so
    editable = status in ("OPEN", "PARTIALLY_SHIPPED")
    lines = [dict(l) for l in con.execute(
        "SELECT line_no, sku, qty_ordered, qty_shipped, unit_price FROM sales_order_lines WHERE so_number=?",
        (so_number,)).fetchall()]
    for l in lines:
        l["qty_open"] = l["qty_ordered"] - l["qty_shipped"]
    return {"exists": True, "status": status, "editable": editable,
            "rule": EDIT_RULES.get(status, "unknown status"),
            "customer_id": customer_id, "lines": lines}


def commit_order(con, customer_id, ship_to_id, customer_po, lines, requested_date=None,
                 order_date=None):
    """Create a sales order after human approval. Allocates inventory.
    Returns new so_number."""
    order_date = order_date or SNAPSHOT_DATE
    n = con.execute(
        "SELECT MAX(CAST(SUBSTR(so_number,4) AS INT)) FROM sales_orders").fetchone()[0] or 24000
    so_number = f"SO-{n + 1}"
    con.execute(
        """INSERT INTO sales_orders
           (so_number, customer_id, ship_to_id, customer_po, order_date, requested_date, status)
           VALUES (?,?,?,?,?,?,'OPEN')""",
        (so_number, customer_id, ship_to_id, customer_po, order_date, requested_date))
    for i, ln in enumerate(lines, 1):
        con.execute(
            "INSERT INTO sales_order_lines VALUES (?,?,?,?,0,?)",
            (so_number, i, ln["sku"], ln["qty"], ln["unit_price"]))
        con.execute(
            "UPDATE inventory SET allocated = allocated + ?, available = available - ? WHERE sku=?",
            (ln["qty"], ln["qty"], ln["sku"]))
    return so_number


def commit_order_change(con, so_number, changes):
    """Apply approved changes to an editable order.
    `changes`: {"cancel_lines":[line_no], "qty_changes":{line_no:new_qty}, "add_lines":[...]}
    Returns list of human-readable actions taken."""
    info = check_order_edit(con, so_number)
    if not info["editable"]:
        raise ValueError(f"{so_number} is {info['status']}: {info['rule']}")
    actions = []
    for ln in changes.get("cancel_lines", []):
        row = con.execute(
            "SELECT sku, qty_ordered, qty_shipped FROM sales_order_lines WHERE so_number=? AND line_no=?",
            (so_number, ln)).fetchone()
        if not row:
            raise ValueError(f"line {ln} not found on {so_number}")
        sku, ordered, shipped = row
        if shipped > 0:
            raise ValueError(f"line {ln} has {shipped} shipped; cannot cancel")
        con.execute("UPDATE inventory SET allocated=allocated-?, available=available+? WHERE sku=?",
                    (ordered, ordered, sku))
        con.execute("DELETE FROM sales_order_lines WHERE so_number=? AND line_no=?",
                    (so_number, ln))
        actions.append(f"cancelled line {ln} ({sku} x{ordered})")
    for line_no, new_qty in (changes.get("qty_changes") or {}).items():
        row = con.execute(
            "SELECT sku, qty_ordered, qty_shipped FROM sales_order_lines WHERE so_number=? AND line_no=?",
            (so_number, line_no)).fetchone()
        if not row:
            raise ValueError(f"line {line_no} not found")
        sku, ordered, shipped = row
        if new_qty < shipped:
            raise ValueError(
                f"line {line_no}: {shipped} already shipped; qty cannot go below that")
        delta = new_qty - ordered
        con.execute("UPDATE inventory SET allocated=allocated+?, available=available-? WHERE sku=?",
                    (delta, delta, sku))
        con.execute("UPDATE sales_order_lines SET qty_ordered=? WHERE so_number=? AND line_no=?",
                    (new_qty, so_number, line_no))
        actions.append(f"line {line_no} {sku} qty {ordered} -> {new_qty}")
    for ln in changes.get("add_lines", []):
        max_ln = con.execute(
            "SELECT MAX(line_no) FROM sales_order_lines WHERE so_number=?",
            (so_number,)).fetchone()[0] or 0
        con.execute("INSERT INTO sales_order_lines VALUES (?,?,?,?,0,?)",
                    (so_number, max_ln + 1, ln["sku"], ln["qty"], ln["unit_price"]))
        con.execute("UPDATE inventory SET allocated=allocated+?, available=available-? WHERE sku=?",
                    (ln["qty"], ln["qty"], ln["sku"]))
        actions.append(f"added line {ln['sku']} x{ln['qty']}")
    if not con.execute("SELECT 1 FROM sales_order_lines WHERE so_number=?",
                       (so_number,)).fetchone():
        con.execute("UPDATE sales_orders SET status='CANCELLED' WHERE so_number=?", (so_number,))
        actions.append(f"{so_number} has no lines left — marked CANCELLED")
    return actions


def commit_cancel_order(con, so_number):
    info = check_order_edit(con, so_number)
    if not info["exists"]:
        raise ValueError(info["reason"])
    if info["status"] == "SHIPPED":
        raise ValueError(f"{so_number} already SHIPPED — cannot cancel")
    if info["status"] == "PARTIALLY_SHIPPED":
        # cancel only unshipped remainder
        return commit_order_change(con, so_number, {
            "qty_changes": {l["line_no"]: l["qty_shipped"] for l in info["lines"]}})
    return commit_order_change(con, so_number,
                               {"cancel_lines": [l["line_no"] for l in info["lines"]]})


def create_proposal(con, email_id, ptype, payload, reason):
    cur = con.execute(
        "INSERT INTO proposals (email_id, type, payload, reason) VALUES (?,?,?,?)",
        (email_id, ptype, json.dumps(payload), reason))
    return cur.lastrowid
