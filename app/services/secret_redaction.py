"""
Hides API keys in text before it is remembered or shown to the model.

Used by the conversation memory (when saving messages and when loading old ones) and by the Slack key capture.
A value counts as a key when its NAME has a key word (key, token, secret, password, credential) or the value itself
looks like a known provider key.
"""
import re

# The key word may be anywhere in the name as its own word: TEST_API_KEY, KEY_FOR_SHOPIFY, MY-SECRET-1 ...
NAME_LOOKS_LIKE_KEY = re.compile(r"(?:^|[_.\-])(?:keys?|tokens?|secrets?|passwords?|passwd|credentials?)(?:$|[_.\-]|\d)", re.I)

_KEY_SHAPES = (
    r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|shpat_[A-Za-z0-9]{10,}"
)
# A value that is clearly an API key counts whatever the name is.
VALUE_LOOKS_LIKE_KEY = re.compile(rf"^(?:{_KEY_SHAPES})")
_KEY_ANYWHERE = re.compile(rf"(?<![A-Za-z0-9_-])(?:{_KEY_SHAPES})")
_QUERY_PARAM = re.compile(r"([?&])([A-Za-z][A-Za-z0-9_.-]{1,49})=([^\s&#]{8,})")
_ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{1,49})(\s*[=:]\s*)[`'\"*]?(\S{8,})")

HIDDEN = "[hidden]"


def redact_secrets(text: str) -> str:
    """Replaces key values (NAME = value pairs with a key-like name, and key-shaped strings) with [hidden]."""
    if not text:
        return text

    def _assign(m: "re.Match[str]") -> str:
        name, sep, value = m.group(1), m.group(2), m.group(3)
        if value.startswith("[hidden") or value.startswith("["):
            return m.group(0)
        if NAME_LOOKS_LIKE_KEY.search(name) or VALUE_LOOKS_LIKE_KEY.match(value):
            return f"{name}{sep}{HIDDEN}"
        return m.group(0)

    def _query(m: "re.Match[str]") -> str:
        if NAME_LOOKS_LIKE_KEY.search(m.group(2)) or VALUE_LOOKS_LIKE_KEY.match(m.group(3)):
            return f"{m.group(1)}{m.group(2)}={HIDDEN}"
        return m.group(0)

    out = _QUERY_PARAM.sub(_query, text)  # URL parameters first, so a leading "https:" can't hide them from the next pass
    out = _ASSIGNMENT.sub(_assign, out)
    return _KEY_ANYWHERE.sub(HIDDEN, out)
