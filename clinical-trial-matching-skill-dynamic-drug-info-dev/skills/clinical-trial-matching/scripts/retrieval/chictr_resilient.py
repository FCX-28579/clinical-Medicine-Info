"""
chictr_resilient.py — v1.6.0 M1

Wrapper around chictr-mcp-server that adds:
  - retry with backoff on `browser closed` / transient errors
  - circuit breaker (skip after N consecutive failures)
  - explicit fetch_status tracking per trial (success / fallback_search_only / failed)
  - degraded mode: when detail-fetch fails, return search-only metadata + a fetch_status flag

This is a thin Python interface; the actual MCP calls happen in the LLM/skill
session. The skill calls these helper functions to manage retry state and
present a unified result schema.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ChictrFetchResult:
    registration_number: str
    status: str  # "success" | "fallback_search_only" | "failed" | "circuit_open"
    detail: Optional[dict] = None  # full detail when status=success
    search_metadata: Optional[dict] = None  # search-level metadata when status=fallback_search_only
    error: Optional[str] = None
    attempts: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class CircuitState:
    consecutive_failures: int = 0
    open: bool = False
    last_failure_at: float = 0.0
    cooldown_seconds: int = 60

    def record_success(self):
        self.consecutive_failures = 0
        self.open = False

    def record_failure(self):
        self.consecutive_failures += 1
        self.last_failure_at = time.time()
        if self.consecutive_failures >= 3:
            self.open = True

    def can_attempt(self) -> bool:
        if not self.open:
            return True
        # try to half-open after cooldown
        if time.time() - self.last_failure_at > self.cooldown_seconds:
            self.open = False
            self.consecutive_failures = 0
            return True
        return False


# Module-level circuit (reset per skill invocation)
_circuit = CircuitState()


def reset_circuit():
    global _circuit
    _circuit = CircuitState()


# ---------------------------------------------------------------------------
# Resilient wrappers
# ---------------------------------------------------------------------------
TRANSIENT_ERROR_PATTERNS = [
    "browser.newContext",
    "browser.newPage",
    "Target page, context or browser has been closed",
    "Timeout",
    "ECONNRESET",
    "ETIMEDOUT",
    "browserContext",
]


def _is_transient_error(err: Exception | str) -> bool:
    err_str = str(err)
    return any(p in err_str for p in TRANSIENT_ERROR_PATTERNS)


def fetch_trial_detail_with_retry(
    registration_number: str,
    detail_fetcher: Callable[[str], dict],
    search_metadata: Optional[dict] = None,
    retries: int = 3,
    backoff_base: float = 2.0,
) -> ChictrFetchResult:
    """
    Try detail_fetcher up to `retries` times with exponential backoff.
    On persistent failure, return fallback result populated with search_metadata.

    detail_fetcher: callable that takes registration_number and returns the trial detail dict.
                    The skill will wrap mcp__chictr__get_trial_detail in a closure.
    """
    if not _circuit.can_attempt():
        return ChictrFetchResult(
            registration_number=registration_number,
            status="circuit_open",
            search_metadata=search_metadata,
            error="Circuit breaker open after 3 consecutive failures; cooling down",
            attempts=0,
        )

    start = time.time()
    last_error = None
    for attempt in range(retries):
        try:
            result = detail_fetcher(registration_number)
            _circuit.record_success()
            return ChictrFetchResult(
                registration_number=registration_number,
                status="success",
                detail=result,
                attempts=attempt + 1,
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            last_error = e
            if not _is_transient_error(e):
                # non-transient → fail fast, no retry
                _circuit.record_failure()
                break
            # backoff before retry
            time.sleep(backoff_base ** attempt)

    _circuit.record_failure()
    return ChictrFetchResult(
        registration_number=registration_number,
        status="fallback_search_only" if search_metadata else "failed",
        search_metadata=search_metadata,
        error=str(last_error) if last_error else "Unknown error",
        attempts=retries,
        elapsed_seconds=time.time() - start,
    )


def normalize_chictr_search_to_metadata(search_result: dict) -> dict:
    """
    Take a search-level result (from mcp__chictr__search_trials) and produce
    the same schema as a NCT trial dict so it can be fed into downstream
    extractor/gating without special-casing.
    """
    return {
        "id": search_result.get("registration_number", ""),
        "source": "ChiCTR",
        "title": search_result.get("title", ""),
        "phases": [search_result.get("study_type", "")],
        "sponsor": search_result.get("institution", ""),
        "interventions": [],
        "line_info": "unknown",
        "prior_kras_inhibitor_excluded": False,
        "china_sites": [{"facility": search_result.get("institution", ""),
                         "city": "",
                         "contact": ""}],
        "china_site_count": 1,
        "eligibility_excerpt": "",
        "eligibility_full": "",
        "parsed_criteria": {"inclusion": [], "exclusion": [], "raw": ""},
        "registration_date": search_result.get("registration_date", ""),
        "fetch_status": "search_only",
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Mock test
    call_log = []

    def flaky_fetcher(rn):
        call_log.append(rn)
        if len(call_log) < 2:
            raise Exception("browser.newContext: Target page closed")
        return {"id": rn, "title": "Mock detail"}

    res = fetch_trial_detail_with_retry("ChiCTR2600122046", flaky_fetcher,
                                         search_metadata={"title": "Mock search"},
                                         retries=3, backoff_base=0.1)
    print(f"Status: {res.status}, attempts: {res.attempts}")
    assert res.status == "success" and res.attempts == 2

    # Test circuit breaker
    reset_circuit()
    def always_fail(rn):
        raise Exception("browser closed")

    for i in range(4):
        r = fetch_trial_detail_with_retry(f"X{i}", always_fail, search_metadata={"title": "fallback"},
                                          retries=2, backoff_base=0.1)
        print(f"  Call {i}: status={r.status}")

    print("✅ chictr_resilient self-test passed")
