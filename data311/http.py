"""The one way this project fetches anything from a data portal.

Every request:
- carries a User-Agent naming the project and the contact address from CONTACT_EMAIL.
  If CONTACT_EMAIL is unset, nothing is fetched (ContactEmailMissing).
- is checked against the host's robots.txt first (Disallowed if it doesn't allow it).
- waits at least the host's Crawl-delay (or the configured minimum) since the last request.
- is cached: the response body goes to the destination file with a sidecar .meta.json
  (URL, params, time, status, size, SHA-256, User-Agent). A cached file is never refetched.
- stops on refusal: 401, 403 and 429 raise Blocked and nothing is written.

robots.txt is parsed with urllib.robotparser, which matches path prefixes and does not
expand '*' or '$' inside rules. The rules that matter for this project (SF's /api/odata/,
San Jose's /api/ and /datastore/) are plain prefixes.
"""

import hashlib
import json
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from agent.config import get_settings

PROJECT_UA = "CampusTrace/0.1 (portfolio research project; contact: {email})"


class ContactEmailMissing(RuntimeError):
    pass


class Disallowed(RuntimeError):
    pass


class Blocked(RuntimeError):
    pass


def user_agent(contact_email: str | None) -> str:
    if not contact_email or "@" not in contact_email:
        raise ContactEmailMissing(
            "CONTACT_EMAIL is not set. Set it in .env before fetching anything; "
            "there is no fallback address."
        )
    return PROJECT_UA.format(email=contact_email.strip())


@dataclass
class PoliteClient:
    cache_dir: Path
    contact_email: str | None = None
    min_delay: Mapping[str, float] = field(default_factory=dict)
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    timeout: float = 120.0

    def __post_init__(self) -> None:
        email = (
            self.contact_email if self.contact_email is not None else get_settings().contact_email
        )
        self.ua = user_agent(email)
        self._http = httpx.Client(
            headers={"User-Agent": self.ua},
            transport=self.transport,
            follow_redirects=True,
            timeout=self.timeout,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last: dict[str, float] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- robots and pacing --------------------------------------------------------------

    def _wait(self, host: str) -> None:
        delay = self.min_delay.get(host, 1.0)
        rp = self._robots.get(host)
        if rp is not None:
            crawl = rp.crawl_delay(self.ua)
            if crawl is not None:
                delay = max(delay, float(crawl))
        last = self._last.get(host)
        if last is not None:
            remaining = delay - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()

    def robots(self, host: str) -> urllib.robotparser.RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        path = self.cache_dir / "robots" / f"{host}.txt"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            self._wait(host)
            resp = self._http.get(f"https://{host}/robots.txt")
            if resp.status_code in (401, 403, 429):
                raise Blocked(f"robots.txt for {host}: HTTP {resp.status_code}")
            text = resp.text if resp.status_code == 200 else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        self._robots[host] = rp
        return rp

    def check_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        rp = self.robots(parts.netloc)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        if not rp.can_fetch(self.ua, target):
            raise Disallowed(f"robots.txt for {parts.netloc} disallows {parts.path}")

    # -- fetching -----------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        dest: Path,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Path:
        """Fetch url once into dest. Returns dest. Uses the cached file if present."""
        meta_path = dest.with_name(dest.name + ".meta.json")
        if dest.exists() and meta_path.exists():
            return dest

        full = url + ("?" + urlencode(params) if params else "")
        self.check_allowed(full)
        host = urlsplit(url).netloc
        self._wait(host)
        resp = self._http.get(url, params=params, headers=headers)
        if resp.status_code in (401, 403, 429):
            raise Blocked(f"{url}: HTTP {resp.status_code}")
        resp.raise_for_status()

        body = resp.content
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        meta: dict[str, Any] = {
            "url": url,
            "params": dict(params or {}),
            "final_url": str(resp.url).split("?")[0],
            "fetched_at": datetime.now(UTC).isoformat(),
            "status": resp.status_code,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "user_agent": self.ua,
        }
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return dest

    def fetch_json(
        self,
        url: str,
        dest: Path,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        return json.loads(self.fetch(url, dest, params, headers).read_text(encoding="utf-8"))
