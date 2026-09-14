#!/usr/bin/env python3
"""
Post-deployment smoke test (M15b).

WHAT THIS CATCHES
    The failure class that made SparkLM look dead while every platform signal
    said healthy: Vercel serves HTTP 200 for the SPA shell whatever state the
    backend is in, so a frontend that cannot reach its API is invisible to
    Vercel, and a backend nobody is calling is invisible to Render. Nothing
    was watching the JOIN between them.

WHERE IT RUNS
    After a deployment, and on demand. Exits non-zero on any failure so a
    pipeline step or a person gets a verdict rather than a wall of output.

WHAT IT DELIBERATELY DOES NOT DO
    No credentials, no account creation, no writes. The auth check uses
    deliberately invalid credentials and asserts the endpoint REJECTS them
    correctly — which proves the whole chain (DNS, TLS, CORS, routing, DRF,
    database) without touching a real account.

    Cold starts are expected, not failures: the API is on a free plan that
    sleeps. The timeout is generous and the elapsed time is reported so a slow
    wake is visible as slow, not as broken.

Usage:
    python scripts/deployment_smoke.py
    python scripts/deployment_smoke.py --frontend URL --backend URL
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_FRONTEND = "https://spark-lm-3y3e.vercel.app"
DEFAULT_BACKEND = "https://sparklm-api.onrender.com"

# A cold Render free instance measured 92.9s in this repository's own
# warm-keeper. 150s leaves room for a cold start plus migrations without
# turning "slow" into "failed".
TIMEOUT_SECONDS = 150


class Result:
    def __init__(self, name: str, ok: bool, detail: str, seconds: float):
        self.name, self.ok, self.detail, self.seconds = name, ok, detail, seconds


def request(method: str, url: str, *, body: dict | None = None,
            headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    merged = {"Accept": "*/*"}
    if data is not None:
        merged["Content-Type"] = "application/json"
    merged.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return (response.status, response.read().decode("utf-8", "replace"),
                    dict(response.headers), time.time() - started)
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.read().decode("utf-8", "replace"),
                dict(exc.headers), time.time() - started)


def check(name, fn) -> Result:
    started = time.time()
    try:
        ok, detail = fn()
    except Exception as exc:                      # noqa: BLE001
        return Result(name, False, f"{type(exc).__name__}: {exc}",
                      time.time() - started)
    return Result(name, ok, detail, time.time() - started)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    args = parser.parse_args()
    frontend, backend = args.frontend.rstrip("/"), args.backend.rstrip("/")

    def frontend_shell():
        status, body, _headers, _s = request("GET", frontend)
        if status != 200:
            return False, f"HTTP {status}"
        if "<div id=\"root\"" not in body and "<script" not in body:
            return False, "served HTML carries no app bundle"
        return True, "HTTP 200, app shell served"

    def frontend_spa_route():
        # The SPA rewrite: a deep link must serve index.html, not 404.
        status, body, _headers, _s = request("GET", f"{frontend}/auth")
        if status != 200:
            return False, f"HTTP {status} — SPA rewrite is not configured"
        return True, "HTTP 200, deep link rewritten to the app"

    def backend_health():
        status, body, _headers, _s = request("GET", f"{backend}/healthz")
        if status != 200:
            return False, f"HTTP {status}: {body[:120]}"
        return True, f"HTTP 200 {body[:70]}"

    def backend_database():
        status, body, _headers, _s = request("GET", f"{backend}/api/health/")
        if status != 200:
            return False, f"HTTP {status}: {body[:120]}"
        try:
            payload = json.loads(body)
        except ValueError:
            return False, f"not JSON: {body[:80]}"
        if payload.get("db") != "connected":
            return False, f"database not connected: {payload}"
        return True, f"db={payload.get('db')}"

    def cors_preflight():
        # The join nothing else watches: does the backend accept THIS
        # frontend's origin? A mismatch makes every API call fail in the
        # browser while both services report healthy on their own.
        status, _body, headers, _s = request(
            "OPTIONS", f"{backend}/api/token/",
            headers={"Origin": frontend,
                     "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers":
                         "content-type,x-sparklm-client"})
        allowed = headers.get("access-control-allow-origin")
        if status >= 400:
            return False, f"preflight HTTP {status}"
        if allowed != frontend:
            return False, (f"origin not allowed: backend returned "
                           f"{allowed!r} for {frontend!r}")
        if headers.get("access-control-allow-credentials") != "true":
            return False, "credentials not allowed; the refresh cookie cannot be sent"
        return True, f"origin allowed, credentials allowed"

    def auth_rejects_bad_credentials():
        # Proves DNS + TLS + routing + DRF + database + password hashing,
        # without using or creating any account.
        status, body, headers, _s = request(
            "POST", f"{backend}/api/token/",
            body={"username": "sparklm-smoke-not-a-real-account",
                  "password": "sparklm-smoke-not-a-real-password"},
            headers={"Origin": frontend, "X-SparkLM-Client": "web"})
        if status != 401:
            return False, (f"expected HTTP 401 for invalid credentials, "
                           f"got {status}: {body[:120]}")
        if headers.get("access-control-allow-origin") != frontend:
            return False, "401 returned without CORS headers"
        try:
            json.loads(body)
        except ValueError:
            return False, f"401 body is not JSON: {body[:80]}"
        return True, "HTTP 401 with a JSON body and CORS headers"

    def refresh_endpoint_reachable():
        # The request the SPA makes on EVERY page load. If this hangs, the
        # app shows its loading screen and looks dead.
        status, body, _headers, _s = request(
            "POST", f"{backend}/api/token/refresh/", body={},
            headers={"Origin": frontend, "X-SparkLM-Client": "web"})
        if status not in (400, 401):
            return False, f"unexpected HTTP {status}: {body[:120]}"
        return True, f"HTTP {status} (signed-out answer, as expected)"

    checks = [
        ("frontend shell", frontend_shell),
        ("frontend SPA route", frontend_spa_route),
        ("backend health", backend_health),
        ("backend database", backend_database),
        ("CORS for this frontend", cors_preflight),
        ("auth rejects bad credentials", auth_rejects_bad_credentials),
        ("page-load refresh endpoint", refresh_endpoint_reachable),
    ]

    print(f"frontend {frontend}")
    print(f"backend  {backend}\n")
    results = [check(name, fn) for name, fn in checks]

    width = max(len(r.name) for r in results)
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        print(f"  [{mark}] {result.name:<{width}}  {result.seconds:6.2f}s  "
              f"{result.detail}")

    failed = [r for r in results if not r.ok]
    slow = [r for r in results if r.ok and r.seconds > 10]
    print(f"\n  {len(results) - len(failed)}/{len(results)} passed")
    if slow:
        print(f"  note: {len(slow)} check(s) took over 10s — the API was "
              f"probably asleep and had to cold start")
    if failed:
        print(f"\n  FAILED: {', '.join(r.name for r in failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
