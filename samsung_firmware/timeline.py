import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urljoin

from lxml import etree

from .files import atomic_json, load_json, now_text
from .network import FetchError


def parse_rows(content):
    root = etree.HTML(content)
    if root is None:
        return []
    items = []
    for row in root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' row ')]"):
        cells = row.xpath(".//div[contains(@class, 'col-md-3')]")
        if len(cells) < 4:
            continue
        values = [re.sub(r"\s+", " ", "".join(cell.itertext())).replace("：", ":").strip() for cell in cells[:4]]
        if any(":" not in value for value in values):
            continue
        values = [value.split(":", 1)[1].strip() for value in values]
        if not re.fullmatch(r"[A-Z0-9]{8,32}", values[0]):
            continue
        items.append(dict(zip(("版本号", "Android版本", "发布日期", "安全补丁级别"), values)))
    items.reverse()
    for index, item in enumerate(items, 1):
        item["index"] = index
    return items


def update_timeline(settings, client, models, pairs, force=False):
    path = settings.output_dir / "更新时间线.json"
    timeline = load_json(path, {"models": {}})
    timeline.setdefault("models", {})

    def fetch(pair):
        model, region = pair
        previous = timeline["models"].get(model, {}).get("cc", {}).get(region, {})
        checked = previous.get("last_check_time") or previous.get("last_update_time")
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(checked)).total_seconds() if checked else float("inf")
        except ValueError:
            elapsed = float("inf")
        if not force and elapsed < 3 * 86400:
            return model, region, previous
        result = dict(previous)
        result["last_check_time"] = now_text()
        entry = f"https://doc.samsungmobile.com/{model}/{region}/doc.html"
        try:
            root = etree.HTML(client.get(entry, force))
            links = root.xpath("//input[@id='dflt_page']/@value") if root is not None else []
            if not links:
                raise FetchError("no_changelog_link")
            url = urljoin(entry, links[0].strip())
            logs = parse_rows(client.get(url, force))
            if not logs:
                raise FetchError("no_changelog_rows")
            result.update({"real_url": url, "logs": logs, "last_update_time": now_text(), "status": "ok"})
        except FetchError as error:
            result["status"] = error.code
        return model, region, result

    with ThreadPoolExecutor(max_workers=settings.io_workers) as pool:
        for model, region, record in pool.map(fetch, pairs):
            entry = timeline["models"].setdefault(model, {"name": models[model]["name"], "cc": {}})
            entry.setdefault("cc", {})[region] = record
            entry["last_update_time"] = max((value.get("last_update_time", "") for value in entry["cc"].values()), default="")
    timeline["生成时间"] = now_text()
    atomic_json(path, timeline)
