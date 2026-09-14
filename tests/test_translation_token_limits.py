"""Offline checks that translation token settings reach actual request payloads."""

import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


class ReconfigurableOutput(io.StringIO):
    def reconfigure(self, **kwargs):
        pass


@pytest.fixture(scope="module")
def translator():
    path = Path(__file__).parents[1] / "scripts" / "translate_with_grok.py"
    spec = importlib.util.spec_from_file_location("translation_token_limits_subject", path)
    module = importlib.util.module_from_spec(spec)
    # Configuration tests need no tokenizer download or real stdout reconfiguration.
    tokenizer = SimpleNamespace(get_encoding=lambda _: SimpleNamespace(encode=lambda text: text.encode("utf-8")))
    with patch.object(sys, "stdout", ReconfigurableOutput()), patch.dict(sys.modules, {"tiktoken": tokenizer}):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def translation_run(translator, monkeypatch, tmp_path):
    input_file = tmp_path / "titles.txt"
    input_file.write_text("1|||소설|||\n", encoding="utf-8")
    for key in ("TRANSLATION_OUTPUT_TOKEN_LIMIT", "TRANSLATION_COMPRESSION_FACTOR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRANSLATION_API_KEY", "offline-test-key")
    monkeypatch.setenv("TRANSLATION_API_BASE_URL", "https://translation.example.test/v1")
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "1|||소설|||Novel"}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(translator.requests, "post", post)

    def run(*arguments):
        monkeypatch.setattr(sys, "argv", [str(translator.__file__), str(input_file),
                                          "--workers", "1", "--delay", "0", *arguments])
        translator.main()

    return run, post, input_file


@pytest.mark.parametrize("model,token_field", [
    ("gpt-5.6-luna", "max_completion_tokens"),
    ("deepseek-chat", "max_tokens"),
])
@pytest.mark.parametrize("environment,cli,expected", [
    (None, None, 16384),
    ("", None, 16384),
    ("24576", None, 24576),
    ("24576", "32768", 32768),
    ("invalid-but-overridden", "32768", 32768),
])
def test_main_sends_resolved_output_limit_to_api(
    translator, translation_run, monkeypatch, capsys, model, token_field, environment, cli, expected
):
    run, post, input_file = translation_run
    if environment is not None:
        monkeypatch.setenv("TRANSLATION_OUTPUT_TOKEN_LIMIT", environment)
    arguments = ["--model", model]
    if cli is not None:
        arguments.extend(["--output-token-limit", cli])

    run(*arguments)

    post.assert_called_once()
    assert post.call_args.args == ("https://translation.example.test/v1/chat/completions",)
    payload = post.call_args.kwargs["json"]
    assert payload[token_field] == expected
    other_field = "max_tokens" if token_field == "max_completion_tokens" else "max_completion_tokens"
    assert other_field not in payload
    assert input_file.read_text(encoding="utf-8") == "1|||소설|||Novel\n"
    output = capsys.readouterr().out
    assert f"Output token limit: {expected:,}" in output
    assert f"compression factor: 2 | soft chunk target: {expected // 2:,} tokens" in output


@pytest.mark.parametrize("limit", ["0", "-1"])
@pytest.mark.parametrize("setting", ["environment", "cli"])
def test_nonpositive_output_limit_fails_before_api_call(translation_run, monkeypatch, capsys, limit, setting):
    run, post, input_file = translation_run
    arguments = ["--model", "gpt-5.6-luna"]
    if setting == "environment":
        monkeypatch.setenv("TRANSLATION_OUTPUT_TOKEN_LIMIT", limit)
    else:
        monkeypatch.setenv("TRANSLATION_OUTPUT_TOKEN_LIMIT", "24576")
        arguments.extend(["--output-token-limit", limit])

    with pytest.raises(SystemExit) as error:
        run(*arguments)

    assert error.value.code != 0
    post.assert_not_called()
    assert input_file.read_text(encoding="utf-8") == "1|||소설|||\n"
    assert "output token limit must be greater than 0" in capsys.readouterr().out


def test_invalid_environment_output_limit_fails_before_api_call(translation_run, monkeypatch, capsys):
    run, post, _ = translation_run
    monkeypatch.setenv("TRANSLATION_OUTPUT_TOKEN_LIMIT", "many")

    with pytest.raises(SystemExit) as error:
        run("--model", "gpt-5.6-luna")

    assert error.value.code != 0
    post.assert_not_called()
    assert "TRANSLATION_OUTPUT_TOKEN_LIMIT must be an integer" in capsys.readouterr().out
