"""Demo data: three fictional companies, two demo users each, and each company's v1 policy.

Idempotent: running it again changes nothing (a company that already has an active
policy is skipped, so there are never duplicate chunks or duplicate active versions).
"""
import logging
import os

from app.services.ingestion import ingest_policy

log = logging.getLogger(__name__)

COMPANIES = {"acme": "Acme Technologies", "northstar": "Northstar Finance", "orbit": "Orbit Retail"}
DEMO_PASSWORD = "demo-pass"  # demo only; printed in the README


def seed(container) -> None:
    db, store, s = container.db, container.store, container.settings
    for company_id, name in COMPANIES.items():
        db.add_company(company_id, name)
        db.add_user(f"{company_id}_employee", DEMO_PASSWORD, company_id, "employee")
        db.add_user(f"{company_id}_admin", DEMO_PASSWORD, company_id, "admin")
        if db.get_active_version(company_id):
            continue
        filename = f"{company_id}_transport_policy_v1.md"
        with open(os.path.join(s.policies_dir, filename), encoding="utf-8") as f:
            text = f.read()
        result = ingest_policy(db, store, s, company_id, "v1", filename, text)
        log.info("seeded %s: %s chunks", company_id, result["chunk_count"])
