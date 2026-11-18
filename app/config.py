import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EMAILS_DIR = BASE_DIR / "emails"
DB_PATH = Path(os.environ.get("AGENT_DB", BASE_DIR / "agent.db"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Snapshot date of the ERP data pack; used as "today" for date-dependent checks
SNAPSHOT_DATE = os.environ.get("SNAPSHOT_DATE", "2026-11-02")
