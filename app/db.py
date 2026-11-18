import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
-- ERP tables (mirror of the data pack, typed columns added)
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT, payment_terms TEXT,
    credit_limit REAL, open_balance REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS customer_contacts (
    customer_id TEXT, name TEXT, email TEXT, role TEXT
);
CREATE TABLE IF NOT EXISTS ship_to_addresses (
    ship_to_id TEXT PRIMARY KEY, customer_id TEXT, name TEXT,
    address TEXT, is_default INTEGER
);
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY, name TEXT, category TEXT, sell_uom TEXT,
    pack TEXT, list_price REAL, status TEXT, replaced_by TEXT
);
CREATE TABLE IF NOT EXISTS customer_prices (
    customer_id TEXT, sku TEXT, unit_price REAL,
    valid_from TEXT, valid_to TEXT
);
CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY, on_hand REAL, allocated REAL, available REAL
);
CREATE TABLE IF NOT EXISTS incoming_supply (
    supplier_po TEXT, sku TEXT, qty REAL, expected_date TEXT
);
CREATE TABLE IF NOT EXISTS sales_orders (
    so_number TEXT PRIMARY KEY, customer_id TEXT, ship_to_id TEXT,
    customer_po TEXT, order_date TEXT, requested_date TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS sales_order_lines (
    so_number TEXT, line_no INTEGER, sku TEXT,
    qty_ordered REAL, qty_shipped REAL, unit_price REAL,
    PRIMARY KEY (so_number, line_no)
);

-- Agent tables
CREATE TABLE IF NOT EXISTS emails (
    email_id TEXT PRIMARY KEY,
    message_id TEXT, sender_name TEXT, sender_email TEXT,
    subject TEXT, date TEXT, body TEXT,
    status TEXT DEFAULT 'pending',          -- pending|processed|skipped|error
    classification TEXT,                    -- new_order|order_change|order_cancel|inquiry|spam|auto_reply|other
    agent_notes TEXT, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT REFERENCES emails(email_id),
    type TEXT,            -- create_order|change_order|cancel_order|reply|skip
    status TEXT DEFAULT 'pending',  -- pending|approved|rejected|edited_approved|committed|superseded
    payload TEXT,         -- JSON: order lines, reasoning, check results
    reason TEXT,          -- agent's explanation
    created_at TEXT DEFAULT (datetime('now')),
    decided_at TEXT, decided_by TEXT, decision_note TEXT,
    committed_ref TEXT    -- e.g. new SO number
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    actor TEXT,           -- 'agent' | 'human:<name>' | 'system'
    action TEXT,          -- tool name or decision
    email_id TEXT, proposal_id INTEGER,
    detail TEXT           -- JSON
);
"""


def init_db(path=None):
    path = path or DB_PATH
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.commit()
    return con


@contextmanager
def get_db(path=None):
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def audit(con, actor, action, email_id=None, proposal_id=None, detail=None):
    import json
    con.execute(
        "INSERT INTO audit_log (actor, action, email_id, proposal_id, detail) VALUES (?,?,?,?,?)",
        (actor, action, email_id, proposal_id,
         json.dumps(detail) if detail is not None else None),
    )
