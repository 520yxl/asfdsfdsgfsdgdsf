#!/usr/bin/env python3
"""Publish bounded, checksummed shards without removing the legacy catalog."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024
TARGET_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 1000

def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

def publish(dist):
    fonts = json.loads((dist / "fonts.json").read_text(encoding="utf-8"))
    if not fonts:
        raise ValueError("Empty catalog")
    shards, batch, size = [], [], 3
    def flush():
        if not batch:
            return
        data = encode(batch)
        if len(data) >= MAX_BYTES:
            raise ValueError("A font record exceeds the shard byte limit")
        sha = hashlib.sha256(data).hexdigest()
        name = f"fonts-{len(shards)+1:03d}-{sha[:16]}.json"
        (dist / name).write_bytes(data)
        shards.append({"path": "dist/" + name, "count": len(batch), "size": len(data), "sha256": sha})
    for font in fonts:
        if font.get("format") not in {"ttf", "otf"}:
            raise ValueError("Unsupported desktop font format")
        record_size = len(encode(font))
        if batch and (len(batch) >= MAX_RECORDS or size + record_size > TARGET_BYTES):
            flush()
            batch, size = [], 3
        batch.append(font)
        size += record_size
    flush()
    manifest_path = dist / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    brands = (dist / "brands.json").read_bytes()
    manifest.update({"schema_version": 2, "catalog_count": len(fonts), "fonts_shards": shards,
                     "brands_index": {"path": "dist/brands.json", "size": len(brands),
                                      "sha256": hashlib.sha256(brands).hexdigest()}})
    # Publish the manifest last; content-derived names reject stale mirror data.
    manifest_path.write_bytes(encode(manifest))
    keep = {Path(shard["path"]).name for shard in shards}
    for path in dist.glob("fonts-*.json"):
        if path.name not in keep:
            path.unlink()
    validate(dist)
    print(f"Published {len(fonts)} fonts in {len(shards)} shards; largest {max(s['size'] for s in shards)} bytes", flush=True)
    return manifest

def validate(dist):
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    count, seen = 0, set()
    for shard in manifest["fonts_shards"] + [manifest["brands_index"]]:
        path = Path(shard["path"])
        if path.parent.as_posix() != "dist" or path.name in seen:
            raise ValueError("Invalid or duplicate index path")
        seen.add(path.name)
        data = (dist / path.name).read_bytes()
        if len(data) != shard["size"] or len(data) >= MAX_BYTES or hashlib.sha256(data).hexdigest() != shard["sha256"]:
            raise ValueError("Index integrity check failed")
        records = json.loads(data)
        if "count" in shard:
            if len(records) != shard["count"] or not records or not all(f["format"] in {"ttf", "otf"} for f in records):
                raise ValueError("Invalid shard records")
            count += len(records)
    if count != manifest["catalog_count"]:
        raise ValueError("Catalog count mismatch")

if __name__ == "__main__":
    import sys
    (validate if "--check" in sys.argv else publish)(ROOT / "dist")
