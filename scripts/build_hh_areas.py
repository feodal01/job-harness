"""Build the HH area directory snapshot (src/parsers/aggregators/hh/data/areas.json.gz).

HH's own place hierarchy «country → region → city» (`GET https://api.hh.ru/areas`) in one version:
the HH normalizer reads it offline, so stored vacancies are enriched again with the same data.
The input is a local copy of the API response (nothing is downloaded here):

    curl -s -A "job-harness" https://api.hh.ru/areas -o /tmp/hh-areas.json
    uv run python scripts/build_hh_areas.py /tmp/hh-areas.json
"""

import argparse
import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

OUTPUT = Path(__file__).parents[1] / "src/parsers/aggregators/hh/data/areas.json.gz"
SOURCE = "https://api.hh.ru/areas"


def flatten(tree: list[dict]) -> dict[str, list]:
    """Area id → [name, parent id or None, has children]."""
    areas: dict[str, list] = {}

    def walk(node: dict, parent: str | None) -> None:
        children = node.get("areas") or []
        if node["id"] in areas:
            raise ValueError(f"Duplicate HH area id {node['id']}")
        areas[node["id"]] = [node["name"], parent, bool(children)]
        for child in children:
            walk(child, node["id"])

    for root in tree:
        walk(root, None)
    return areas


def build(source: Path, output: Path) -> dict:
    raw = source.read_bytes()
    areas = flatten(json.loads(raw))
    metadata = {
        "format_version": 1,
        "source": SOURCE,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "areas": len(areas),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"metadata": metadata, "areas": areas}, ensure_ascii=False, separators=(",", ":"))
    output.write_bytes(gzip.compress(payload.encode(), mtime=0))
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    print(json.dumps(build(**vars(parser.parse_args())), ensure_ascii=False))
