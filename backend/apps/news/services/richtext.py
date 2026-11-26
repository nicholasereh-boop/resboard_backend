"""Turns the story-style editor document (TipTap JSON, the same schema Stories use) into the plain sanitised HTML that News keeps in
NewsArticle.body for search, word counts, duplicate detection and the rights gate. Images and videos stay in the JSON only (the public
page draws them from there); here they are simply left out. The result is passed through sanitize.clean_body() by the caller."""
from html import escape

_MARKS = {"bold": "strong", "italic": "em", "underline": "u"}


def _text(node):
    out = escape(node.get("text", ""))
    for mark in node.get("marks") or []:
        kind = mark.get("type")
        if kind in _MARKS:
            out = f"<{_MARKS[kind]}>{out}</{_MARKS[kind]}>"
        elif kind == "link":
            href = (mark.get("attrs") or {}).get("href", "")
            out = f'<a href="{escape(href, quote=True)}">{out}</a>'
    return out


def _inline(node):
    return "".join(_node(c) for c in node.get("content") or [])


def _node(node):
    kind = node.get("type")
    if kind == "text":
        return _text(node)
    if kind == "hardBreak":
        return "<br>"
    if kind == "paragraph":
        return f"<p>{_inline(node)}</p>"
    if kind == "heading":
        level = int((node.get("attrs") or {}).get("level") or 2)
        tag = "h2" if level <= 2 else "h3" if level == 3 else "h4"
        return f"<{tag}>{_inline(node)}</{tag}>"
    if kind in ("bulletList", "orderedList"):
        tag = "ul" if kind == "bulletList" else "ol"
        return f"<{tag}>{_inline(node)}</{tag}>"
    if kind == "listItem":
        return f"<li>{_inline(node)}</li>"
    if kind == "blockquote":
        return f"<blockquote>{_inline(node)}</blockquote>"
    if kind == "horizontalRule":
        return "<hr>"
    return ""  # image, youtube and anything unknown: not part of the text body


def doc_to_html(doc):
    if not isinstance(doc, dict):
        return ""
    return "".join(_node(n) for n in doc.get("content") or [])


def reading_minutes(text):
    words = len(str(text or "").split())
    return 0 if words == 0 else max(1, round(words / 200))
