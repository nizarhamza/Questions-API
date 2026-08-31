"""Wikidata SPARQL client: descriptive User-Agent, paging, retry/backoff, disk cache."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

ENDPOINT = "https://query.wikidata.org/sparql"

# Wikidata throttles anonymous clients hard. Set QBANK_USER_AGENT to something
# that identifies you and includes a way to contact you.
DEFAULT_UA = os.environ.get(
    "QBANK_USER_AGENT",
    "QuestionsAPI-BankBuilder/0.1 (https://github.com/nizarh393/Questions-API; nizarh393@gmail.com)",
)

RETRY_STATUS = {429, 500, 502, 503, 504}
_LIMIT_TAIL = re.compile(r"\blimit\s+\d+\s*$", re.IGNORECASE)


class SparqlError(RuntimeError):
    pass


class SparqlClient:
    """Thin, polite wrapper over the Wikidata Query Service."""

    def __init__(
        self,
        endpoint: str = ENDPOINT,
        user_agent: str = DEFAULT_UA,
        cache_dir: str | Path = ".cache/sparql",
        timeout: int = 90,
        max_retries: int = 5,
        use_cache: bool = True,
        min_interval: float = 1.0,
        verbose: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self.use_cache = use_cache
        self.min_interval = min_interval
        self.verbose = verbose
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/sparql-results+json"}
        )

    # ---------------------------------------------------------------- internals

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def _cache_path(self, query: str) -> Path:
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    @staticmethod
    def _flatten(payload: dict[str, Any]) -> list[dict[str, str]]:
        """SPARQL JSON results -> plain dicts of column name to string value."""
        rows: list[dict[str, str]] = []
        for binding in payload.get("results", {}).get("bindings", []):
            rows.append({k: v.get("value", "") for k, v in binding.items()})
        return rows

    # ------------------------------------------------------------------- public

    def query(self, query: str, *, refresh: bool = False) -> list[dict[str, str]]:
        """Run one query. Cached on disk by exact query text."""
        cache_file = self._cache_path(query)
        if self.use_cache and not refresh and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.post(
                    self.endpoint, data={"query": query}, timeout=self.timeout
                )
            except requests.RequestException as exc:  # network / timeout
                last_error = exc
                delay = self._backoff(attempt)
                self._log(f"  ! {type(exc).__name__}; retrying in {delay:.1f}s")
                time.sleep(delay)
                continue

            if resp.status_code == 200:
                rows = self._flatten(resp.json())
                if self.use_cache:
                    cache_file.write_text(json.dumps(rows), encoding="utf-8")
                return rows

            if resp.status_code in RETRY_STATUS:
                delay = self._retry_after(resp) or self._backoff(attempt)
                self._log(f"  ! HTTP {resp.status_code}; retrying in {delay:.1f}s")
                time.sleep(delay)
                last_error = SparqlError(f"HTTP {resp.status_code}")
                continue

            raise SparqlError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        raise SparqlError(f"gave up after {self.max_retries} attempts: {last_error}")

    def query_paged(
        self,
        query: str,
        *,
        page_size: int = 5000,
        max_rows: int | None = None,
        refresh: bool = False,
    ) -> list[dict[str, str]]:
        """Page a query with LIMIT/OFFSET so it stays under the 60s server timeout.

        The query must carry its own ORDER BY (paging without one is not stable)
        and must not already end in a LIMIT clause.
        """
        if "order by" not in query.lower():
            raise ValueError("query_paged requires an ORDER BY for stable paging")
        if _LIMIT_TAIL.search(query.strip()):
            return self.query(query, refresh=refresh)

        rows: list[dict[str, str]] = []
        offset = 0
        while True:
            page = self.query(
                f"{query.rstrip()}\nLIMIT {page_size} OFFSET {offset}", refresh=refresh
            )
            rows.extend(page)
            self._log(f"  .. {len(rows)} rows")
            if len(page) < page_size:
                break
            if max_rows is not None and len(rows) >= max_rows:
                self._log(
                    f"  ! truncated at {max_rows} rows -- deep OFFSET paging times out on "
                    "WDQS, so raise this pattern's sitelink floor instead of paging further"
                )
                break
            offset += page_size
        return rows[:max_rows] if max_rows else rows

    # ------------------------------------------------------------------ backoff

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(60.0, (2**attempt) + random.uniform(0, 1.0))

    @staticmethod
    def _retry_after(resp: requests.Response) -> float | None:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return min(120.0, float(raw))
        except ValueError:
            return None
