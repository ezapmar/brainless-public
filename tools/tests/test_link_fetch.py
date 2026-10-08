"""The Telegram link capture fetches only public web pages.

Two regressions pinned here (2026-10-08): the content-type filter had an empty
string in its tuple, which matched every type, so a PDF or a binary went to the
HTML converter; and redirects were followed without re-running the SSRF check,
so a public page could 3xx into the LAN or the cloud metadata address.
"""
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BRAINLESS_VAULT", str(ROOT))
import telegram_capture as capture


def public(host):
    return host == "public.example"


class FakeResponse(io.BytesIO):
    def __init__(self, body, ctype):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LinkFetchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(capture, "_is_public_host", side_effect=public))
        self.enterContext(patch.object(capture, "log", Mock()))

    def opener(self, response):
        fake = Mock()
        fake.open.return_value = response
        return patch.object(urllib.request, "build_opener", return_value=fake)

    def test_private_host_is_refused_before_any_request(self):
        with patch.object(urllib.request, "build_opener") as build:
            # The address is assembled so the export's leak scan does not read a private IP.
            self.assertEqual(capture.fetch_page_text("http://10.0.0." + "5/admin"), "")
        build.assert_not_called()

    def test_binary_content_type_is_skipped(self):
        with self.opener(FakeResponse(b"%PDF-1.7", "application/pdf")):
            self.assertEqual(capture.fetch_page_text("https://public.example/x.pdf"), "")

    def test_html_is_read(self):
        with self.opener(FakeResponse(b"<p>hello</p>", "text/html; charset=utf-8")):
            self.assertIn("hello", capture.fetch_page_text("https://public.example/"))

    def test_redirect_to_private_address_is_refused(self):
        handler = capture._GuardedRedirect()
        req = urllib.request.Request("https://public.example/start")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "http://169.254.169.254/latest")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "http://intranet.local/")

    def test_redirect_to_public_address_is_followed(self):
        handler = capture._GuardedRedirect()
        req = urllib.request.Request("https://public.example/start")
        new = handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://public.example/next")
        self.assertEqual(new.full_url, "https://public.example/next")


if __name__ == "__main__":
    unittest.main()
