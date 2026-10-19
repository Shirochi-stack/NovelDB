"""Incremental public catalog policy; full audits retain long-tail coverage."""

from datetime import datetime, timedelta, timezone

try:
    from .metadata_common import listing_fingerprint, utc_now
except ImportError:
    from metadata_common import listing_fingerprint, utc_now


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


class IncrementalPolicy:
    default_delay = 2.0
    audit_days = 28
    quiet_pages = 3
    changed_detail_days = 7

    def begin_catalog(self, state):
        policy = state["progress"].setdefault("nweb_policy", {})
        now = datetime.now(timezone.utc)
        baseline = state["coverage"].get("has_complete_baseline", False)
        if baseline and not policy.get("audit_due_at"):
            # Adopt an existing complete baseline without claiming a new audit.
            anchor = stamp(state["progress"].get("pass_started_at")) or now
            policy["baseline_adopted_at"] = utc_now()
            policy["audit_due_at"] = (anchor + timedelta(days=self.audit_days)).isoformat()
        due = stamp(policy.get("audit_due_at"))
        policy["mode"] = "full" if not baseline or due is None or now >= due else "incremental"
        policy["started_at"] = utc_now()
        self._full_audit = policy["mode"] == "full"

    def prepare_collection(self, state):
        policy = state["progress"].setdefault("nweb_policy", {"mode": "full"})
        self._full_audit = policy["mode"] == "full"

    def observe_catalog_page(self, state, partition, cursor, page, result):
        """Called before records are merged, so new/changed rows reset the window."""
        policy = state["progress"].get("nweb_policy", {})
        if policy.get("mode") != "incremental":
            return False
        unchanged = bool(result.records) and not result.skipped_rows and not result.untitled
        for row in result.records:
            old = state["records"].get(str(row.get("id")), {})
            if (not old.get("synopsis") or not (old.get("history", {}).get("last_success") or old.get("bulk_metadata_at"))
                    or old.get("listing_fingerprint") != listing_fingerprint(row, self.detail_fields)):
                unchanged = False
                break
        # Resumed scans re-read an overlap page; it must never count twice.
        previous = cursor.get("quiet_last_page", 0)
        if page <= previous:
            if not unchanged:
                cursor["quiet_count"] = 0
            return False
        count = cursor.get("quiet_count", 0) if previous == page - 1 else 0
        cursor.update(quiet_count=count + 1 if unchanged else 0, quiet_last_page=page)
        return cursor["quiet_count"] >= self.quiet_pages and result.next_page is not None

    def detail_needed(self, old, item, default):
        now = datetime.now(timezone.utc)
        last = stamp(old.get("history", {}).get("last_success"))
        bulk = stamp(old.get("bulk_metadata_at"))
        if bulk and old.get("synopsis") and old.get("detail_version") == self.detail_version:
            # Public batches do not always load keywords. Full audits still
            # obtain missing details and periodically refresh older full records.
            if getattr(self, "_full_audit", True) and (last is None or (now - last).days >= self.detail_refresh_days):
                return True
            if ((now - bulk).total_seconds() < self.changed_detail_days * 86400
                    and all(old.get(k) == item.get(k) for k in ("title", "author", "complete", "tier", "episodes"))):
                return False
        if (not default or not old.get("synopsis")
                or old.get("detail_version") != self.detail_version
                or old.get("history", {}).get("latest_outcome") != "success"):
            return default
        if any(old.get(key) != item.get(key) for key in ("title", "author", "complete", "tier")):
            return default
        return default and (last is None or (now - last).total_seconds()
                            >= self.changed_detail_days * 86400)

    def finish_catalog_pass(self, state, successful):
        policy = state["progress"].get("nweb_policy", {})
        if not successful or not policy:
            return False
        policy["finished_at"] = utc_now()
        if state["coverage"]["catalog"].get("discovery_complete"):
            policy["last_full_completed_at"] = utc_now()
            policy["audit_due_at"] = (datetime.now(timezone.utc) + timedelta(days=self.audit_days)).isoformat()
        state["coverage"]["catalog"]["scan_mode"] = policy["mode"]
        return True
