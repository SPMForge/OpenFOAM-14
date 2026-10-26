from __future__ import annotations

import re


_STABLE_TAG_PATTERN = re.compile(r"^refs/(?:tags|upstream-tags)/(\d{8})$")


def select_latest_stable_tag(refs: list[str]) -> str:
    stable_tags = []

    for ref in refs:
        match = _STABLE_TAG_PATTERN.match(ref)
        if match:
            stable_tags.append(match.group(1))

    if not stable_tags:
        raise ValueError("no stable upstream tags found")

    stable_tags.sort()
    return stable_tags[-1]
