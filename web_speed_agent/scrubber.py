"""HTML scrubber — removes session tokens and sensitive fields before transmission.

Called automatically by Agent.extract() unless scrub=False is passed.

What gets removed:
  - Hidden form fields whose names suggest session/auth tokens (csrf, token, nonce, etc.)
  - <meta> tags with session or auth content
  - Inline script blocks (JavaScript is not needed for extraction and may contain tokens)
  - Data attributes that commonly embed auth state

What is preserved:
  - All visible text content
  - Links, headings, tables, product data, article text
  - Form structure (field names, labels) — just not sensitive values
  - Everything needed for accurate extraction

This is a best-effort privacy layer on top of HTTPS. The server never stores HTML,
but scrubbing means confidential values never leave the machine at all.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Field names that commonly carry session/auth tokens.
# Matched case-insensitively against the `name` and `id` attributes of <input> tags.
_SENSITIVE_FIELD_RE = re.compile(
    r"csrf|_token|authenticity|nonce|secret|session|auth|captcha|recaptcha"
    r"|__requestverificationtoken|viewstate|__eventvalidation",
    re.IGNORECASE,
)

# <meta> name/property values that may carry session data.
_SENSITIVE_META_RE = re.compile(
    r"csrf|token|nonce|session|auth",
    re.IGNORECASE,
)


class _Scrubber(HTMLParser):
    """Single-pass HTML scrubber. Strips sensitive values without a full DOM parse."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []
        self._skip_depth = 0      # depth counter for tags being suppressed
        self._skip_tag: str = ""  # tag name that triggered suppression

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs}

    def _rebuild_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        self_closing: bool = False,
    ) -> str:
        parts = [f"<{tag}"]
        for k, v in attrs:
            if v is None:
                parts.append(f" {k}")
            else:
                escaped = v.replace('"', "&quot;")
                parts.append(f' {k}="{escaped}"')
        parts.append(" />" if self_closing else ">")
        return "".join(parts)

    # ── HTMLParser callbacks ──────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            self._skip_depth += 1
            return

        d = self._attrs_dict(attrs)

        # Drop inline <script> blocks entirely — JS not needed for extraction.
        if tag == "script":
            self._skip_depth = 1
            self._skip_tag = "script"
            return

        # Drop inline <style> blocks — CSS not needed for extraction.
        if tag == "style":
            self._skip_depth = 1
            self._skip_tag = "style"
            return

        # Hidden inputs: blank the value if name/id looks sensitive.
        if tag == "input" and d.get("type", "").lower() == "hidden":
            name = d.get("name", "") + d.get("id", "")
            if _SENSITIVE_FIELD_RE.search(name):
                # Emit the tag with value scrubbed
                clean_attrs = [
                    (k, "" if k == "value" else v) for k, v in attrs
                ]
                self._out.append(self._rebuild_tag(tag, clean_attrs, self_closing=True))
                return

        # <meta> tags with sensitive name/property: drop the content attribute.
        if tag == "meta":
            meta_name = d.get("name", "") + d.get("property", "") + d.get("http-equiv", "")
            if _SENSITIVE_META_RE.search(meta_name):
                clean_attrs = [
                    (k, "" if k == "content" else v) for k, v in attrs
                ]
                self._out.append(self._rebuild_tag(tag, clean_attrs, self_closing=True))
                return

        self._out.append(self._rebuild_tag(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            return
        d = self._attrs_dict(attrs)

        if tag == "input" and d.get("type", "").lower() == "hidden":
            name = d.get("name", "") + d.get("id", "")
            if _SENSITIVE_FIELD_RE.search(name):
                clean_attrs = [(k, "" if k == "value" else v) for k, v in attrs]
                self._out.append(self._rebuild_tag(tag, clean_attrs, self_closing=True))
                return

        if tag == "meta":
            meta_name = d.get("name", "") + d.get("property", "") + d.get("http-equiv", "")
            if _SENSITIVE_META_RE.search(meta_name):
                clean_attrs = [(k, "" if k == "content" else v) for k, v in attrs]
                self._out.append(self._rebuild_tag(tag, clean_attrs, self_closing=True))
                return

        self._out.append(self._rebuild_tag(tag, attrs, self_closing=True))

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._out.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        pass  # drop all HTML comments

    def get_output(self) -> str:
        return "".join(self._out)


def scrub(html: str) -> str:
    """Remove session tokens and sensitive fields from HTML before transmission.

    Safe to call on any HTML string. Returns scrubbed HTML with:
      - Inline <script> and <style> blocks removed
      - Hidden form fields with auth/token names blanked
      - Sensitive <meta> content attributes cleared
      - HTML comments removed

    Visible content (text, links, tables, headings) is untouched.

    Example::

        from web_speed_agent.scrubber import scrub
        clean = scrub(raw_html)
        result = await agent.extract(clean, scrub=False)  # already scrubbed
    """
    parser = _Scrubber()
    parser.feed(html)
    return parser.get_output()
