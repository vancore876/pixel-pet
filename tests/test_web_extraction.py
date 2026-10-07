"""Offline article extraction and malformed public search regressions."""
from pathlib import Path
from email.message import Message
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import content_intake as intake
import web_helpers


ARTICLE = """<html><head><title>Daily health guide</title>
<meta property="og:title" content="Preparing for your appointment">
<meta name="description" content="A short clinic preparation guide.">
<meta property="article:published_time" content="2026-10-07T08:00:00Z">
</head><body><div class="related">Shop sponsored offers and promotional links.</div>
<main><article><h1>Preparing for your appointment</h1>
<p>Bring identification and your appointment letter. Arrive fifteen minutes before
your visit so reception can check your details.</p>
<p>Write down the medicines you take and the questions you want to ask. The clinic
recommends keeping a copy of your records for follow-up visits.</p>
<p>Remember to call the clinic if you need to reschedule. Staff can help you choose
a convenient time and explain which documents to bring.</p>
<p hidden>Hidden patient details.</p><div aria-hidden="true"><p>Secret patient details.</p></div>
<p style="display: none">Invisible details.</p>
</article></main><nav>Menu offers</nav><script>run_untrusted_code()</script></body></html>"""


class ArticleExtractionTests(unittest.TestCase):
    def fetch(self, source, mime="text/html"):
        return patch.object(intake, "fetch_public_bytes",
            return_value=("https://example.org/final", mime, source))

    def test_real_article_engine_keeps_metadata_and_excludes_boilerplate(self):
        with self.fetch(ARTICLE), patch("socket.create_connection",
                side_effect=AssertionError("article extraction must remain offline")):
            result = intake.extract_web("https://example.org/start")
        self.assertEqual(result["extractor"], "trafilatura")
        self.assertEqual(result["title"], "Preparing for your appointment")
        self.assertEqual(result["metadata"]["date"], "2026-10-07")
        self.assertEqual(result["metadata"]["description"], "A short clinic preparation guide.")
        self.assertEqual(result["sources"], [{"title": result["title"], "url": "https://example.org/final"}])
        self.assertIn("Bring identification", result["text"])
        self.assertIn("reschedule", result["text"])
        for unwanted in ("sponsored", "Hidden patient", "Secret patient", "Invisible details", "Menu offers", "run_untrusted_code"):
            self.assertNotIn(unwanted, result["text"])

    def test_short_page_uses_reader_when_article_detection_has_no_result(self):
        with self.fetch("<title>Clinic hours</title><p>Monday: 9 AM.</p>"), \
                patch("trafilatura.bare_extraction", return_value=None):
            result = intake.extract_web("https://example.org/")
        self.assertEqual(result["extractor"], "html")
        self.assertEqual(result["title"], "Clinic hours")
        self.assertEqual(result["text"], "Monday: 9 AM.")

    def test_library_parse_failure_preserves_basic_web_reading(self):
        with self.fetch("<title>Notice</title><p>Bring your ID.</p>"), \
                patch("trafilatura.bare_extraction", side_effect=ValueError("malformed tree")):
            result = intake.extract_web("https://example.org/")
        self.assertEqual(result["text"], "Bring your ID.")
        self.assertEqual(result["extractor"], "html")

    def test_plain_text_bypasses_article_detection_and_preserves_lines(self):
        with self.fetch("First task.\r\n\r\nSecond task.", mime="text/plain"), \
                patch.object(web_helpers, "extract_article") as extract:
            result = intake.extract_web("https://example.org/")
        extract.assert_not_called()
        self.assertEqual(result["extractor"], "plain")
        self.assertEqual(result["text"], "First task.\n\nSecond task.")

    def test_cancel_during_article_detection_releases_job_without_result(self):
        cancel = threading.Event()
        def canceled_extract(*args, **kwargs):
            cancel.set()
            return "Notice", "Bring your ID.", {}
        with self.fetch(ARTICLE), patch.object(web_helpers, "extract_article", side_effect=canceled_extract):
            with self.assertRaises(intake._Canceled):
                intake.extract_web("https://example.org/", cancel)

    def test_boolean_attributes_do_not_crash_fallback_reader(self):
        title, text = intake.ReadableHTML().read("<title>Notice</title><p style role aria-hidden>Visible.</p><div hidden><span>Hidden.</span></div>")
        self.assertEqual(title, "Notice")
        self.assertEqual(text, "Visible.")


class WebEncodingTests(unittest.TestCase):
    def fetch_payload(self, payload, content_type="text/html"):
        headers = Message()
        headers["Content-Type"] = content_type
        response = SimpleNamespace(headers=headers,
            geturl=lambda: "https://example.org/",
            read=Mock(side_effect=[payload, b""]))
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(intake, "_request_url", side_effect=lambda value: value), \
                patch("urllib.request.build_opener", return_value=opener):
            return intake.fetch_public_bytes("https://example.org/")[2]

    def test_html_meta_charset_preserves_accents_when_http_omits_charset(self):
        source = self.fetch_payload(b'<meta charset="windows-1252"><p>Caf\xe9 opens at noon.</p>')
        self.assertIn("Caf\u00e9", source)
        self.assertNotIn("\ufffd", source)

    def test_http_charset_has_priority_over_conflicting_html_metadata(self):
        payload = '<meta charset="windows-1252"><p>Caf\u00e9</p>'.encode("utf-8")
        source = self.fetch_payload(payload, "text/html; charset=utf-8")
        self.assertIn("Caf\u00e9", source)

    def test_unicode_bom_and_unknown_encoding_have_readable_fallbacks(self):
        for encoding in ("utf-16", "utf-32"):
            with self.subTest(encoding=encoding):
                source = self.fetch_payload("First task.\nSecond task.".encode(encoding), "text/plain")
                self.assertEqual(source, "First task.\nSecond task.")
        source = self.fetch_payload(b"\xef\xbb\xbfRemember the appointment.", "text/plain; charset=invalid-encoding")
        self.assertEqual(source, "Remember the appointment.")


class PublicSearchRegressionTests(unittest.TestCase):
    def test_malformed_ipv6_and_boolean_href_do_not_discard_valid_results(self):
        rows = intake._SearchHTML().read("""<a class="result__a" href="https://[bad">Broken</a>
            <a class="result__a" href>Empty link</a><a class>Unused</a>
            <a class="result__a" href="https://example.org/guide">Guide</a>""")
        self.assertEqual([row["title"] for row in rows], ["Guide"])

    def test_redirect_parameters_are_only_unwrapped_on_duckduckgo_hosts(self):
        rows = intake._SearchHTML().read("""<a class="result__a"
            href="https://notduckduckgo.com/?uddg=https%3A%2F%2Fexample.org%2F">Direct page</a>
            <a class="result__a" href="https://html.duckduckgo.com/html/">Search form</a>
            <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.net%2F">Real redirect</a>""")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["url"], "https://notduckduckgo.com/?uddg=https%3A%2F%2Fexample.org%2F")
        self.assertEqual(rows[1]["url"], "https://example.net/")

    def test_nested_snippet_nodes_keep_text_after_the_inner_div_closes(self):
        rows = intake._SearchHTML().read("""<a class="result__a" href="https://example.org/">Guide</a>
            <div class="result__snippet"><div>First sentence.</div> Last sentence.</div>""")
        self.assertEqual(rows[0]["snippet"], "First sentence. Last sentence.")

    def test_empty_class_and_long_untrusted_snippets_remain_bounded(self):
        source = '<a class>Ignored</a><a class="result__a" href="https://example.org/">' + "T" * 500 + '</a><div class="result__snippet">' + "x" * 2000 + "</div>"
        rows = intake._SearchHTML().read(source)
        self.assertEqual(len(rows[0]["title"]), 300)
        self.assertEqual(len(rows[0]["snippet"]), 1200)


if __name__ == "__main__":
    unittest.main()
