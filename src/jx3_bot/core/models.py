"""Shared command, match context and reply data; no weather or QQ DTOs."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Command:
    kind: str
    argument: str = ""
    matcher: str = ""


@dataclass(frozen=True)
class MatchContext:
    scene: str
    app_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    to_me: bool = False


def markdown_escape(text):
    # Escape dynamic values and template text alike: no injected headings, links,
    # mentions, HTML or image fetches. Attribution URLs remain visible as text.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)


@dataclass(frozen=True)
class Reply:
    title: str
    body: str
    footer: str = ""

    @property
    def text(self):
        parts = [self.title, self.body]
        if self.footer:
            parts.extend(("────────", self.footer))
        return "\n".join(p for p in parts if p)

    @property
    def markdown(self):
        parts = ["## " + markdown_escape(self.title) if self.title else "", markdown_escape(self.body)]
        if self.footer:
            parts.append("\n".join("> " + markdown_escape(line) for line in self.footer.splitlines()))
        return "\n\n".join(p for p in parts if p)
