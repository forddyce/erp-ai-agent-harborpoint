"""Tools the Gemini agent can call. Read tools are safe; every write creates a
`proposals` row that a human must approve before it touches the ERP."""
import json
from . import domain
from .db import audit

ACTOR = "agent"


class Tools:
    def __init__(self, con, email_id):
        self.con = con
        self.email_id = email_id

    def _log(self, name, args, result):
        audit(self.con, ACTOR, name, email_id=self.email_id,
              detail={"args": args, "result": result})
        return result

    # ---------- read-only tools ----------

    def search_customer(self, query: str) -> list:
        """Find customers by name fragment or contact email. Use this first to
        identify who sent the email. Returns customers with status/credit fields."""
        q = f"%{query}%"
        rows = self.con.execute(
            """SELECT DISTINCT c.customer_id, c.name, c.status, c.credit_limit, c.open_balance
               FROM customers c
               LEFT JOIN customer_contacts k ON k.customer_id = c.customer_id
               WHERE c.name LIKE ? OR c.customer_id LIKE ? OR k.email LIKE ? OR k.name LIKE ?""",
            (q, q, q, q)).fetchall()
        return self._log("search_customer", {"query": query}, [dict(r) for r in rows])

    def get_customer(self, customer_id: str) -> dict:
        """Full customer record: contacts and ship-to addresses."""
        c = self.con.execute("SELECT * FROM customers WHERE customer_id=?",
                             (customer_id,)).fetchone()
        if not c:
            return self._log("get_customer", {"customer_id": customer_id}, None)
        d = dict(c)
        d["contacts"] = [dict(r) for r in self.con.execute(
            "SELECT name, email, role FROM customer_contacts WHERE customer_id=?",
            (customer_id,)).fetchall()]
        d["ship_to"] = [dict(r) for r in self.con.execute(
            "SELECT ship_to_id, name, address, is_default FROM ship_to_addresses WHERE customer_id=?",
            (customer_id,)).fetchall()]
        return self._log("get_customer", {"customer_id": customer_id}, d)

    def search_product(self, query: str) -> list:
        """Find products by SKU or name fragment. Returns sku, name, sell_uom
        (CS=case, EA=each), list_price, status, replaced_by."""
        q = f"%{query}%"
        rows = self.con.execute(
            """SELECT sku, name, category, sell_uom, pack, list_price, status, replaced_by
               FROM products WHERE sku LIKE ? OR name LIKE ? OR category LIKE ?""",
            (q, q, q)).fetchall()
        return self._log("search_product", {"query": query}, [dict(r) for r in rows])

    def check_inventory(self, sku: str) -> dict:
        """Stock for a SKU: on_hand, allocated, available, plus incoming supply."""
        inv = self.con.execute("SELECT * FROM inventory WHERE sku=?", (sku,)).fetchone()
        sup = [dict(r) for r in self.con.execute(
            "SELECT supplier_po, qty, expected_date FROM incoming_supply WHERE sku=? ORDER BY expected_date",
            (sku,)).fetchall()]
        r = dict(inv) if inv else None
        if r:
            r["incoming_supply"] = sup
        return self._log("check_inventory", {"sku": sku}, r)

    def get_price(self, customer_id: str, sku: str) -> dict:
        """Unit price for a customer+SKU on the snapshot date. A valid contract
        price overrides list price."""
        return self._log("get_price", {"customer_id": customer_id, "sku": sku},
                         domain.price_for(self.con, customer_id, sku))

    def find_orders(self, customer_id: str = None, so_number: str = None,
                    customer_po: str = None) -> list:
        """Look up sales orders by SO number, customer PO, or customer id.
        Returns orders with their lines and editability status."""
        if so_number:
            rows = self.con.execute("SELECT * FROM sales_orders WHERE so_number=?",
                                    (so_number,)).fetchall()
        elif customer_po:
            rows = self.con.execute("SELECT * FROM sales_orders WHERE customer_po LIKE ?",
                                    (f"%{customer_po}%",)).fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM sales_orders WHERE customer_id=? ORDER BY order_date DESC",
                (customer_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            info = domain.check_order_edit(self.con, d["so_number"])
            d["lines"] = info.get("lines", [])
            d["editable"] = info.get("editable")
            d["edit_rule"] = info.get("rule")
            out.append(d)
        return self._log("find_orders", {"customer_id": customer_id, "so_number": so_number,
                                         "customer_po": customer_po}, out)

    def validate_order(self, customer_id: str, lines: list) -> dict:
        """Dry-run an order through all business rules: whole-unit check,
        contract pricing, stock availability, credit hold/limit. Call this before
        propose_create_order. lines: [{"sku": "HPS-XXXX", "qty": N}]"""
        r = domain.validate_order_lines(self.con, customer_id, lines)
        return self._log("validate_order", {"customer_id": customer_id, "lines": lines}, r)

    # ---------- proposal tools (human approval required) ----------

    def propose_create_order(self, customer_id: str, ship_to_id: str, lines: list,
                             reasoning: str, customer_po: str = None,
                             requested_date: str = None) -> dict:
        """Propose a new sales order. Always run validate_order first. The order
        is only created if a human approves the proposal.
        lines: [{"sku": "HPS-XXXX", "qty": N}] in whole selling units."""
        check = domain.validate_order_lines(self.con, customer_id, lines, )
        if customer_po:
            dup = self.con.execute(
                """SELECT proposal_id FROM proposals
                   WHERE type='create_order' AND status='pending'
                     AND json_extract(payload,'$.customer_po')=?""",
                (customer_po,)).fetchone()
            if dup:
                check["issues"].append(
                    f"possible duplicate: pending proposal #{dup[0]} has same PO {customer_po}")
                check["ok"] = False
        payload = {"customer_id": customer_id, "ship_to_id": ship_to_id,
                   "customer_po": customer_po, "requested_date": requested_date,
                   "lines": check["lines"], "total": check["total"], "check": check}
        pid = domain.create_proposal(self.con, self.email_id, "create_order",
                                     payload, reasoning)
        r = {"proposal_id": pid, "total": check["total"], "ok": check["ok"],
             "issues": check["issues"]}
        return self._log("propose_create_order",
                         {"customer_id": customer_id, "lines": lines}, r)

    def propose_change_order(self, so_number: str, changes: dict, reasoning: str) -> dict:
        """Propose changes to an existing order.
        changes: {"cancel_lines": [line_no], "qty_changes": {"<line_no>": new_qty},
                  "add_lines": [{"sku","qty"}], "note": "..."}"""
        info = domain.check_order_edit(self.con, so_number)
        payload = {"so_number": so_number, "changes": changes, "current": info}
        status = "editable" if info["editable"] else f"blocked: {info['rule']}"
        pid = domain.create_proposal(self.con, self.email_id, "change_order",
                                     payload, reasoning)
        return self._log("propose_change_order", {"so_number": so_number, "changes": changes},
                         {"proposal_id": pid, "status": status})

    def propose_cancel_order(self, so_number: str, reasoning: str) -> dict:
        """Propose cancelling an order (or its unshipped remainder)."""
        info = domain.check_order_edit(self.con, so_number)
        pid = domain.create_proposal(self.con, self.email_id, "cancel_order",
                                     {"so_number": so_number, "current": info}, reasoning)
        return self._log("propose_cancel_order", {"so_number": so_number},
                         {"proposal_id": pid, "status": info.get("status")})

    def propose_reply(self, body: str, reasoning: str, subject: str = None) -> dict:
        """Propose an email reply to the sender (quote, question, explanation,
        apology...). Nothing is sent without human approval."""
        pid = domain.create_proposal(
            self.con, self.email_id, "reply",
            {"subject": subject, "body": body}, reasoning)
        return self._log("propose_reply", {"subject": subject},
                         {"proposal_id": pid})

    def flag_for_review(self, reason: str) -> dict:
        """Escalate this email to a human when you cannot safely proceed:
        unknown sender, missing info, conflicting instructions, anything risky
        or unusual not covered by other tools."""
        audit(self.con, ACTOR, "flag_for_review", email_id=self.email_id,
              detail={"reason": reason})
        self.con.execute("UPDATE emails SET agent_notes=? WHERE email_id=?",
                         (reason, self.email_id))
        return {"flagged": True}

    def mark_no_action(self, classification: str, reason: str) -> dict:
        """Mark the email as needing no order action: spam, auto-reply, or
        duplicate. classification: spam|auto_reply|duplicate|other"""
        audit(self.con, ACTOR, "mark_no_action", email_id=self.email_id,
              detail={"classification": classification, "reason": reason})
        self.con.execute(
            "UPDATE emails SET status='skipped', classification=?, agent_notes=? WHERE email_id=?",
            (classification, reason, self.email_id))
        return {"skipped": True}

    def all(self):
        return [self.search_customer, self.get_customer, self.search_product,
                self.check_inventory, self.get_price, self.find_orders,
                self.validate_order, self.propose_create_order,
                self.propose_change_order, self.propose_cancel_order,
                self.propose_reply, self.flag_for_review, self.mark_no_action]
