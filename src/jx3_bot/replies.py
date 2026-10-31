"""Business templates -> readable text or QQ custom Markdown (msg_type=2)."""
from functools import lru_cache
from pathlib import Path
import re
import tomllib
from string import Formatter

from .errors import ConfigurationError, ReplyTooLong
from .core.models import Reply, markdown_escape

MAX_REPLY_CHARS = 1800


def safe_value(value):
    text = str(value).replace("\r", " ").replace("\x00", "")
    return text.replace("@", "").replace("<", "").replace(">", "")


@lru_cache(maxsize=32)
def templates(module="weather"):
    if not isinstance(module, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", module):
        raise ConfigurationError("Invalid template module name")
    with (Path(__file__).parent / "modules" / module / "templates" / f"{module}.toml").open("rb") as handle:
        return tomllib.load(handle)


def validate_templates(module="weather"):
    # Keep the historical default, but let weather own its template contract.
    arguments = {}
    if module == "weather":
        from .modules.weather.rendering import TEMPLATE_ARGUMENTS
        arguments = TEMPLATE_ARGUMENTS
    required = set(arguments)
    try:
        specs = templates(module)
        if not specs or (module == "weather" and not required <= specs.keys()):
            raise ValueError
        for template_name, spec in specs.items():
            allowed = arguments.get(template_name, set()) if module == "weather" and template_name in required else None
            if set(spec) != {"title", "body", "footer"} or any(not isinstance(v, str) for v in spec.values()):
                raise ValueError
            for value in spec.values():
                for _, name, format_spec, conversion in Formatter().parse(value):
                    if name is not None and (not re.fullmatch(r"[a-z][a-z0-9_]*", name)
                                             or (allowed is not None and name not in allowed)
                                             or format_spec or conversion):
                        raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        raise ConfigurationError("Local reply templates are invalid; check titles/body/footer and named placeholders") from None


def render_result(name, *, module="weather", **values):
    spec = templates(module)[name]
    fields = {k: safe_value(v) for k, v in values.items()}
    return Reply(*(spec.get(key, "").format_map(fields) for key in ("title", "body", "footer")))


def message_payload(results, mode="text"):
    if mode not in {"text", "markdown"}:
        raise ConfigurationError("QQ_REPLY_FORMAT must be text or markdown")
    if not isinstance(results, (tuple, list)):
        results = [results]
    replies = [Reply(safe_value(r.title), safe_value(r.body), safe_value(r.footer))
               if isinstance(r, Reply) else Reply("", safe_value(r)) for r in results]
    text = "\n\n".join(r.text for r in replies)
    if not text or len(text) > MAX_REPLY_CHARS:
        raise ReplyTooLong("Reply is empty or exceeds the safe output limit")
    if mode == "markdown":
        content = "\n\n".join(r.markdown for r in replies)
        # Pre-POST fallback only. Never retry a rejected/ambiguous POST as text.
        if len(content) <= MAX_REPLY_CHARS:
            return {"msg_type": 2, "markdown": {"content": content}}
    return {"msg_type": 0, "content": text}


def validate_payload(payload):
    if not isinstance(payload, dict) or type(payload.get("msg_type")) is not int:
        raise ConfigurationError("Invalid reply payload")
    if payload.get("msg_type") == 0 and set(payload) == {"msg_type", "content"}:
        content = payload["content"]
    elif payload.get("msg_type") == 2 and set(payload) == {"msg_type", "markdown"}:
        markdown = payload["markdown"]
        if not isinstance(markdown, dict) or set(markdown) != {"content"}:
            raise ConfigurationError("Invalid Markdown reply payload")
        content = markdown["content"]
    else:
        raise ConfigurationError("Unsupported reply payload")
    if not isinstance(content, str) or not content or len(content) > MAX_REPLY_CHARS:
        raise ConfigurationError("Invalid reply content")
    return payload
