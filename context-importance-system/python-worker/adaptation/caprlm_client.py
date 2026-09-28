"""The privacy filter the learning job uses: CAPRLM's local service, called over HTTP.

Implements :class:`adaptation.privacy.PrivacyFilter`, so the screen-memory stage needs no change. The service
(``CAPRLM/research/api/service.py``, ``POST /filter``) returns the redacted text, whether the page must be dropped, and a
version string; no entity values come back.

Rules that keep it safe:

* **Loopback only.** The text is sent to this PC's own service. A URL that points anywhere else is refused, so a typo or a
  copied setting cannot send page text across a network.
* **Fails closed.** Any problem (service down, HTTP error, malformed answer) raises :class:`CaprlmFilterError`; the stage
  then drops that capture's text, as the filter contract requires. Nothing is ever returned unfiltered.
* Standard library only, so it also works inside the frozen ``adapt.exe``.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .privacy import FilterResult, PrivacyContext

DEFAULT_URL = "http://127.0.0.1:8787"


class CaprlmFilterError(RuntimeError):
    pass


def _check_loopback(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "http":
        raise ValueError(f"privacy URL must be plain http on this PC, got {url!r}")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(f"privacy URL must point at this PC (127.0.0.1 or localhost), got host {host!r}")
    return url.rstrip("/")


class CaprlmPrivacyFilter:
    version = "caprlm-http"

    def __init__(self, base_url: str = DEFAULT_URL, *, timeout: float = 120.0) -> None:
        self.base_url = _check_loopback(base_url)
        self.timeout = timeout

    def available(self) -> bool:
        """True when the service answers its health check."""
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def filter(self, text: str, ctx: PrivacyContext) -> FilterResult:
        body = json.dumps({
            "text": text,
            "context": {"app": ctx.app or "", "domain": ctx.domain, "title": ctx.title,
                        "source": ctx.source if ctx.source in ("uia", "ocr") else "uia"},
        }).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/filter", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise CaprlmFilterError(f"privacy service call failed ({type(exc).__name__})") from None
        if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not isinstance(data.get("blocked"), bool):
            raise CaprlmFilterError("privacy service answered with an unexpected shape")
        return FilterResult(
            text="" if data["blocked"] else data["text"],
            blocked=data["blocked"],
            redactions=int(data.get("redactions") or 0),
            filter_version=str(data.get("filter_version") or self.version),
        )
