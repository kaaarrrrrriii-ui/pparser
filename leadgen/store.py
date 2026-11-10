from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .model import branch_key, website


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS companies (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    synced_revision INTEGER NOT NULL DEFAULT 0, crm_id TEXT, error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    source TEXT NOT NULL, source_id TEXT NOT NULL, company_id TEXT NOT NULL,
                    data TEXT NOT NULL, seen_at TEXT NOT NULL,
                    PRIMARY KEY(source, source_id),
                    FOREIGN KEY(company_id) REFERENCES companies(id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_records (
                    job_id TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL,
                    company_id TEXT NOT NULL,
                    PRIMARY KEY(job_id,source,source_id)
                );
            """)
            if "removed_from_crm" not in {row["name"] for row in db.execute("PRAGMA table_info(companies)")}:
                # Set when a manager deletes the lead in the CRM; such companies are never sent again.
                db.execute("ALTER TABLE companies ADD COLUMN removed_from_crm INTEGER NOT NULL DEFAULT 0")
            # Canonicalize legacy URLs and backfill per-source metrics.
            for observation in db.execute("SELECT source,source_id,data FROM observations").fetchall():
                record = json.loads(observation["data"])
                previous_record = record.copy()
                record["source_url"] = website(record.get("source_url"))
                for key in ("websites", "social"):
                    record[key] = sorted({value for item in record.get(key, []) if (value := website(item))})
                if record != previous_record:
                    db.execute("UPDATE observations SET data=? WHERE source=? AND source_id=?",
                               (json.dumps(record, ensure_ascii=False), observation["source"], observation["source_id"]))
            for company in db.execute("SELECT id,data FROM companies").fetchall():
                observations = [json.loads(row[0]) for row in db.execute(
                    "SELECT data FROM observations WHERE company_id=? ORDER BY seen_at,source,source_id",
                    (company["id"],))]
                if not observations:
                    continue
                metrics = {row["source"]: {"rating": row.get("rating"), "review_count": row.get("reviews")}
                           for row in observations}
                data = json.loads(company["data"])
                previous_payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
                data["sources"] = [{"source": row["source"], "id": row["source_id"],
                                    "url": website(record.get("source_url"))}
                                   for row in db.execute(
                                       "SELECT source,source_id,data FROM observations WHERE company_id=? "
                                       "ORDER BY seen_at,source,source_id", (company["id"],))
                                   for record in [json.loads(row["data"])]]
                data["websites"] = sorted({value for row in observations
                                           for item in row.get("websites", []) if (value := website(item))})
                data["social"] = sorted({value for row in observations
                                         for item in row.get("social", []) if (value := website(item))})
                data["website_status"] = "Present" if data["websites"] else "NotListed"
                data["source_metrics"] = metrics
                payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
                if payload == previous_payload:
                    continue
                db.execute("UPDATE companies SET data=?,revision=revision+1,updated_at=? WHERE id=?",
                           (payload, now(), company["id"]))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, record, job_id=None):
        """Commit every company before the collector makes another request."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT company_id FROM observations WHERE source=? AND source_id=?",
                             (record["source"], record["source_id"])).fetchone()
            company_id = old[0] if old else None
            if old:
                previous = json.loads(db.execute("SELECT data FROM observations WHERE source=? AND source_id=?",
                                                (record["source"], record["source_id"])).fetchone()[0])
                record = dict(record)
                # Search snippets may contain fewer contacts than a previous full card.
                for key in ("phones", "emails", "websites", "social"):
                    record[key] = sorted(set(previous[key]) | set(record[key]))
                for key in ("city", "address", "category", "source_url"):
                    record[key] = record[key] or previous[key]
                record["gis_created_at"] = record["gis_created_at"] or previous.get("gis_created_at", "")
                for key in ("rating", "reviews"):
                    if record.get(key) is None:
                        record[key] = previous.get(key)
            if not company_id and record["address"]:
                record_branch = branch_key(record["city"], record["address"])
                # Require a matching branch address. A chain's shared phone/site alone is insufficient.
                for row in db.execute("SELECT id,data FROM companies"):
                    data = json.loads(row["data"])
                    same_branch = branch_key(data["city"], data["address"]) == record_branch
                    same_name = data["name"].casefold() == record["name"].casefold()
                    same_phone = bool(set(data["phones"]) & set(record["phones"]))
                    if same_branch and (same_name or same_phone):
                        company_id = row["id"]
                        break
            if not company_id:
                company_id = uuid.uuid4().hex
                db.execute("INSERT INTO companies(id,data,updated_at) VALUES(?,?,?)",
                           (company_id, "{}", now()))
            db.execute("""INSERT INTO observations VALUES(?,?,?,?,?)
                          ON CONFLICT(source,source_id) DO UPDATE SET data=excluded.data,seen_at=excluded.seen_at""",
                       (record["source"], record["source_id"], company_id, json.dumps(record, ensure_ascii=False), now()))
            records = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM observations WHERE company_id=? ORDER BY seen_at,source,source_id", (company_id,))]
            merged = dict(records[0])
            for item in records[1:]:
                for key in ("name", "city", "address", "category", "gis_created_at"):
                    if item.get(key) is not None and item.get(key) != "":
                        merged[key] = item[key]
                for key in ("phones", "emails", "websites", "social"):
                    merged[key] = sorted(set(merged[key]) | set(item[key]))
            merged["website_status"] = "Present" if merged["websites"] else "NotListed"
            merged["sources"] = [{"source": r["source"], "id": r["source_id"], "url": r["source_url"]} for r in records]
            merged["source_metrics"] = {
                source: {"rating": record.get("rating"), "review_count": record.get("reviews")}
                for source, record in ((r["source"], r) for r in records)
            }
            payload = json.dumps(merged, ensure_ascii=False, sort_keys=True)
            db.execute("""UPDATE companies SET revision=revision+CASE WHEN data!=? THEN 1 ELSE 0 END,
                          data=?,updated_at=?,error=NULL WHERE id=?""", (payload, payload, now(), company_id))
            if job_id:
                db.execute("INSERT OR IGNORE INTO job_records VALUES(?,?,?,?)",
                           (job_id, record["source"], record["source_id"], company_id))
            return company_id

    def job_records(self, job_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM job_records WHERE job_id=?", (job_id,))]

    def companies(self, pending=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM companies" + (" WHERE revision>synced_revision" if pending else "") +
                              " ORDER BY updated_at DESC").fetchall()
            return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def synced(self, company_id, revision, crm_id):
        with self.connect() as db:
            db.execute("UPDATE companies SET synced_revision=?,crm_id=?,error=NULL WHERE id=?",
                       (revision, crm_id, company_id))

    def filtered(self, company_id, revision, reason):
        """Keep the company locally, acknowledge this revision and record why it was not sent."""
        with self.connect() as db:
            db.execute("UPDATE companies SET synced_revision=?,error=? WHERE id=?", (revision, reason, company_id))

    def removed(self, company_id, revision, reason):
        """Remember that the lead was deleted in the CRM so later collections do not recreate it."""
        with self.connect() as db:
            db.execute("UPDATE companies SET synced_revision=?,crm_id=NULL,error=?,removed_from_crm=1 WHERE id=?",
                       (revision, reason, company_id))

    def sync_error(self, company_id, error):
        with self.connect() as db:
            db.execute("UPDATE companies SET error=? WHERE id=?", (str(error), company_id))

    def create_job(self, config):
        job_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,config,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (job_id, json.dumps(config, ensure_ascii=False), "queued", now(), now()))
        return job_id

    def jobs(self):
        with self.connect() as db:
            return [dict(r) | {"config": json.loads(r["config"])} for r in
                    db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def update_job(self, job_id, status, count=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,count=COALESCE(?,count),error=?,updated_at=? WHERE id=?",
                       (status, count, error, now(), job_id))
