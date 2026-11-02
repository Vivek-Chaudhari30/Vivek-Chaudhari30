"""Replace marker-delimited blocks in README.md, leaving everything else untouched."""

from __future__ import annotations

import re


class MarkerError(ValueError):
    """Raised when a block's START/END markers are missing or duplicated."""


def markers(name: str) -> tuple[str, str]:
    return f"<!-- {name}:START -->", f"<!-- {name}:END -->"


def replace_block(text: str, name: str, content: str) -> str:
    start, end = markers(name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise MarkerError(f"Expected exactly one {start} … {end} block, found {len(matches)}")
    replacement = f"{start}\n{content.strip()}\n{end}"
    return pattern.sub(lambda _: replacement, text, count=1)
