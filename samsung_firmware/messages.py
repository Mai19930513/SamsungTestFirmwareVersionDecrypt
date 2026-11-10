"""Telegram digest layout and UTF-16-safe pagination."""

from .events import render_message
from .models import COUNTRIES
from .versions import version_key


def split_text(text, limit=3900):
    """Prefer whole lines/versions; Telegram limits UTF-16 units."""
    chunks, current, units = [], "", 0
    for line in text.splitlines(keepends=True):
        width = len(line.encode("utf-16-le")) // 2
        if units and units + width > limit:
            chunks.append(current.rstrip())
            current, units = "", 0
        for character in line:
            size = 2 if ord(character) > 65535 else 1
            if units + size > limit:
                chunks.append(current.rstrip())
                current, units = "", 0
            current += character
            units += size
    if current:
        chunks.append(current.rstrip())
    return chunks


def split_message(text, entities=(), limit=3900):
    """Keep Telegram text-link offsets correct after splitting, including emoji."""
    messages = []
    cursor = 0
    for chunk in split_text(text, limit):
        start = text.index(chunk, cursor)
        offset = len(text[:start].encode("utf-16-le")) // 2
        length = len(chunk.encode("utf-16-le")) // 2
        links = []
        for entity in entities:
            left = max(offset, entity["offset"])
            right = min(offset + length, entity["offset"] + entity["length"])
            if left < right:
                links.append({**entity, "offset": left - offset, "length": right - left})
        messages.append({"text": chunk, "entities": links})
        cursor = start + len(chunk)
    return messages


TITLES = {"test": "三星新增内测固件", "stable": "三星 FUS 新版本上线"}


def device_label(event, index):
    country = COUNTRIES.get(event["region"], event["region"])
    return f"{index}. #{event['model'].removeprefix('SM-')} - {event['name']}({country}版)"


def digest_blocks(entries, target, group_target, *, group_stable=True):
    selected = sorted((entry for entry in entries if target in entry["targets"]),
                      key=lambda entry: (entry["model"], entry["region"]))
    blocks = []
    for kind in ("test", "stable"):
        index = 0
        for entry in selected:
            if kind == "test":
                versions = sorted(set(entry.get("versions", [])),
                                  key=lambda v: (version_key(v), v), reverse=True)
                if not versions:
                    continue
                index += 1
                message = {"text": "\n".join([device_label(entry, index), *versions]), "entities": []}
            else:
                if not entry.get("stable") or (target == group_target and not group_stable):
                    continue
                index += 1
                message = render_message({**entry, "versions": []}, header=device_label(entry, index))
            blocks.append({"entry": entry["id"], "kind": kind, **message})
    return blocks


def digest_counts(blocks):
    return {kind: len({block["entry"] for block in blocks if block["kind"] == kind}) for kind in TITLES}


def render_digest_page(blocks, counts, page, total):
    text, entities, previous = "", [], None
    for block in blocks:
        if text:
            text += "\n\n"
        kind = block["kind"]
        if kind != previous:
            text += f"{TITLES[kind]}—共<{counts[kind]}>台机型({page}/{total})\n"
        offset = len(text.encode("utf-16-le")) // 2
        entities.extend({**entity, "offset": offset + entity["offset"]} for entity in block["entities"])
        text += block["text"]
        previous = kind
    return {"text": text, "entities": entities}


def digest_pages(entries, target, group_target=None, limit=3900, *, group_stable=True):
    """Prefer whole device blocks; freeze fragments so retries cannot repeat pages."""
    blocks = digest_blocks(entries, target, group_target, group_stable=group_stable)
    counts = digest_counts(blocks)
    digits = 1
    while True:
        pages, current = [], []
        placeholder = "9" * digits
        for block in blocks:
            heading = f"{TITLES[block['kind']]}—共<{counts[block['kind']]}>台机型({placeholder}/{placeholder})\n"
            budget = limit - len(heading.encode("utf-16-le")) // 2
            if budget < 2:
                raise ValueError("Telegram digest limit is too small for the title")
            for part in split_message(block["text"], block["entities"], budget):
                fragment = {**block, **part}
                candidate = render_digest_page([*current, fragment], counts, placeholder, placeholder)
                if len(candidate["text"].encode("utf-16-le")) // 2 > limit:
                    pages.append(current)
                    current = []
                current.append(fragment)
        if current:
            pages.append(current)
        if len(str(len(pages))) <= digits:
            return pages
        digits = len(str(len(pages)))
