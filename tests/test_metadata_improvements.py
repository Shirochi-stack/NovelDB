"""Regression checks for throughput, durable continuation, and translation routing."""
import json
from pathlib import Path
import threading

import pytest

from scripts import metadata_common as m
from scripts import metadata_workflow as workflow
from scripts import translate_with_grok as translator
from test_metadata_common import Adapter, Client, args


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(m.requests.Session, "request", lambda *a, **k: pytest.fail("Unexpected network"))


def test_details_start_before_discovery_finishes_and_workers_refill(tmp_path):
    third_started = threading.Event()
    events = []
    class Continuous(Adapter):
        def fetch_page(self, client, partition, page):
            events.append(f"page:{page}")
            return m.CatalogPage([{"id": str(i), "title": "Title"} for i in range((page-1)*3+1, page*3+1)],
                                 page + 1 if page < 3 else None)
        def detail(self, client, record):
            events.append(f"detail:{record['id']}")
            if record["id"] == "1":
                assert third_started.wait(5), "Worker pool waited for a batch instead of refilling"
            elif record["id"] == "3":
                third_started.set()
            return super().detail(client, record)
    report = m.run_source(Continuous(), args(tmp_path, "--mode", "catalog", "--workers", "2"), client=Client())
    assert events.index("detail:1") < events.index("page:3")
    assert third_started.is_set() and report["coverage"]["successful_details"] == 9
    assert report["coverage"]["complete"]


def test_checkpoint_count_is_bounded_for_large_detail_queue(tmp_path, monkeypatch):
    saves = []
    original_save = m.save_state
    def save(state, directory):
        saves.append(len(state["progress"]["pending_details"]))
        original_save(state, directory)
    monkeypatch.setattr(m, "save_state", save)
    class Large(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": str(i), "title": "Title"} for i in range(1, 1101)], None)
    report = m.run_source(Large(), args(tmp_path, "--mode", "catalog"), client=Client())
    assert report["coverage"]["successful_details"] == 1100
    assert len(saves) <= 12  # Periodic saves plus explicit phase boundaries, not 275 batches.
    assert saves[-1] == 0


def test_recovered_details_run_before_any_catalog_discovery(tmp_path):
    saved = m.empty_state("naver")
    saved["records"] = {str(i): {"id": str(i), "title": "Saved title"} for i in (1, 2, 3)}
    saved["progress"] = {"scan_id": "recovered", "revision": 5, "pending_details": ["3", "1", "2"],
                         "partitions": {"best": {"next_page": 20, "complete": False}}}
    m.save_state(saved, tmp_path / "state")
    attempted = []
    class Backfill(Adapter):
        def partitions(self, client):
            pytest.fail("Must finish recovered details before discovering more pages")
        def detail(self, client, record):
            attempted.append(record["id"])
            if record["id"] == "3":
                raise m.BudgetExceeded("Runtime budget reached")
            return super().detail(client, record)
    report = m.run_source(Backfill(), args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1"), client=Client())
    restored = m.load_state("naver", tmp_path / "state")
    assert attempted == ["1", "2", "3"]
    assert restored["records"]["1"]["synopsis"]
    assert restored["progress"]["pending_details"] == ["3"]
    assert restored["progress"]["partitions"]["best"]["next_page"] == 20
    assert report["coverage"]["pages"] == 0 and report["coverage"]["continuation"]["eligible"]


def test_budget_resume_preserves_catalog_status_across_rank_refresh(tmp_path):
    class Interrupted(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": str(i), "title": "Title"} for i in (1, 2, 3)], None)
        def detail(self, client, record):
            raise m.BudgetExceeded("Runtime budget reached")
    first = m.run_source(Interrupted(), args(tmp_path, "--mode", "catalog"), client=Client())
    decision = first["coverage"]["continuation"]
    assert decision["eligible"]
    assert first["coverage"]["catalog"]["discovery_complete"]
    second = m.run_source(Adapter(), args(tmp_path, "--mode", "rankings"), client=Client())
    assert second["coverage"]["catalog"] == first["coverage"]["catalog"]
    assert not second["coverage"]["continuation"]["eligible"]
    assert workflow.claim_continuation("naver", tmp_path / "state", decision["scan_id"], decision["revision"])
    assert not workflow.claim_continuation("naver", tmp_path / "state", decision["scan_id"], decision["revision"])
    class Resume(Adapter):
        def fetch_page(self, *arguments):
            pytest.fail("Completed discovery must not restart during enrichment resume")
    final = m.run_source(Resume(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    assert final["coverage"]["complete"]
    assert not workflow.claim_continuation("naver", tmp_path / "state", decision["scan_id"], decision["revision"])


def test_rankings_only_cannot_claim_catalog_baseline(tmp_path):
    report = m.run_source(Adapter(), args(tmp_path, "--mode", "rankings"), client=Client())
    assert report["coverage"]["rankings"]["complete"]
    assert not report["coverage"]["catalog"].get("started")
    assert not report["coverage"]["catalog"]["has_complete_baseline"]


def test_no_progress_budget_does_not_continue(tmp_path):
    class NoProgress(Adapter):
        def fetch_page(self, *arguments):
            raise m.BudgetExceeded("Runtime budget reached")
    result = m.run_source(NoProgress(), args(tmp_path, "--mode", "catalog"), client=Client())
    assert not result["coverage"]["continuation"]["eligible"]


def test_restriction_outcomes_are_saved_progress_on_detail_only_resume(tmp_path):
    class Interrupted(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": str(i), "title": "Title"} for i in (1, 2, 3)], None)
        def detail(self, client, record):
            raise m.BudgetExceeded("Runtime budget reached")
    m.run_source(Interrupted(), args(tmp_path, "--mode", "catalog"), client=Client())
    class Restricted(Interrupted):
        def detail(self, client, record):
            if record["id"] == "1":
                return m.MetadataResult("restricted", {"id": "1"}, "Public age gate")
            return super().detail(client, record)
    report = m.run_source(Restricted(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    assert report["coverage"]["pages"] == 0
    assert report["coverage"]["successful_details"] == 0
    assert report["coverage"]["metadata_updated"]
    assert report["coverage"]["continuation"]["eligible"]


def test_coverage_limitation_stops_chain_but_known_details_are_enriched(tmp_path):
    class Limited(Adapter):
        def fetch_page(self, client, partition, page):
            if page == 2:
                return m.CatalogPage([], None, False, "Pagination reset")
            return super().fetch_page(client, partition, page)
    report = m.run_source(Limited(), args(tmp_path, "--mode", "catalog"), client=Client())
    assert report["coverage"]["catalog"]["errors"]
    assert report["coverage"]["successful_details"] == 1
    assert not report["coverage"]["continuation"]["eligible"]


def test_continuation_report_requires_success_and_enabled_flag():
    report = {"source": "naver", "coverage": {"continuation": {
        "eligible": True, "scan_id": "a" * 32, "revision": 2, "workers": 4}}}
    assert workflow.continuation_report("naver", report)["eligible"]
    assert not workflow.continuation_report("naver", report, successful=False)["eligible"]
    assert not workflow.continuation_report("naver", report, enabled=False)["eligible"]
    assert not workflow.continuation_report("joara", report)["eligible"]


def test_luna_payload_and_provider_keys(monkeypatch):
    assert translator.DEFAULT_MODEL == "gpt-6-luna"
    for key in translator.model_key_order(translator.DEFAULT_MODEL):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "wrong-provider")
    with pytest.raises(SystemExit):
        translator.resolve_api_key(model=translator.DEFAULT_MODEL)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai")
    assert translator.resolve_api_key(model=translator.DEFAULT_MODEL) == "fixture-openai"
    sent = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "translated"}}]}
    def post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return Response()
    monkeypatch.setattr(translator.requests, "post", post)
    # The older Luna name is included deliberately: the payload branch used to
    # match one exact model, so an upgrade silently reverted to the wrong
    # completion parameters. Every Luna revision must keep this shape.
    for model in (translator.DEFAULT_MODEL, "gpt-5.6-luna", "deepseek-v4-pro"):
        translator.call_api("title", "fixture", model, "https://example.test/chat/completions", output_token_limit=8192)
    luna, previous_luna, override = sent[0][1], sent[1][1], sent[2][1]
    for payload in (luna, previous_luna):
        assert payload["reasoning_effort"] == "none" and payload["max_completion_tokens"] == 8192
        assert "max_tokens" not in payload and "temperature" not in payload
    assert override["max_tokens"] == 8192 and override["temperature"] == 0.3


def test_all_translation_workflows_have_luna_and_openai_secret():
    root = Path(__file__).resolve().parents[1]
    for name in ("translate-novelpia-top", "translate-kakao", "translate-sfacg", "translate-tags", "metadata-source-job"):
        content = (root / ".github" / "workflows" / f"{name}.yml").read_text(encoding="utf-8")
        assert "gpt-6-luna" in content and "secrets.OPENAI_API_KEY" in content
        assert "deepseek-v4-pro" not in content
    for path in (root / ".github" / "workflows").glob("*.yml"):
        content = path.read_text(encoding="utf-8")
        if "group: data-write-lock" in content:
            assert "cancel-in-progress: false" in content
            assert "queue:" not in content
            # Reject unsupported concurrency keys rather than suppressing lint errors.
            import re
            block = re.search(r"(?m)^concurrency:\n((?:[ ]+[^\n]*\n)+)", content)
            assert block, path.name
            keys = {line.strip().split(":", 1)[0] for line in block[1].splitlines() if line.strip()}
            assert keys <= {"group", "cancel-in-progress"}, (path.name, keys)
