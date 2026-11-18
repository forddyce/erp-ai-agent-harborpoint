"""Load the ERP data pack (CSVs) and the order inbox (.eml files) into agent.db."""
import csv
import email
import email.policy
import re
from email.utils import parseaddr
from pathlib import Path

from .config import DATA_DIR, EMAILS_DIR
from .db import init_db

CSV_TABLES = {
    "customers": ["customer_id", "name", "payment_terms", "credit_limit", "open_balance", "status"],
    "customer_contacts": ["customer_id", "name", "email", "role"],
    "ship_to_addresses": ["ship_to_id", "customer_id", "name", "address", "is_default"],
    "products": ["sku", "name", "category", "sell_uom", "pack", "list_price", "status", "replaced_by"],
    "customer_prices": ["customer_id", "sku", "unit_price", "valid_from", "valid_to"],
    "inventory": ["sku", "on_hand", "allocated", "available"],
    "incoming_supply": ["supplier_po", "sku", "qty", "expected_date"],
    "sales_orders": ["so_number", "customer_id", "ship_to_id", "customer_po", "order_date", "requested_date", "status"],
    "sales_order_lines": ["so_number", "line_no", "sku", "qty_ordered", "qty_shipped", "unit_price"],
}


def _num(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return v


def load_erp(con, data_dir=DATA_DIR):
    for table, cols in CSV_TABLES.items():
        con.execute(f"DELETE FROM {table}")
        path = Path(data_dir) / f"{table}.csv"
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                con.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [_num(row.get(c)) for c in cols],
                )


def _body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_content()
        return ""
    return msg.get_content()


def load_emails(con, emails_dir=EMAILS_DIR):
    con.execute("DELETE FROM emails")
    for path in sorted(Path(emails_dir).glob("*.eml")):
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        name, addr = parseaddr(msg["From"] or "")
        email_id = re.sub(r"\..*$", "", path.stem)
        con.execute(
            """INSERT INTO emails
               (email_id, message_id, sender_name, sender_email, subject, date, body)
               VALUES (?,?,?,?,?,?,?)""",
            (email_id, msg["Message-ID"] or "", name, addr,
             msg["Subject"] or "", msg["Date"] or "", _body(msg)),
        )


def main():
    con = init_db()
    load_erp(con)
    load_emails(con)
    con.commit()
    n_emails = con.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    n_products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"Ingested {n_emails} emails, {n_products} products into agent.db")


if __name__ == "__main__":
    main()
