"""Merge per-engine shard lists into one content/manifest.json.

Engine A (`qbank generate`) and Engine C (`qbank import`) write into the same
`content/` tree but run independently. Each owns the shards under
`imported/<engine>/`; rebuilding one must not drop the other's. This module
reads the manifest that is already there, swaps in the caller's shards, and
rewrites -- keeping a per-engine provenance block and a combined `source`
string so a downstream consumer (the Worker's build step) sees one flat list.

The Worker's `build-data.mjs` reads `generated`, `source`, `seed` and `shards`;
those four keys are always present in the output here.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

# Keys that belong to no single engine and are carried forward untouched when a
# later run rewrites the manifest without supplying them.
_CARRIED = ("seed", "difficulty_thresholds")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def shard_entry(
    out_root: Path,
    path: Path,
    *,
    category: str,
    difficulty: str,
    engine: str,
    source: str,
    license: str,
) -> dict:
    """One manifest row for a written .jsonl shard, hashed on the spot."""
    raw = Path(path).read_bytes()
    return {
        "path": str(Path(path).relative_to(out_root)).replace("\\", "/"),
        "category": category,
        "difficulty": difficulty,
        "count": sum(1 for line in raw.splitlines() if line.strip()),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "engine": engine,
        "source": source,
        "license": license,
    }


def write_merged(
    out_root: Path | str,
    *,
    engine: str,
    engine_meta: dict,
    shards: list[dict],
    extra_top: dict | None = None,
    default_top: dict | None = None,
) -> dict:
    """Replace this engine's shards in content/manifest.json, keep the rest.

    `engine` is the subdirectory name under `content/imported/` (``wikidata``
    for Engine A, ``opentdb`` for Engine C). `engine_meta` is stored under
    ``generators[engine]`` and feeds the combined ``source``/``sources`` fields.

    `extra_top` keys always win; `default_top` keys are written only when the
    manifest does not carry them already (a seed the other engine owns is kept).
    """
    out_root = Path(out_root)
    manifest_path = out_root / "manifest.json"
    existing: dict = {}
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Every shard the caller does not own is kept. Match on the `engine` tag, and
    # fall back to the path prefix for manifests written before that tag existed.
    prefix = f"imported/{engine}/"
    kept = [
        s for s in existing.get("shards", [])
        if s.get("engine") != engine and not s.get("path", "").startswith(prefix)
    ]
    # Backfill provenance onto shards from a pre-tag manifest so the merged file
    # is not half-tagged. Only Engine A ever wrote one, under imported/wikidata/.
    for s in kept:
        if "engine" not in s and s.get("path", "").startswith("imported/wikidata/"):
            s["engine"], s["source"], s["license"] = "wikidata", "Wikidata", "CC0"
    merged = [dict(s) for s in kept] + list(shards)
    # Deterministic, and stable when two engines both feed one (category,
    # difficulty) cell -- the Worker bakes global indexes from this order.
    merged.sort(key=lambda s: (s["category"], s["difficulty"], s["path"]))

    generators: dict = dict(existing.get("generators", {}))
    if not generators and existing.get("generator"):
        # Migrate a legacy single-engine manifest (Engine A wrote flat keys).
        # Its top-level `source` was the decorated "Wikidata (CC0)"; store the
        # bare name so the combined `sources` string does not read "(CC0) (CC0)".
        generators["wikidata"] = {
            "engine": existing.get("generator", "qbank engine-a"),
            "source": "Wikidata",
            "license": "CC0",
            "generated": existing.get("generated"),
            "seed": existing.get("seed"),
        }
    generators[engine] = engine_meta

    sources: list[str] = []
    for meta in generators.values():
        name, lic = meta.get("source"), meta.get("license")
        tag = f"{name} ({lic})" if name and lic else name
        if tag and tag not in sources:
            sources.append(tag)

    manifest: dict = {
        "generated": _now(),
        "generators": generators,
        "source": "; ".join(sources),
        "sources": sources,
    }
    for key in _CARRIED:
        if key in existing:
            manifest[key] = existing[key]
    for key, value in (default_top or {}).items():
        manifest.setdefault(key, value)
    if extra_top:
        manifest.update(extra_top)
    manifest["total"] = sum(s["count"] for s in merged)
    manifest["shards"] = merged

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
