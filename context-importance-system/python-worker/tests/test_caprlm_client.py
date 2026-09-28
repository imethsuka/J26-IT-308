import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from adaptation.caprlm_client import CaprlmFilterError, CaprlmPrivacyFilter
from adaptation.privacy import PrivacyContext, PrivacyFilter

CTX = PrivacyContext(app="chrome", domain="mail.example.com", title="Inbox")


class _Server:
    """A throw-away stand-in for CAPRLM's service on a free local port."""

    def __init__(self, answer, status=200):
        outer = self
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 404)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                payload = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                self.send_response(status)
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def server():
    servers = []

    def make(answer, status=200):
        s = _Server(answer, status)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def test_it_satisfies_the_filter_protocol():
    assert isinstance(CaprlmPrivacyFilter(), PrivacyFilter)


def test_returns_the_redacted_text_and_sends_the_context(server):
    s = server({"text": "Mail [EMAIL_1]", "blocked": False, "redactions": 1, "filter_version": "caprlm/logreg-v0.2"})
    result = CaprlmPrivacyFilter(s.url).filter("Mail a@b.com", CTX)

    assert (result.text, result.blocked, result.redactions, result.filter_version) == ("Mail [EMAIL_1]", False, 1, "caprlm/logreg-v0.2")
    assert s.requests[0]["text"] == "Mail a@b.com"
    assert s.requests[0]["context"] == {"app": "chrome", "domain": "mail.example.com", "title": "Inbox", "source": "uia"}


def test_a_blocked_page_never_carries_text(server):
    s = server({"text": "leftover", "blocked": True, "redactions": 0, "filter_version": "v"})
    result = CaprlmPrivacyFilter(s.url).filter("x", CTX)

    assert result.blocked and result.text == ""


def test_service_down_raises_instead_of_passing_text_through():
    f = CaprlmPrivacyFilter("http://127.0.0.1:9", timeout=2)   # nothing listens on the discard port
    assert not f.available()
    with pytest.raises(CaprlmFilterError):
        f.filter("secret", CTX)


@pytest.mark.parametrize("answer, status", [(b"not json", 200), ({"text": 5, "blocked": False}, 200),
                                            ({"detail": "boom"}, 503), ({"text": "x"}, 200)])
def test_bad_answers_raise(server, answer, status):
    s = server(answer, status)
    with pytest.raises(CaprlmFilterError):
        CaprlmPrivacyFilter(s.url).filter("x", CTX)


@pytest.mark.parametrize("url", ["http://example.com:8787", "http://192.168.1.20:8787", "https://127.0.0.1:8787", "ftp://127.0.0.1"])
def test_only_this_pc_is_accepted(url):
    with pytest.raises(ValueError):
        CaprlmPrivacyFilter(url)


def test_available_reports_a_healthy_service(server):
    assert CaprlmPrivacyFilter(server({}).url).available()


def test_job_uses_the_service_when_it_is_up_and_keeps_no_text_when_it_is_not(server, tmp_path):
    import argparse

    from adaptation.scheduled_job import _build_screen_memory

    def args(url, dev=False):
        return argparse.Namespace(screen_memory=True, dev_passthrough_filter=dev, privacy_url=url, raw_db=None, memory_db=None,
                                  db=str(tmp_path / "context.db"), sm_high_threshold=0.5, sm_medium_threshold=0.2)

    up = _build_screen_memory(args(server({}).url))
    assert isinstance(up.config.privacy_filter, CaprlmPrivacyFilter)

    down = _build_screen_memory(args("http://127.0.0.1:9"))
    assert down.config.privacy_filter is None              # service missing: metadata-only, never unfiltered

    both = _build_screen_memory(args("http://127.0.0.1:9", dev=True))
    assert both.config.privacy_filter is None              # a dead service must not fall back to the unredacted dev filter
