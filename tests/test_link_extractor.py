from __future__ import annotations

from src.worker.extractors.link import _extract_html_metadata, extract_urls_from_text


def test_extract_urls_from_text_deduplicates_and_strips_trailing_punctuation() -> None:
    text = """
    Review https://example.com/path, then compare with https://example.com/path.
    Also read (https://another.example.org/docs?q=1).
    """

    assert extract_urls_from_text(text) == [
        "https://example.com/path",
        "https://another.example.org/docs?q=1",
    ]


def test_extract_html_metadata_returns_title_description_and_body() -> None:
    html = """
    <html>
      <head>
        <title>Project Update</title>
        <meta name="description" content="Latest status for the launch" />
      </head>
      <body>
        <h1>Heading</h1>
        <p>We shipped the next milestone.</p>
      </body>
    </html>
    """

    extracted = _extract_html_metadata(html, "https://example.com/update")

    assert "URL: https://example.com/update" in extracted
    assert "Title: Project Update" in extracted
    assert "Latest status for the launch" in extracted
    assert "We shipped the next milestone." in extracted
