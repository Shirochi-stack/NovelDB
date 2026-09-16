"""Small, testable workflow guards and summaries; never scrapes or calls GitHub."""
import argparse
import json
import os
from pathlib import Path

try:
    from .metadata_common import SOURCE_LABELS, load_state, save_state, atomic_json
except ImportError:
    from metadata_common import SOURCE_LABELS, load_state, save_state, atomic_json


def claim_continuation(source, state_dir, scan_id="", revision=""):
    if not scan_id and not revision:
        return True
    if not scan_id or not str(revision).isdigit():
        return False
    state = load_state(source, state_dir)
    progress = state["progress"]
    claim = {"scan_id": scan_id, "revision": int(revision)}
    if (progress.get("scan_id") != scan_id or progress.get("revision") != int(revision)
            or progress.get("pass_complete") or progress.get("continuation_claim") == claim
            or not progress.get("catalog_continuation", {}).get("eligible")):
        return False
    progress["continuation_claim"] = claim
    save_state(state, state_dir)
    return True


def continuation_report(source, report, *, enabled=True, successful=True):
    coverage = report.get("coverage", {}) if report.get("source") == source else {}
    next_run = coverage.get("continuation", {})
    eligible = bool(enabled and successful and next_run.get("eligible")
                    and next_run.get("scan_id") and isinstance(next_run.get("revision"), int))
    return {"source": source, "eligible": eligible,
            "scan_id": next_run.get("scan_id", ""), "revision": str(next_run.get("revision", "")),
            "workers": str(next_run.get("workers", 4))}


def summary(source, report):
    coverage = report.get("coverage", {})
    catalog = coverage.get("catalog", {})
    lines = [f"## {SOURCE_LABELS[source]} metadata", "",
             f"Operation: **{coverage.get('mode', 'build/translate')}**",
             f"Records: **{report.get('records', 'see build output'):,}**" if isinstance(report.get('records'), int) else "Records: see build output",
             f"Catalog pages this run: {coverage.get('pages', 0):,}",
             f"Details: {coverage.get('successful_details', 0):,} successful / {coverage.get('details', 0):,} attempted",
             f"Requests: {coverage.get('requests', 0):,}",
             f"Discovery complete: {catalog.get('discovery_complete', False)}",
             f"Details pending: {coverage.get('enrichment', {}).get('pending', 'unknown')}",
             f"Stop: {coverage.get('stop_reason', coverage.get('status', 'see build output'))}",
             f"Automatic continuation eligible: {coverage.get('continuation', {}).get('eligible', False)}", ""]
    for category, counts in catalog.get("verification", {}).items():
        lines.append(f"Category {category}: {counts['observed']:,} unique works observed / "
                     f"{counts['expected']:,} reported by source")
    for error in coverage.get("errors", []):
        lines.append("- " + json.dumps(error, ensure_ascii=False))
    return "\n\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("guard", "summary"))
    parser.add_argument("--source", choices=SOURCE_LABELS, required=True)
    parser.add_argument("--state-dir", default="metadata/state")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "guard":
        allowed = claim_continuation(args.source, args.state_dir,
                                     os.getenv("EXPECTED_SCAN", ""), os.getenv("EXPECTED_REVISION", ""))
        print("Collection allowed" if allowed else "Skipping stale or duplicate continuation", flush=True)
        if os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                stream.write(f"allowed={str(allowed).lower()}\n")
    else:
        path = args.output_dir / f"{args.source}_run_report.json"
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not report:
            state = load_state(args.source, Path(args.state_dir))
            coverage = {**state.get("coverage", {}), "mode": "build/translate", "continuation": {"eligible": False}}
            for key in ("pages", "requests", "details", "successful_details"):
                coverage[key] = 0
            report = {"source": args.source, "records": len(state["records"]), "coverage": coverage}
        text = summary(args.source, report)
        print(text, flush=True)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(text)
        atomic_json(args.output_dir / "continuation.json", continuation_report(
            args.source, report, enabled=os.getenv("AUTO_CONTINUE", "true") == "true",
            successful=os.getenv("METADATA_SUCCESS") == "true"))


if __name__ == "__main__":
    main()
