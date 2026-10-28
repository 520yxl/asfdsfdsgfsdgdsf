#!/usr/bin/env python3
"""Fetch Google Fonts public catalog metadata without cloning or downloading font binaries.
Merges metadata-only family records into the existing dist/fonts.json schema.
"""
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CATALOG_URL = "https://fonts.google.com/metadata/fonts"
HEADERS = {"User-Agent": "MoxYi-Font-Index/1.0"}

def safe_id(value):
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value or "google-fonts"

def category(value):
    value = (value or "").casefold()
    if "serif" in value and "sans" not in value:
        return "宋体"
    if "handwriting" in value:
        return "手写体"
    if "display" in value:
        return "艺术体"
    if "sans" in value:
        return "黑体"
    return "其他"

def main():
    req = urllib.request.Request(CATALOG_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=90) as response:
        raw = response.read(80 * 1024 * 1024).decode("utf-8")
    # Google Fonts catalog responses are prefixed to prevent JSON hijacking.
    raw = raw.lstrip()
    if raw.startswith(")]}'"):
        raw = raw.split("\n", 1)[1]
    payload = json.loads(raw)
    families = payload.get("familyMetadataList", [])
    if not families:
        raise RuntimeError("Google Fonts catalog returned no familyMetadataList")
    index_path = DIST / "fonts.json"
    existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    # Rebuild only our remote entries, preserving local font-file entries.
    merged = [item for item in existing if item.get("source") != "google-fonts"]
    known = {str(item.get("family", "")).casefold() for item in merged}
    for item in families:
        family = (item.get("family") or "").strip()
        if not family:
            continue
        designer = (item.get("designer") or "Google Fonts contributor").strip()
        files = item.get("fonts") or {}
        if not isinstance(files, dict):
            files = {}
        stable = hashlib.sha256(("google-fonts:" + family.casefold()).encode("utf-8")).hexdigest()
        aliases = list(dict.fromkeys([family] + ([item.get("locales", {}).get("zh", family)] if isinstance(item.get("locales"), dict) else [])))
        record = {
            "id": stable[:20],
            "family": family,
            "style": "Variable" if item.get("axes") else "Regular",
            "full_name": family,
            "manufacturer": designer,
            "brand_id": safe_id(designer),
            "brand_name": designer,
            "family_id": safe_id(family),
            "category": category(item.get("category")),
            "faces": [],
            "format": "remote",
            "size": 0,
            "sha256": stable,
            "path": "google-fonts/" + quote(family, safe=""),
            "download_url": "",
            "source": "google-fonts",
            "source_url": "https://fonts.google.com/specimen/" + quote(family, safe=""),
            "official_files": files,
            "chinese_names": [name for name in aliases if any(0x3400 <= ord(ch) <= 0x9fff for ch in name)],
            "searchable_names": aliases,
            "license": "See official family metadata and source project before redistribution",
            "subsets": item.get("subsets", []),
            "axes": item.get("axes", []),
        }
        # Keep local records intact; remote entries remain separately identifiable.
        merged.append(record)
    merged.sort(key=lambda obj: (str(obj.get("brand_id", "")).casefold(), str(obj.get("family", "")).casefold(), str(obj.get("style", "")).casefold()))
    DIST.mkdir(exist_ok=True)
    index_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Google Fonts metadata families:", len(families), "merged index records:", len(merged))

if __name__ == "__main__":
    main()
