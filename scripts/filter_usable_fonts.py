#!/usr/bin/env python3
"""Keep only fonts that can be applied in desktop design software in dist/fonts.json."""
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DESKTOP_FORMATS = {"ttf", "otf", "ttc", "otc"}

def emit(name, data):
    (DIST / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    index_path = DIST / "fonts.json"
    fonts = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    usable, excluded = [], []
    for font in fonts:
        fmt = str(font.get("format", "")).lower().lstrip(".")
        path = str(font.get("path", ""))
        source = font.get("source", "")
        if fmt in DESKTOP_FORMATS and path.startswith("fonts/"):
            usable.append(font)
            continue
        if fmt in {"woff", "woff2"}:
            reason = "Web-only WOFF/WOFF2 format; not supported for direct desktop font installation"
        elif source in {"google-fonts", "fontsource"} or fmt == "remote":
            reason = "Metadata-only record without a verified downloadable desktop font file"
        else:
            reason = "No supported desktop font file found"
        excluded.append({**font, "usable_in_desktop": False, "exclusion_reason": reason})

    # The full upstream metadata remains searchable/auditable separately; it is not
    # mixed into the installable desktop catalog.
    emit("online-fonts-unavailable.json", excluded)
    emit("fonts.json", usable)

    brands, categories = {}, {}
    for font in usable:
        bid = font.get("brand_id", "unknown")
        brands[bid] = {"id": bid, "name": font.get("brand_name", font.get("manufacturer", bid)),
                       "count": brands.get(bid, {}).get("count", 0) + 1}
        cat = font.get("category", "其他")
        categories[cat] = categories.get(cat, 0) + 1
    emit("brands.json", sorted(brands.values(), key=lambda x: x["id"]))
    emit("categories.json", [{"name": name, "count": count} for name, count in sorted(categories.items())])
    weights = sorted({(font.get("weight_class", 400), font.get("weight_name", "Regular")) for font in usable})
    filters = {
        "languages": sorted({tag for font in usable for tag in font.get("language_tags", [])}),
        "scripts": sorted({tag for font in usable for tag in font.get("script_tags", [])}),
        "categories": [{"name": name, "count": count} for name, count in sorted(categories.items())],
        "weights": [{"value": value, "label": label} for value, label in weights],
        "brands": sorted(brands.values(), key=lambda x: x["id"]),
        "licenses": sorted({font.get("license_type", "unknown") for font in usable}),
    }
    emit("filters.json", filters)
    manifest_path = DIST / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"schema_version": 1}
    manifest["catalog_count"] = len(usable)
    manifest["brand_count"] = len(brands)
    manifest["excluded_unusable_count"] = len(excluded)
    manifest["unavailable_fonts_url"] = "https://raw.githubusercontent.com/520yxl/asfdsfdsgfsdgdsf/main/dist/online-fonts-unavailable.json"
    emit("manifest.json", manifest)
    report = {
        "desktop_usable_count": len(usable),
        "excluded_count": len(excluded),
        "excluded_woff_count": sum(1 for f in excluded if str(f.get("format", "")).lower().lstrip(".") in {"woff", "woff2"}),
        "excluded_metadata_only_count": sum(1 for f in excluded if f.get("source") in {"google-fonts", "fontsource"} or f.get("format") == "remote"),
        "supported_formats": sorted(DESKTOP_FORMATS),
        "note": "Excluded entries are retained in online-fonts-unavailable.json for audit, not shown as installable desktop fonts."
    }
    emit("desktop-usability-report.json", report)
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
