"""Incremental Nseries discovery with periodic complete catalog audits."""
from datetime import datetime, timedelta, timezone

try:
    from .metadata_common import listing_fingerprint, utc_now
except ImportError:
    from metadata_common import listing_fingerprint, utc_now


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


class CollectionPolicy:
    default_delay = 2.0
    audit_days = 28
    quiet_pages = 3
    catalog_fields = ("id", "title", "author", "synopsis", "age", "episodes", "complete", "updated")

    def begin_catalog(self, state):
        policy = state["progress"].setdefault("nseries_policy", {})
        now = datetime.now(timezone.utc)
        baseline = state["coverage"].get("has_complete_baseline", False)
        if baseline and not policy.get("audit_due_at"):
            anchor = stamp(state["progress"].get("pass_started_at")) or now
            policy["audit_due_at"] = (anchor + timedelta(days=self.audit_days)).isoformat()
        due = stamp(policy.get("audit_due_at"))
        policy.update(mode="incremental" if baseline and due and now < due else "full", started_at=utc_now())

    def observe_catalog_page(self, state, partition, cursor, page, result):
        if state["progress"].get("nseries_policy", {}).get("mode") != "incremental":
            return False
        unchanged = bool(result.records) and not result.skipped_rows
        for row in result.records:
            old = state["records"].get(row["id"], {})
            # Failed optional batches and legacy rows cannot establish a quiet window.
            if (not row.get("nseries_batch_complete")
                    or old.get("nseries_catalog_fingerprint") != row.get("nseries_catalog_fingerprint")
                    or not old.get("synopsis")
                    or (row.get("age") != 19 and not old.get("history", {}).get("last_success"))):
                unchanged = False
                break
        previous = cursor.get("quiet_last_page", 0)
        if page <= previous:
            if not unchanged:
                cursor["quiet_count"] = 0
            return False
        count = cursor.get("quiet_count", 0) if previous == page - 1 else 0
        cursor.update(quiet_count=count + 1 if unchanged else 0, quiet_last_page=page)
        return cursor["quiet_count"] >= self.quiet_pages and result.next_page is not None

    @staticmethod
    def detail_needed(old, item, default):
        age = item.get("age") if item.get("age") is not None else old.get("age")
        return False if age == 19 else default

    def finish_catalog_pass(self, state, successful):
        policy = state["progress"].get("nseries_policy", {})
        if not successful or not policy:
            return False
        policy["finished_at"] = utc_now()
        catalog = state["coverage"]["catalog"]
        if catalog.get("discovery_complete"):
            policy["last_full_completed_at"] = utc_now()
            policy["audit_due_at"] = (datetime.now(timezone.utc) + timedelta(days=self.audit_days)).isoformat()
        catalog["scan_mode"] = policy["mode"]
        return True

    def catalog_fingerprint(self, row):
        return listing_fingerprint(row, self.catalog_fields)
