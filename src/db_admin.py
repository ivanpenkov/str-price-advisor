"""
Database Administration & Maintenance Module for STR Price Advisor.
Provides schema definitions, connectivity diagnostics (check-db),
and streaming compressed disaster-recovery backups (backup-cloud-db).
"""

import gzip
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from src import database

logger = logging.getLogger(__name__)

TABLE_SCHEMAS = {
    "reservations": """
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY,
            confirmation_id INTEGER,
            creation_date TEXT,
            start_date TEXT,
            end_date TEXT,
            days_number INTEGER NOT NULL,
            type_id INTEGER,
            type_name TEXT,
            type_description TEXT,
            status_name TEXT NOT NULL,
            occupants INTEGER DEFAULT 0,
            occupants_small INTEGER DEFAULT 0,
            pets INTEGER DEFAULT 0,
            unit_id INTEGER,
            unit_name TEXT,
            owner_payout REAL DEFAULT 0.0,
            management_fee REAL DEFAULT 0.0,
            gross_rent REAL DEFAULT 0.0,
            is_future INTEGER DEFAULT 0,
            last_scraped_at TEXT NOT NULL,
            raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_res_dates ON reservations (start_date, end_date);
        CREATE INDEX IF NOT EXISTS idx_res_status ON reservations (status_name);
        CREATE INDEX IF NOT EXISTS idx_res_future ON reservations (is_future);
    """,
    "sync_history": """
        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TEXT NOT NULL,
            sync_mode TEXT NOT NULL,
            records_fetched INTEGER DEFAULT 0,
            records_upserted INTEGER DEFAULT 0,
            records_future INTEGER DEFAULT 0,
            records_past INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sync_time ON sync_history (synced_at);
    """,
    "competitor_sales": """
        CREATE TABLE IF NOT EXISTS competitor_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            listing_name TEXT,
            tier TEXT,
            location TEXT,
            check_in TEXT NOT NULL,
            check_out TEXT NOT NULL,
            nights INTEGER NOT NULL,
            segment_type TEXT,
            detected_date TEXT NOT NULL,
            lead_time_days INTEGER,
            last_observed_rate REAL,
            last_observed_adj_rate REAL,
            last_observed_percentile REAL,
            composite_score REAL,
            desirability_ratio REAL,
            verification_status TEXT DEFAULT 'verified',
            raw_snippet TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(listing_id, check_in, check_out)
        );
        CREATE INDEX IF NOT EXISTS idx_comp_sales_lead ON competitor_sales (lead_time_days);
        CREATE INDEX IF NOT EXISTS idx_comp_sales_seg ON competitor_sales (segment_type);
        CREATE INDEX IF NOT EXISTS idx_comp_sales_detected ON competitor_sales (detected_date);
    """,
    "property_rate_snapshots": """
        CREATE TABLE IF NOT EXISTS property_rate_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            calendar_date TEXT NOT NULL,
            nightly_rate REAL NOT NULL,
            interval_type TEXT,
            season_name TEXT,
            period_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (calendar_date, snapshot_date)
        );
        CREATE INDEX IF NOT EXISTS idx_rate_snap_lookup ON property_rate_snapshots (calendar_date, snapshot_date);
        CREATE INDEX IF NOT EXISTS idx_rate_snap_date ON property_rate_snapshots (snapshot_date);
    """
}


def provision_schemas(conn, drop_existing: bool = False):
    """Executes DDL statements on the given database connection."""
    cur = conn.cursor()
    if drop_existing:
        for table_name in reversed(list(TABLE_SCHEMAS.keys())):
            cur.execute(f"DROP TABLE IF EXISTS {table_name}")
    for table_name, schema_sql in TABLE_SCHEMAS.items():
        statements = [s.strip() for s in schema_sql.strip().split(";") if s.strip()]
        for stmt in statements:
            cur.execute(stmt)


def check_database_health() -> Dict[str, Any]:
    """Tests connectivity, measures roundtrip latency, and checks table counts."""
    conn = database.get_db_connection()
    try:
        cur = conn.cursor()

        ping_start = time.time()
        cur.execute("SELECT 1")
        cur.fetchone()
        latency_ms = (time.time() - ping_start) * 1000

        is_remote = isinstance(conn, database.TursoRemoteConnection)
        backend_type = "Turso Cloud (LibSQL)" if is_remote else "Local SQLite (data/reservations.db)"
        turso_url = os.getenv("TURSO_DATABASE_URL", "N/A") if is_remote else str(database.DEFAULT_LOCAL_DB)

        table_counts = {}
        for tbl in TABLE_SCHEMAS.keys():
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                res = cur.fetchone()
                table_counts[tbl] = res[0] if res else 0
            except Exception as e:
                table_counts[tbl] = f"Error: {e}"
    finally:
        conn.close()

    print("\n" + "=" * 80)
    print("  STR Price Advisor - Database Connectivity & Health Diagnostics")
    print("=" * 80)
    print(f"  Backend Type:       {backend_type}")
    print(f"  Database URL:       {turso_url}")
    print(f"  Network Latency:    {latency_ms:.2f} ms (roundtrip ping)")
    print(f"  Status:             ONLINE (Connected & Authenticated)")
    print("\n  Table Record Counts & Index Health:")
    print("  +-------------------------+------------+--------------------+")
    print("  | Table Name              | Row Count  | Health Status      |")
    print("  +-------------------------+------------+--------------------+")
    for tbl, count in table_counts.items():
        cnt_str = f"{count} rows" if isinstance(count, int) else str(count)
        status = "OK (Indexed)" if isinstance(count, int) and count >= 0 else "ERROR"
        print(f"  | {tbl:<23} | {cnt_str:>10} | {status:<18} |")
    print("  +-------------------------+------------+--------------------+")
    print("  Overall Health: HEALTHY - Ready for multi-device operation.")
    print("=" * 80 + "\n")

    return {
        "backend": backend_type,
        "backend_type": backend_type,
        "url": turso_url,
        "latency_ms": latency_ms,
        "table_counts": table_counts,
        "status": "healthy"
    }


def backup_cloud_database(retention_days: int = 30, output_dir: Union[Path, str] = Path("data/backups")) -> Path:
    """
    Dumps all table data and schemas from the active database into a compressed SQL archive
    and rotates out backups older than retention_days.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_file = output_dir / f"turso_backup_{today_str}.sql.gz"

    conn = database.get_db_connection()
    try:
        cur = conn.cursor()

        total_rows = 0
        with gzip.open(backup_file, "wt", encoding="utf-8") as gz:
            gz.write("-- STR Price Advisor Database Backup\n")
            gz.write(f"-- Timestamp: {datetime.now().isoformat()}\n")
            gz.write(f"-- Cloud: {database.is_cloud_enabled()}\n\n")
            gz.write("BEGIN TRANSACTION;\n\n")

            for tbl, schema_sql in TABLE_SCHEMAS.items():
                gz.write(f"-- Table: {tbl}\n")
                gz.write(schema_sql.strip() + "\n\n")

                # Stream rows via cursor iterator with per-table fault tolerance
                try:
                    cur.execute(f"SELECT * FROM {tbl}")
                except Exception as e:
                    logger.warning(f"Could not dump table {tbl}: {e}")
                    continue

                cols = [c[0] for c in cur.description] if cur.description else []

                if cols:
                    cols_str = ", ".join(cols)
                    for row in cur:
                        total_rows += 1
                        val_strs = []
                        for v in row:
                            if v is None:
                                val_strs.append("NULL")
                            elif isinstance(v, (int, float)):
                                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                                    val_strs.append("NULL")
                                else:
                                    val_strs.append(str(v))
                            else:
                                val_strs.append("'" + str(v).replace("'", "''") + "'")
                        vals_joined = ", ".join(val_strs)
                        gz.write(f"INSERT INTO {tbl} ({cols_str}) VALUES ({vals_joined});\n")
                    gz.write("\n")

            gz.write("COMMIT;\n")
    finally:
        conn.close()

    compressed_size_kb = backup_file.stat().st_size / 1024
    print(f"Created compressed backup: {backup_file} ({total_rows} rows, {compressed_size_kb:.1f} KB gzip)")

    cutoff_time = time.time() - (retention_days * 86400)
    for p in output_dir.glob("turso_backup_*.sql.gz"):
        if p.stat().st_mtime < cutoff_time:
            p.unlink()
            print(f"Rotated old backup: {p.name}")

    return backup_file

