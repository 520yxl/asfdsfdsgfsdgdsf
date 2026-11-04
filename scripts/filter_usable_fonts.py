#!/usr/bin/env python3
"""Keep only fonts that can be applied in desktop design software in dist/fonts.json."""
import json
import re
from urllib.parse import urlparse, unquote
from pathlib import Path
from taxonomy import enrich, filter_indexes
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DESKTOP_FORMATS = {"ttf", "otf"}

def compact_catalog(fonts):
    """Omit inherited face fields; desktop readers fall back to item metadata."""
    result = []
    for font in fonts:
        item = dict(font)
        if "faces" in font:
            item["faces"] = [
                {key: value for key, value in face.items()
                 if key in {"family", "style", "manufacturer", "full_name"}
                 or key not in font or value != font[key]}
                for face in font["faces"]
            ]
        result.append(item)
    return result


def emit(name, data):
    if name == "fonts.json":
        text = json.dumps(compact_catalog(data), ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    (DIST / name).write_text(text + "\n", encoding="utf-8")

def usable_font(font):
    fmt = str(font.get("format", "")).lower().lstrip(".")
    path = str(font.get("path", ""))
    if fmt not in DESKTOP_FORMATS or Path(path).suffix.lower() != "." + fmt:
        return False
    if not path.startswith("fonts/") or any(x in {"", ".", ".."} for x in path.split("/")) or "\\" in path:
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(font.get("sha256", ""))) or font.get("size", 0) <= 0:
        return False
    source = font.get("source")
    if source in {"google-fonts", "fontsource"}:
        repo = "google/fonts" if source == "google-fonts" else "fontsource/font-files"
        url = urlparse(font.get("download_url", ""))
        parts = unquote(url.path).strip("/").split("/")
        prefix = "fonts/" + source + "/"
        return (url.scheme == "https" and url.netloc == "raw.githubusercontent.com" and not url.query and not url.fragment
                and len(parts) >= 5 and "/".join(parts[:2]) == repo and re.fullmatch(r"[0-9a-f]{40}", parts[2]) is not None
                and (parts[3] in {"ofl", "apache", "ufl", "cc-by-sa"} if source == "google-fonts" else parts[3] == "sources")
                and path.startswith(prefix) and path[len(prefix):] == "/".join(parts[3:]))
    return True


def main():
    index_path = DIST / "fonts.json"
    fonts = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    usable, excluded = [], []
    for font in fonts:
        fmt = str(font.get("format", "")).lower().lstrip(".")
        path = str(font.get("path", ""))
        source = font.get("source", "")
        if usable_font(font):
            usable.append(font)
            continue
        if fmt in {"woff", "woff2"}:
            reason = "Web-only WOFF/WOFF2 format; not supported for direct desktop font installation"
        elif fmt == "remote" or not font.get("download_url"):
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
    taxonomy_indexes = filter_indexes(usable)
    filters = {
        "regions": taxonomy_indexes["regions"],
        "languages": taxonomy_indexes["languages"],
        "scripts": taxonomy_indexes["scripts"],
        "categories": [{"name": name, "count": count} for name, count in sorted(categories.items())],
        "weights": [{"value": value, "label": label} for value, label in weights],
        "brands": sorted(brands.values(), key=lambda x: x["id"]),
        "licenses": sorted({font.get("license_type", "unknown") for font in usable}),
    }
    emit("filters.json", filters)
    for key in ("regions", "languages", "scripts"):
        emit(key + ".json", taxonomy_indexes[key])
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
        "excluded_metadata_only_count": sum(1 for f in excluded if f.get("format") == "remote" or not f.get("download_url")),
        "supported_formats": sorted(DESKTOP_FORMATS),
        "note": "Excluded entries are retained in online-fonts-unavailable.json for audit, not shown as installable desktop fonts."
    }
    emit("desktop-usability-report.json", report)
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()

