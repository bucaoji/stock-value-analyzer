import re
from html.parser import HTMLParser


class FilingHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag.lower() in {"p", "div", "tr", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        if tag.lower() in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        joined = " ".join(part.strip() for part in self.parts if part.strip())
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n\s*\n+", "\n\n", joined)
        return joined.strip()


def html_to_text(content: bytes) -> str:
    text = content.decode("utf-8", errors="ignore")
    if "<html" not in text[:2000].lower() and "<document" not in text[:2000].lower():
        return normalize_text(strip_tags(text))
    parser = FilingHTMLParser()
    try:
        parser.feed(text)
        return parser.text()
    except Exception:
        return normalize_text(strip_tags(text))


def strip_tags(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def normalize_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sentences_containing(text: str, keywords, limit: int = 8):
    sentence_pattern = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_pattern.split(re.sub(r"\s+", " ", text))
    hits = []
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for sentence in sentences:
        lower = sentence.lower()
        if any(keyword in lower for keyword in lowered_keywords):
            clean = sentence.strip()
            if 80 <= len(clean) <= 600 and clean not in hits:
                hits.append(clean)
        if len(hits) >= limit:
            break
    return hits
