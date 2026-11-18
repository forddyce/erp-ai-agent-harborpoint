import unittest

from app import domain
from app.db import init_db
from app.ingest import load_erp


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.con = init_db(":memory:")
        load_erp(self.con, )

    # --- pricing ---
    def test_contract_price_within_validity(self):
        p = domain.price_for(self.con, "C001", "HPS-1401", "2026-11-02")
        self.assertEqual(p["unit_price"], 59.0)
        self.assertEqual(p["price_source"], "contract")

    def test_list_price_when_contract_expired(self):
        # C002's HPS-1403 contract ended 2026-09-30
        p = domain.price_for(self.con, "C002", "HPS-1403", "2026-11-02")
        self.assertEqual(p["price_source"], "list")

    # --- credit ---
    def test_credit_hold_blocks(self):
        held = self.con.execute(
            "SELECT customer_id FROM customers WHERE status='CREDIT_HOLD' LIMIT 1"
        ).fetchone()
        if held:  # only assert if fixture contains a held customer
            ok, reason = domain.credit_check(self.con, held[0], 1)
            self.assertFalse(ok)

    def test_credit_limit_breach(self):
        ok, reason = domain.credit_check(self.con, "C001", 999999)
        self.assertFalse(ok)
        self.assertIn("credit_limit", reason)

    def test_credit_ok(self):
        ok, _ = domain.credit_check(self.con, "C001", 100)
        self.assertTrue(ok)

    # --- order validation ---
    def test_discontinued_flagged(self):
        disc = self.con.execute(
            "SELECT sku, replaced_by FROM products WHERE status='DISCONTINUED' LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(disc, "fixture should contain a discontinued SKU")
        r = domain.validate_order_lines(self.con, "C001", [{"sku": disc[0], "qty": 1}])
        self.assertFalse(r["ok"])
        self.assertTrue(any("DISCONTINUED" in i for i in r["issues"]))
        self.assertEqual(r["lines"][0].get("replacement_sku"), disc[1] or None)

    def test_fractional_qty_flagged(self):
        r = domain.validate_order_lines(self.con, "C001", [{"sku": "HPS-1408", "qty": 1.5}])
        self.assertFalse(r["ok"])

    def test_insufficient_stock_flagged(self):
        r = domain.validate_order_lines(self.con, "C001", [{"sku": "HPS-1408", "qty": 9999}])
        self.assertFalse(r["ok"])
        self.assertTrue(any("available" in i for i in r["issues"]))

    def test_unknown_sku_flagged(self):
        r = domain.validate_order_lines(self.con, "C001", [{"sku": "NOPE-1", "qty": 1}])
        self.assertFalse(r["ok"])

    # --- order edits ---
    def test_shipped_not_editable(self):
        info = domain.check_order_edit(self.con, "SO-24040")
        self.assertEqual(info["status"], "SHIPPED")
        self.assertFalse(info["editable"])

    def test_partially_shipped_qty_floor(self):
        ps = self.con.execute(
            "SELECT so_number FROM sales_orders WHERE status='PARTIALLY_SHIPPED' LIMIT 1"
        ).fetchone()
        if not ps:
            self.skipTest("no partially shipped order in fixture")
        info = domain.check_order_edit(self.con, ps[0])
        line = next(l for l in info["lines"] if l["qty_shipped"] > 0)
        with self.assertRaises(ValueError):
            domain.commit_order_change(
                self.con, ps[0],
                {"qty_changes": {line["line_no"]: line["qty_shipped"] - 1}})

    # --- commits ---
    def test_commit_order_allocates_inventory(self):
        before = self.con.execute(
            "SELECT available FROM inventory WHERE sku='HPS-1408'").fetchone()[0]
        so = domain.commit_order(self.con, "C001", "S001", "PO-1",
                                 [{"sku": "HPS-1408", "qty": 2, "unit_price": 45.0}])
        after = self.con.execute(
            "SELECT available FROM inventory WHERE sku='HPS-1408'").fetchone()[0]
        self.assertEqual(before - 2, after)
        self.assertTrue(so.startswith("SO-"))

    def test_cancel_restocks_inventory(self):
        open_so = self.con.execute(
            "SELECT so_number FROM sales_orders WHERE status='OPEN' LIMIT 1"
        ).fetchone()
        if not open_so:
            self.skipTest("no open order")
        so = open_so[0]
        l = self.con.execute(
            "SELECT sku, qty_ordered FROM sales_order_lines WHERE so_number=? LIMIT 1",
            (so,)).fetchone()
        before = self.con.execute(
            "SELECT available FROM inventory WHERE sku=?", (l[0],)).fetchone()[0]
        domain.commit_cancel_order(self.con, so)
        after = self.con.execute(
            "SELECT available FROM inventory WHERE sku=?", (l[0],)).fetchone()[0]
        self.assertEqual(before + l[1], after)

    def test_cancel_shipped_rejected(self):
        with self.assertRaises(ValueError):
            domain.commit_cancel_order(self.con, "SO-24040")


if __name__ == "__main__":
    unittest.main()
