"""
ai_services._generate_json_with_fallback — the NVIDIA NIM backup path.

Groq is still the only provider used in the normal case. NIM is called
ONLY when Groq's response carries a daily (TPD) rate-limit error — never
for a per-minute limit, a transport error, or bad JSON, since those are
either transient or real bugs that a second provider wouldn't fix, and
retrying them is already the management commands' job.
"""

from unittest.mock import MagicMock, patch

import pytest

from groups import ai_services
from groups.ai_services import DailyQuotaExhausted, _generate_json_with_fallback


def _groq_response(content):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


def _daily_quota_error():
    return Exception(
        "Error code: 429 - {'error': {'message': 'Rate limit reached ... "
        "on tokens per day (TPD): Limit 100000, Used 99999', 'type': 'tokens', "
        "'code': 'rate_limit_exceeded'}}"
    )


def _per_minute_error():
    return Exception(
        "Error code: 429 - {'error': {'message': 'Rate limit reached ... "
        "on requests per minute (RPM)', 'type': 'requests', "
        "'code': 'rate_limit_exceeded'}}"
    )


class TestGroqHappyPath:
    def test_returns_parsed_groq_json_without_touching_nim(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "_call_nim_raw") as mock_nim:
            mock_groq.chat.completions.create.return_value = _groq_response('{"ok": true}')
            result = _generate_json_with_fallback("prompt", "ctx")
        assert result == {"ok": True}
        mock_nim.assert_not_called()

    def test_strips_markdown_fences(self):
        with patch.object(ai_services, "groq_client") as mock_groq:
            mock_groq.chat.completions.create.return_value = _groq_response('```json\n{"a": 1}\n```')
            result = _generate_json_with_fallback("prompt", "ctx")
        assert result == {"a": 1}


class TestDailyQuotaFallsBackToNim:
    def test_nim_configured_and_succeeds(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services, "_call_nim_raw", return_value='{"from": "nim"}') as mock_nim:
            mock_groq.chat.completions.create.side_effect = _daily_quota_error()
            result = _generate_json_with_fallback("prompt", "ctx")
        assert result == {"from": "nim"}
        mock_nim.assert_called_once()

    def test_nim_not_configured_raises_daily_quota_exhausted(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "NIM_API_KEY", None):
            mock_groq.chat.completions.create.side_effect = _daily_quota_error()
            with pytest.raises(DailyQuotaExhausted):
                _generate_json_with_fallback("prompt", "ctx")

    def test_nim_configured_but_also_fails_raises_daily_quota_exhausted(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services, "_call_nim_raw", return_value=None):
            mock_groq.chat.completions.create.side_effect = _daily_quota_error()
            with pytest.raises(DailyQuotaExhausted):
                _generate_json_with_fallback("prompt", "ctx")


class TestNonDailyErrorsDoNotFallBack:
    def test_per_minute_limit_returns_none_without_calling_nim(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services, "_call_nim_raw") as mock_nim:
            mock_groq.chat.completions.create.side_effect = _per_minute_error()
            result = _generate_json_with_fallback("prompt", "ctx")
        assert result is None
        mock_nim.assert_not_called()

    def test_generic_exception_returns_none_without_calling_nim(self):
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services, "_call_nim_raw") as mock_nim:
            mock_groq.chat.completions.create.side_effect = Exception("connection reset")
            result = _generate_json_with_fallback("prompt", "ctx")
        assert result is None
        mock_nim.assert_not_called()


class TestCallNimRaw:
    def test_returns_none_when_unconfigured(self):
        with patch.object(ai_services, "NIM_API_KEY", None):
            assert ai_services._call_nim_raw("prompt") is None

    def test_posts_to_nim_and_returns_content(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "choices": [{"message": {"content": '{"hello": "world"}'}}]
        }
        with patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services.requests, "post", return_value=fake_response) as mock_post:
            result = ai_services._call_nim_raw("prompt")
        assert result == '{"hello": "world"}'
        args, kwargs = mock_post.call_args
        assert args[0] == ai_services.NIM_CHAT_URL
        assert kwargs["headers"]["Authorization"] == "Bearer fake-nim-key"
        assert kwargs["json"]["model"] == ai_services.NIM_MODEL

    def test_returns_none_on_transport_failure(self):
        with patch.object(ai_services, "NIM_API_KEY", "fake-nim-key"), \
             patch.object(ai_services.requests, "post", side_effect=Exception("timeout")):
            assert ai_services._call_nim_raw("prompt") is None


# ═════════════════════════════════════════════════════════════════════
# The withdrawn Groq model (M2 P2.26)
#
# Five call sites hard-coded `llama-3.3-70b-versatile`. Groq withdrew it,
# so every Groq path in the app returned 404 — and because this module
# only diverts to NIM on a DAILY quota error, a 404 was not a supply
# problem it recognised, so the backup never fired for it either.
# ═════════════════════════════════════════════════════════════════════

WITHDRAWN_MODEL = "llama-3.3-70b-versatile"


def _model_not_found_error():
    """Exactly what Groq returned for the withdrawn model."""
    return Exception(
        "Error code: 404 - {'error': {'message': 'The model "
        "`llama-3.3-70b-versatile` does not exist or you do not have access "
        "to it.', 'type': 'invalid_request_error', 'code': "
        "'model_not_found'}}"
    )


class TestGroqModelConfiguration:
    def test_the_groq_model_comes_from_the_canonical_provider_config(self):
        """
        One source of truth. `reseed_generation.PROVIDER_MODELS` is what the
        agent provider already reads; this module must not hold a second
        answer that can drift from it.
        """
        from groups.reseed_generation import PROVIDER_MODELS

        assert ai_services._groq_model() == PROVIDER_MODELS["groq"]

    def test_the_model_is_environment_overridable(self, monkeypatch):
        """A retired model must be a config change, not a patch."""
        import importlib

        from groups import reseed_generation

        monkeypatch.setenv("RESEED_GROQ_MODEL", "vendor/some-newer-model")
        importlib.reload(reseed_generation)
        try:
            assert ai_services._groq_model() == "vendor/some-newer-model"
        finally:
            monkeypatch.delenv("RESEED_GROQ_MODEL", raising=False)
            importlib.reload(reseed_generation)

    def test_no_call_site_still_names_the_withdrawn_model(self):
        """
        Source-level guard. A future edit that reintroduces the literal is a
        live 404 on quizzes, hints and RAG — it must fail here first.
        """
        import inspect

        source = inspect.getsource(ai_services)

        assert WITHDRAWN_MODEL not in source.replace(
            f"`{WITHDRAWN_MODEL}`", "")  # the docstring may name it as history

    def test_every_groq_call_passes_the_configured_model(self):
        """
        Behavioural, not textual: drive the real function and inspect what
        was actually sent to the client.
        """
        with patch.object(ai_services, "groq_client") as mock_groq:
            mock_groq.chat.completions.create.return_value = _groq_response(
                '{"ok": true}')
            _generate_json_with_fallback("prompt", "ctx")

        sent = mock_groq.chat.completions.create.call_args.kwargs["model"]
        assert sent == ai_services._groq_model()
        assert sent != WITHDRAWN_MODEL


class TestModelNotFoundBehaviour:
    def test_a_404_is_handled_gracefully_and_does_not_raise(self):
        """
        Requirement: a Groq 404 must not surface as a 500. It returns None,
        which every caller already treats as "no answer".
        """
        with patch.object(ai_services, "groq_client") as mock_groq:
            mock_groq.chat.completions.create.side_effect = \
                _model_not_found_error()
            result = _generate_json_with_fallback("prompt", "ctx")

        assert result is None

    def test_a_404_does_not_divert_to_nim_and_that_is_deliberate(self):
        """
        DOCUMENTED SEMANTICS, PRESERVED. `_generate_json_with_fallback`
        diverts to NIM only on a DAILY quota error: "Any other Groq failure
        ... does NOT fall back: those are usually transient or real bugs that
        a second provider wouldn't fix."

        A withdrawn model is a real bug, and it has now been fixed at the
        source. Routing 404s to NIM would paper over the next one.

        NOTE the asymmetry, which is intentional and lives elsewhere: the
        agent's `reseed_generation._is_exhausted` DOES treat 404 as a supply
        problem, because that path is an offline batch that should keep
        working while a human fixes the config.
        """
        with patch.object(ai_services, "groq_client") as mock_groq, \
             patch.object(ai_services, "_call_nim_raw") as mock_nim:
            mock_groq.chat.completions.create.side_effect = \
                _model_not_found_error()
            result = _generate_json_with_fallback("prompt", "ctx")

        assert result is None
        mock_nim.assert_not_called()

    def test_a_404_does_not_leak_the_api_key_into_the_log(self, caplog):
        import logging

        with patch.object(ai_services, "groq_client") as mock_groq, \
             caplog.at_level(logging.ERROR):
            mock_groq.chat.completions.create.side_effect = \
                _model_not_found_error()
            _generate_json_with_fallback("prompt", "ctx")

        assert "gsk_" not in caplog.text
        assert "api_key" not in caplog.text.lower()


class TestAgentProviderUnchanged:
    def test_the_agent_reads_the_same_canonical_model(self):
        """
        The agent path was already correct. This fix must not regress it, and
        both must now agree on one id.
        """
        from groups.reseed_generation import PROVIDER_MODELS

        assert ai_services._groq_model() == PROVIDER_MODELS["groq"]

    def test_the_agent_provider_module_names_no_withdrawn_model_in_code(self):
        import inspect

        from groups.agent import provider

        source = inspect.getsource(provider)
        # The module docstring documents the incident by name; no executable
        # line may pass it as a model id.
        for line in source.splitlines():
            if "model=" in line:
                assert WITHDRAWN_MODEL not in line
