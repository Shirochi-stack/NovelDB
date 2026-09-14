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


def test_discovery_finishes_before_details_and_workers_refill(tmp_path):
    third_started = threading.Event()
    events = []
    class Continuous(Adapter):
        def fetch_page(self, client, partition, page):
            events.append(f"page:{page}")
            return super().fetch_page(client, partition, page)
        def detail(self, client, record):
            events.append(f"detail:{record['id']}")
            if record["id"] == "1":
                assert third_started.wait(5), "Worker pool waited for a batch instead of refilling"
            elif record["id"] == "3":
                third_started.set()
            return super().detail(client, record)
    report = m.run_source(Continuous(), args(tmp_path, "--mode", "catalog", "--workers", "2"), client=Client())
    assert events[:3] == ["page:1", "page:2", "page:3"]
    assert third_started.is_set() and report["coverage"]["successful_details"] == 3
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


def test_budget_resume_preserves_catalog_status_across_rank_refresh(tmp_path):
    class Interrupted(Adapter):
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
    assert translator.DEFAULT_MODEL == "gpt-5.6-luna"
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
    for model in (translator.DEFAULT_MODEL, "deepseek-v4-pro"):
        translator.call_api("title", "fixture", model, "https://example.test/chat/completions", output_token_limit=8192)
    luna, override = sent[0][1], sent[1][1]
    assert luna["reasoning_effort"] == "none" and luna["max_completion_tokens"] == 8192
    assert "max_tokens" not in luna and "temperature" not in luna
    assert override["max_tokens"] == 8192 and override["temperature"] == 0.3


def test_all_translation_workflows_have_luna_and_openai_secret():
    root = Path(__file__).resolve().parents[1]
    for name in ("translate-novelpia-top", "translate-kakao", "translate-sfacg", "translate-tags", "metadata-source-job"):
        content = (root / ".github" / "workflows" / f"{name}.yml").read_text(encoding="utf-8")
        assert "gpt-5.6-luna" in content and "secrets.OPENAI_API_KEY" in content
        assert "deepseek-v4-pro" not in content
    for path in (root / ".github" / "workflows").glob("*.yml"):
        content = path.read_text(encoding="utf-8")
        if "group: data-write-lock" in content:
            assert "queue: max" in content
