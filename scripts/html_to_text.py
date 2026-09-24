#!/usr/bin/env python3
"""Visible text from HTML. The ONE implementation, shared by fetch and ingest.

WHY THIS IS ITS OWN MODULE
    There used to be two, and they disagreed about a whole class of page.

    sapme_fetch decided whether a response was a usable page or an empty shell
    with a regex:

        re.sub(r"<[^>]+>", " ", body)

    That strips TAGS but keeps what is between them, so the body of every
    inline <script> survives as apparent prose. A single-page-app shell is
    mostly inline JavaScript, so it cleared the MIN_USEFUL_HTML bar comfortably
    and was written to disk as content.

    sapme_ingest used the parser below, which skips script/style properly, and
    extracted zero characters from those same files.

    So the fetcher stored eleven copies of the SAP Discovery Center SPA shell
    (6,244 bytes each, byte-identical) and the ingest silently dropped all of
    them. No error at either end: one side thought it had content, the other
    thought the file was empty, and nothing compared the two.

    A shell detector and a text extractor must answer "is there any text here"
    the same way, or the fetcher's guard is measuring something the corpus
    never sees. Hence one module, imported by both. Kept dependency-free so
    sapme_fetch does not pull the ingest's docx/openpyxl/spacy chain in just to
    decide whether to keep a page.
"""

import re
from html.parser import HTMLParser


class _Text(HTMLParser):
    """Visible text from HTML. Drops script/style, keeps block boundaries."""

    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        t = re.sub(r"[ \t]+", " ", "".join(self.out))
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_text(raw):
    """Bytes of HTML -> the text a reader would see. Never raises."""
    p = _Text()
    try:
        p.feed(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw)
    except Exception:                                   # noqa: BLE001
        return ""
    return p.text()
