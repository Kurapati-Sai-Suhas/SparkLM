"""
Under pytest, Judge0 is unreachable (Phase 1 M17).

`.env` carries the real RapidAPI key. Before M17 two tests reached the runner
unmocked and each local run made two real, billed submissions. The isolation
plugin now blanks the key and points the base URL at the loopback discard
port. These tests hold that, behaviourally: an unmocked call fails locally,
and never with a response from anywhere.
"""

import requests

from groups import coding_views


def test_the_runner_is_pointed_at_the_loopback_discard_port():
    assert coding_views.JUDGE0_KEY in (None, "")
    assert coding_views.JUDGE0_BASE == "http://127.0.0.1:9"


def test_an_unmocked_submission_never_leaves_the_machine(monkeypatch):
    seen = []
    real = requests.Session.request

    def spy(self, method, url, *args, **kwargs):
        seen.append(url)
        return real(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", spy)

    verdict = coding_views._run_on_judge0("print(1)", "python", "")

    assert seen and all(url.startswith("http://127.0.0.1:9/") for url in seen)
    assert verdict.get("status_id") != 3          # nothing was accepted
    assert verdict.get("stdout") in (None, "")    # nothing ran anywhere
