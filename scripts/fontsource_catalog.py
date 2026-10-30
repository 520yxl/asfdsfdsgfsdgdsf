#!/usr/bin/env python3
"""Build a Fontsource metadata-only index from GitHub raw files; never clone or download font binaries."""
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from taxonomy import enrich, filter_indexes
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
FONTLIST_URL = "https://raw.githubusercontent.com/fontsource/font-files/main/FONTLIST.json"
FAMILY_BASE = "https://raw.githubusercontent.com/fontsource/fontsource/main/registry/data/families"

def norm(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in value if ch.isalnum())

def fetch_json(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "MoxYi-Font-Index/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")

def category(classifications):
    values = " ".join(classifications or []).casefold()
    if "handwriting" in values or "script" in values:
        return "手写体"
    if "display" in values:
        return "艺术体"
    if "serif" in values and "sans" not in values:
        return "宋体"
    if "sans-serif" in values or "sans serif" in values:
        return "黑体"
    return "其他"

def language_tags(languages):
    tags = set()
    for item in languages or []:
        parts = str(item).replace("-", "_").split("_")
        lang = (parts[0] if parts else "").casefold()
        script = (parts[1] if len(parts) > 1 else "").casefold()
        if lang in {"zh", "zho", "chi"}:
            tags.add("zh-Hant" if script in {"hant", "hans"} and script == "hant" else "zh-Hans")
        elif lang in {"ja", "jpn"}:
            tags.add("ja")
        elif lang in {"ko", "kor"}:
            tags.add("ko")
        elif lang in {"en", "eng"}:
            tags.add("en")
        elif lang:
            tags.add(lang)
    return sorted(tags)

def license_info(text):
    value = (text or "").casefold()
    if "sil open font license" in value or "ofl" in value:
        return "OFL", True, True
    if "apache license" in value or "apache-2.0" in value:
        return "Apache-2.0", True, True
    if "ubuntu font license" in value:
        return "UFL", True, True
    if "gnu general public license" in value or "gpl" in value:
        return "GPL", True, True
    return "unknown", None, None

def main():
    fontlist = fetch_json(FONTLIST_URL)
    index_path = DIST / "fonts.json"
    existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    # Existing local and Google Fonts entries win; Fontsource is only an additional
    # source for families not already represented in the catalog.
    merged = [item for item in existing if item.get("source") != "fontsource"]
    seen = set()
    for item in merged:
        for key in ("family", "full_name", "family_id"):
            value = item.get(key)
            if value:
                seen.add(norm(value))
        for value in item.get("searchable_names", []) + item.get("chinese_names", []):
            seen.add(norm(value))
    added = 0
    duplicates = 0
    errors = []
    for slug, updated in sorted(fontlist.items()):
        slug = str(slug).strip()
        slug_key = norm(slug)
        if not slug_key or slug_key in seen:
            duplicates += 1
            continue
        meta = None
        source_kind = None
        # Prefer the project's own font families, then Google family metadata.
        for kind in ("fontsource", "google"):
            url = f"{FAMILY_BASE}/{kind}/{quote(slug, safe='')}/family.json"
            try:
                meta = fetch_json(url, timeout=20)
                source_kind = kind
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                continue
        if not isinstance(meta, dict):
            errors.append(slug)
            continue
        family = str(meta.get("family") or slug.replace("-", " ").title()).strip()
        family_key = norm(family)
        if family_key in seen:
            duplicates += 1
            continue
        designer = str(meta.get("designer") or "").strip()
        classifications = meta.get("classifications") or []
        languages = meta.get("languages") or []
        license_text = ""
        license_url = f"{FAMILY_BASE}/{source_kind}/{quote(slug, safe='')}/license.txt"
        try:
            req = urllib.request.Request(license_url, headers={"User-Agent": "MoxYi-Font-Index/1.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                license_text = response.read(256 * 1024).decode("utf-8", errors="replace")
        except Exception:
            pass
        license_type, commercial_use, redistribution_allowed = license_info(license_text)
        stable = hashlib.sha256(("fontsource:" + slug_key).encode("utf-8")).hexdigest()
        aliases = list(dict.fromkeys([family, slug, slug.replace("-", " ")]))
        record = {
            "id": stable[:20],
            "family": family,
            "style": "Variable" if meta.get("axes") else "Regular",
            "full_name": family,
            "manufacturer": designer,
            "brand_id": "unknown",
            "brand_name": "待核实",
            "family_id": slug,
            "category": category(classifications),
            "faces": [],
            "format": "remote",
            "size": 0,
            "sha256": stable,
            "path": "fontsource/" + slug,
            "download_url": "",
            "source": "fontsource",
            "source_url": f"https://fontsource.org/fonts/{slug}",
            "source_repository": "https://github.com/fontsource/fontsource",
            "upstream_kind": source_kind,
            "upstream_updated": updated,
            "official_files": {},
            "chinese_names": [],
            "searchable_names": aliases,
            "license": license_text[:3000],
            "license_type": license_type,
            "license_url": license_url if license_text else "",
            "commercial_use": commercial_use,
            "redistribution_allowed": redistribution_allowed,
            "language_tags": language_tags(languages),
            "script_tags": sorted({str(item).split("_", 1)[-1] for item in languages if "_" in str(item)}),
            "subsets": [],
            "axes": meta.get("axes") or [],
            "weight_class": 400,
            "weight_name": "Variable" if meta.get("axes") else "Regular",
            "weight_range": [next((axis.get("min") for axis in (meta.get("axes") or []) if axis.get("tag") == "wght"), 400),
                             next((axis.get("max") for axis in (meta.get("axes") or []) if axis.get("tag") == "wght"), 400)],
        }
        record = enrich(record, languages=languages, regions=meta.get("region_tags", meta.get("regions", [])), scripts=record.get("script_tags", []))
        merged.append(record)
        seen.update({norm(family), norm(slug), norm(slug.replace("-", " "))})
        added += 1
        # Avoid hammering raw GitHub while resolving metadata for new families.
        time.sleep(0.03)
    merged.sort(key=lambda obj: (str(obj.get("brand_id", "")).casefold(), str(obj.get("family", "")).casefold(), str(obj.get("style", "")).casefold()))
    DIST.mkdir(exist_ok=True)
    index_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Rebuild shared filter options from the final merged catalog, not from a single source.
    brand_counts = {}
    category_counts = {}
    for obj in merged:
        bid = obj.get("brand_id", "unknown")
        brand_counts[bid] = {"id": bid, "name": obj.get("brand_name", obj.get("manufacturer", bid)), "count": brand_counts.get(bid, {}).get("count", 0) + 1}
        cat = obj.get("category", "其他")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    weights = sorted({(obj.get("weight_class", 400), obj.get("weight_name", "Regular")) for obj in merged}, key=lambda x: x[0])
    taxonomy_indexes = filter_indexes(merged)
    filters = {
        "regions": taxonomy_indexes["regions"],
        "languages": taxonomy_indexes["languages"],
        "scripts": taxonomy_indexes["scripts"],
        "categories": [{"name": name, "count": count} for name, count in sorted(category_counts.items())],
        "weights": [{"value": value, "label": label} for value, label in weights],
        "brands": sorted(brand_counts.values(), key=lambda x: x["id"]),
        "licenses": sorted({obj.get("license_type", "unknown") for obj in merged}),
    }
    (DIST / "filters.json").write_text(json.dumps(filters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path = DIST / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["catalog_count"] = len(merged)
        manifest["brand_count"] = len(brand_counts)
        manifest["fontsource_report_url"] = "https://raw.githubusercontent.com/520yxl/asfdsfdsgfsdgdsf/main/dist/fontsource-index-report.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"source": "fontsource", "upstream_count": len(fontlist), "added": added, "skipped_duplicates": duplicates, "metadata_errors": errors, "index_count": len(merged)}
    (DIST / "fontsource-index-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
