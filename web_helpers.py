"""Offline article extraction from HTML already fetched by content_intake.

Trafilatura only receives a bounded HTML document. It never fetches links,
executes JavaScript, or replaces the application's public URL checks.
"""
from __future__ import annotations

import re


def extract_article(source, url, *, omit_tags=()):
    """Return article text and bounded source metadata, or an empty fallback.

    Short notices, malformed HTML, and pages without an article are handled by
    the caller's small HTML reader. Optional dependency failures use that same
    fallback so a webpage import remains usable after a partial installation.
    """
    try:
        from lxml import html
        from trafilatura import bare_extraction

        tree = html.document_fromstring(source, parser=html.HTMLParser(
            recover=True, no_network=True, huge_tree=False))
        omitted = set(omit_tags)
        # Remove hidden content before running extraction, including hidden
        # ancestors. Article engines can otherwise retain CSS-hidden notes.
        remove = []
        for node in tree.iter():
            if not isinstance(node.tag, str):
                continue
            style = re.sub(r"\s+", "", node.get("style", "")).casefold()
            hidden = (node.tag.casefold() in omitted or "hidden" in node.attrib
                      or node.get("aria-hidden", "").casefold() == "true"
                      or "display:none" in style or "visibility:hidden" in style
                      or node.get("role", "").casefold() in
                      ("navigation", "banner", "contentinfo"))
            if hidden:
                if node is tree:
                    return "", "", {}
                remove.append(node)
        for node in reversed(remove):
            if node.getparent() is not None:
                node.drop_tree()

        document = bare_extraction(tree, url=url, fast=True,
            favor_precision=True, with_metadata=True, include_comments=False,
            include_tables=True, include_images=False, include_links=False,
            date_extraction_params={"extensive_search": False})
        if document is None or not document.text:
            return "", "", {}
        metadata = {}
        for key, limit in (("author", 200), ("date", 40), ("sitename", 200),
                           ("description", 600)):
            value = getattr(document, key, None)
            if value:
                metadata[key] = str(value)[:limit]
        return str(document.title or "")[:300], str(document.text), metadata
    except MemoryError:
        raise
    except Exception:
        # Parsing third-party HTML must not prevent the basic text fallback.
        return "", "", {}
