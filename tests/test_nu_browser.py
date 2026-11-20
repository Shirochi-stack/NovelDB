from contextlib import contextmanager
from unittest.mock import Mock
import json

import pytest

from scripts import nu_browser, nu_titles


def test_cdp_event_stream_cannot_extend_command_deadline(monkeypatch):
    import websocket

    clock = [100.0]
    monkeypatch.setattr(nu_browser.time, "monotonic", lambda: clock[0])
    ws = Mock()

    def event():
        clock[0] += 3
        return json.dumps({"method": "DOM.documentUpdated"})

    ws.recv.side_effect = event
    monkeypatch.setattr(websocket, "create_connection", Mock(return_value=ws))
    with pytest.raises(TimeoutError, match="DOM.getDocument"):
        with nu_browser.connection({"webSocketDebuggerUrl": "ws://localhost/test"}) as call:
            call("DOM.getDocument")
    assert ws.recv.call_count == 4
    ws.close.assert_called_once()


def test_checkbox_uses_actual_frame_dom_and_geometry(monkeypatch):
    node = {"nodeName": "INPUT", "nodeId": 17, "attributes": ["type", "checkbox"]}
    call = Mock(side_effect=[
        {"root": {"shadowRoots": [{"children": [node]}]}},
        {"model": {"width": 20, "height": 20, "border": [10, 20, 30, 20, 30, 40, 10, 40]}},
        {}, {}, {},
    ])

    @contextmanager
    def connection(_):
        yield call

    monkeypatch.setattr(nu_browser, "connection", connection)
    assert nu_browser.click_verification({})
    events = [item.args for item in call.call_args_list if item.args[0] == "Input.dispatchMouseEvent"]
    assert [event[1]["type"] for event in events] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert all(event[1]["x"] == 20 and event[1]["y"] == 30 for event in events)
    assert all(not item.args[0].startswith("Runtime.") for item in call.call_args_list)


def browser_fixture(monkeypatch, titles):
    clock = [100.0]
    monkeypatch.setattr(nu_browser.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(nu_browser.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    browser = nu_browser.Browser.__new__(nu_browser.Browser)
    url = "https://www.novelupdates.com/language/korean/"
    frame = {"id": "frame", "type": "iframe", "url": "https://challenges.cloudflare.com/widget"}
    states = iter(titles)

    def request(path, method="GET"):
        if path.startswith("/json/new?"):
            assert method == "PUT"
            return {"id": "page"}
        if path == "/json/list":
            return [{"id": "page", "url": url, "title": next(states)}, frame]
        assert path == "/json/close/page"

    browser.request = Mock(side_effect=request)
    return browser, url


def test_interactive_challenge_then_validated_document(monkeypatch):
    browser, url = browser_fixture(monkeypatch, ["Just a moment..."] * 12 + ["Language Korean"])
    click = Mock(return_value=True)
    monkeypatch.setattr(nu_browser, "click_verification", click)
    monkeypatch.setattr(nu_browser, "document", Mock(return_value="verified html"))
    validate = Mock()
    assert browser.fetch(url, validate) == "verified html"
    assert click.call_count == 2  # Wait between attempts; never click every polling tick.
    validate.assert_called_once_with("verified html", url)
    browser.request.assert_called_with("/json/close/page")


def test_a_normal_title_does_not_make_a_challenge_a_success(monkeypatch):
    browser, url = browser_fixture(monkeypatch, ["Language Korean"] * 3)
    monkeypatch.setattr(nu_browser, "document", Mock(return_value="<title>Just a moment...</title>"))
    with pytest.raises(RuntimeError, match="timed out.*listing page"):
        browser.fetch(url, nu_titles.Collector.validate, timeout=3)
    browser.request.assert_called_with("/json/close/page")


def test_timeout_is_bounded_and_closes_only_its_own_tab(monkeypatch):
    browser, url = browser_fixture(monkeypatch, ["Just a moment..."] * 3)
    monkeypatch.setattr(nu_browser, "click_verification", Mock(return_value=False))
    with pytest.raises(RuntimeError, match='timed out after 3s.*Just a moment'):
        browser.fetch(url, Mock(), timeout=3)
    browser.request.assert_called_with("/json/close/page")
