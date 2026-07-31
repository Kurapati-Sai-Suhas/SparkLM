#!/usr/bin/env bash
#
# Production warm-keeper. Pings a health endpoint on a fixed interval and
# fails if the service goes cold after it is known to be warm.
#
# Extracted from .github/workflows/keepalive.yml so it can be tested. The
# previous inline version had no automated coverage: a mutation that disabled
# its failure path left every contract assertion green, which is the same
# class of unfalsifiable success the warm-keeper exists to detect.
# See common/test_keepalive_contract.py for the behavioural tests.
#
# Configuration (all via environment):
#   HEALTH_URL               required. Readiness endpoint to probe.
#   LOOP_SECONDS             total time to keep pinging. 0 = single ping and
#                            exit (use once an external uptime monitor owns
#                            the job; the loop then adds cost but no value).
#   INTERVAL_SECONDS         gap between pings. Must be < the platform idle
#                            timeout or the service sleeps between pings.
#   WARM_THRESHOLD_SECONDS   a ping at or above this, once the service is
#                            already warm, is treated as a regression.
#   CURL_MAX_TIME            per-attempt curl timeout.
#   PROBE_ATTEMPTS           attempts before declaring the endpoint down.
#   PROBE_RETRY_SLEEP        gap between attempts.
#
# Exit codes:
#   0  every ping healthy (a slow FIRST ping is tolerated by design: it
#      arrives after an uncontrolled scheduling gap, so cold there is
#      expected, not a regression)
#   1  endpoint never returned 200, or went cold after being warm
#
set -uo pipefail

HEALTH_URL="${HEALTH_URL:?HEALTH_URL is required}"
LOOP_SECONDS="${LOOP_SECONDS:-2700}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"
WARM_THRESHOLD_SECONDS="${WARM_THRESHOLD_SECONDS:-10}"
CURL_MAX_TIME="${CURL_MAX_TIME:-120}"
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-3}"
PROBE_RETRY_SLEEP="${PROBE_RETRY_SLEEP:-20}"

# Latency is handled in integer milliseconds throughout. The earlier version
# truncated to whole seconds with ${x%%.*}, which reported a 1.432 s ping as
# "1s" and let a 9.999 s ping pass a 10 s threshold.
to_ms()  { awk -v t="$1"  'BEGIN { printf "%.0f", t * 1000 }'; }
fmt_s()  { awk -v ms="$1" 'BEGIN { printf "%.3f", ms / 1000 }'; }

threshold_ms=$(to_ms "$WARM_THRESHOLD_SECONDS")

# Echoes "<http_code> <seconds>". Retries tolerate a redeploy in progress;
# only a sustained failure is real. Returns 1 if no attempt returned 200.
probe() {
  local out code attempt
  out="000 0"
  for attempt in $(seq 1 "$PROBE_ATTEMPTS"); do
    out=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" \
                --max-time "$CURL_MAX_TIME" "$HEALTH_URL" 2>/dev/null || echo "000 0")
    code=${out%% *}
    if [ "$code" = "200" ]; then
      echo "$out"
      return 0
    fi
    # No sleep after the final attempt - it only delays the failure.
    if [ "$attempt" -lt "$PROBE_ATTEMPTS" ]; then
      sleep "$PROBE_RETRY_SLEEP"
    fi
  done
  echo "$out"
  return 1
}

deadline=$(( $(date +%s) + LOOP_SECONDS ))
ping_no=0
unexpected_cold=0
max_ms=0
first_ms=0

# Ping-first loop: at least one ping always happens, so LOOP_SECONDS=0 means
# exactly one ping rather than none. The structure is what guarantees the
# invariant; test_single_ping_mode_sends_exactly_one_request proves it.
while true; do
  ping_no=$(( ping_no + 1 ))

  if ! result=$(probe); then
    echo "::error::${HEALTH_URL} did not return 200 after ${PROBE_ATTEMPTS} attempts (last: ${result})"
    exit 1
  fi

  code=${result%% *}
  seconds=${result##* }
  ms=$(to_ms "$seconds")

  if [ "$ms" -gt "$max_ms" ]; then
    max_ms="$ms"
  fi

  if [ "$ping_no" -eq 1 ]; then
    first_ms="$ms"
    echo "ping ${ping_no}: ${code} in $(fmt_s "$ms")s (first ping - cold tolerated)"
  else
    echo "ping ${ping_no}: ${code} in $(fmt_s "$ms")s"
    if [ "$ms" -ge "$threshold_ms" ]; then
      unexpected_cold=$(( unexpected_cold + 1 ))
      echo "::warning::ping ${ping_no} took $(fmt_s "$ms")s while the service should have been warm"
    fi
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    break
  fi
  sleep "$INTERVAL_SECONDS"
done

echo "--- summary ---"
echo "pings: ${ping_no} | first: $(fmt_s "$first_ms")s | max: $(fmt_s "$max_ms")s | unexpected cold: ${unexpected_cold}"

# Set by GitHub Actions; defaulted so the script runs anywhere.
: "${GITHUB_STEP_SUMMARY:=/dev/null}"
{
  echo "### Warm-keeper summary"
  echo ""
  echo "| metric | value |"
  echo "| --- | --- |"
  echo "| pings this run | ${ping_no} |"
  echo "| first ping (cold tolerated) | $(fmt_s "$first_ms")s |"
  echo "| slowest ping | $(fmt_s "$max_ms")s |"
  echo "| unexpected cold responses | ${unexpected_cold} |"
} >> "$GITHUB_STEP_SUMMARY"

if [ "$unexpected_cold" -gt 0 ]; then
  echo "::error::${unexpected_cold} ping(s) exceeded ${WARM_THRESHOLD_SECONDS}s after warm-up - the instance is not staying warm"
  exit 1
fi
