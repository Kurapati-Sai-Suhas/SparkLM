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
