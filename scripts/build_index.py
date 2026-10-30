#!/usr/bin/env python3
"""Scan and organize fonts for MoxYi."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import quote
from fontTools.ttLib import TTFont, TTCollection
from taxonomy import enrich, filter_indexes

ROOT = Path(__file__).resolve().parents[1]
FONTS, INCOMING, DIST = (ROOT / folder for folder in ("fonts", "incoming", "dist"))
EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}
CATEGORIES = ["黑体", "宋体", "楷体", "圆体", "书法体", "手写体", "艺术体", "其他"]

def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def safe_id(text):
    # Keep CJK characters readable; GitHub supports Unicode directory names.
    cleaned = re.sub(r"[^a-z0-9㐀-鿿]+", "-", str(text).lower()).strip("-")
    return cleaned[:90] or ("u-" + hashlib.sha1(str(text).encode()).hexdigest()[:12])

def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()

def inspect(path):
    def collect(font):
        name_table = font["name"]
        data = {}
        for key, ids in {"family": [16, 1], "style": [17, 2], "full_name": [4],
                         "manufacturer": [8], "license": [13]}.items():
            strings = []
            for name_id in ids:
                for record in name_table.names:
                    if record.nameID == name_id:
                        try:
                            value = record.toUnicode().strip()
                            if value and value not in strings:
                                strings.append(value)
                        except Exception:
                            pass
                if strings:
                    break
            data[key] = strings[0] if strings else ""
        # Collect Chinese localized names so the frontend can search them.
        family_zh, full_name_zh, style_zh = [], [], []
        for record in name_table.names:
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if not value or not any(0x3400 <= ord(ch) <= 0x9fff for ch in value):
                continue
            target = None
            if record.nameID in (1, 16):
                target = family_zh
            elif record.nameID in (4, 6):
                target = full_name_zh
            elif record.nameID in (2, 17):
                target = style_zh
            if target is not None and value not in target:
                target.append(value)
        # Record explicit name-table language IDs and infer supported scripts from cmap.
        language_tags = set()
        for record in name_table.names:
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if not value:
                continue
            lang_id = getattr(record, "langID", None)
            if lang_id == 0x0404: language_tags.add("zh-Hant")
            elif lang_id == 0x0804: language_tags.add("zh-Hans")
            elif lang_id == 0x0411: language_tags.add("ja")
            elif lang_id == 0x0412: language_tags.add("ko")
            elif lang_id == 0x0409: language_tags.add("en")
        codepoints = set()
        try:
            cmap = font.getBestCmap() or {}
            codepoints = set(cmap.keys())
        except Exception:
            pass
        script_tags = []
        if any(0x3400 <= cp <= 0x9FFF or 0x20000 <= cp <= 0x3134F for cp in codepoints):
            script_tags.append("Han")
        if any(0x3040 <= cp <= 0x30FF for cp in codepoints):
            script_tags.append("Hiragana-Katakana")
        if any(0xAC00 <= cp <= 0xD7AF for cp in codepoints):
            script_tags.append("Hangul")
        if any(0x0041 <= cp <= 0x024F for cp in codepoints):
            script_tags.append("Latin")
        if any(0x0400 <= cp <= 0x052F for cp in codepoints):
            script_tags.append("Cyrillic")
        if any(0x0600 <= cp <= 0x06FF for cp in codepoints):
            script_tags.append("Arabic")
        if any(0x0590 <= cp <= 0x05FF for cp in codepoints):
            script_tags.append("Hebrew")
        data["family_zh"] = family_zh
        data["full_name_zh"] = full_name_zh
        data["style_zh"] = style_zh
        data["language_tags"] = sorted(language_tags)
        data["script_tags"] = script_tags
        data["unicode_codepoint_count"] = len(codepoints)
        weight_class = 400
        try:
            weight_class = int(font["OS/2"].usWeightClass)
        except Exception:
            pass
        weight_names = [(100, "Thin"), (200, "ExtraLight"), (300, "Light"), (400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold"), (900, "Black")]
        data["weight_class"] = weight_class
        data["weight_name"] = min(weight_names, key=lambda item: abs(item[0] - weight_class))[1]
        data["license_url"] = ""
        for record in name_table.names:
            if record.nameID == 14:
                try:
                    data["license_url"] = record.toUnicode().strip()
                    if data["license_url"]:
                        break
                except Exception:
                    pass
        data["license_type"] = "unknown"
        data["commercial_use"] = None
        data["redistribution_allowed"] = None
        data["family"] = data["family"] or (family_zh[0] if family_zh else "Unknown")
        data["style"] = data["style"] or (style_zh[0] if style_zh else "Regular")
        return data
    if path.suffix.lower() in {".ttc", ".otc"}:
        collection = TTCollection(str(path), lazy=True)
        try:
            return [collect(font) for font in collection.fonts]
        finally:
            collection.close()
    font = TTFont(str(path), lazy=True)
    try:
        return [collect(font)]
    finally:
        font.close()

def category(family, style):
    content = (family + " " + style).lower()
    groups = [
        ("圆体", ["圆体", "rounded"]),
        ("楷体", ["楷", "kai"]),
        ("宋体", ["宋", "serif", "ming", "mincho", "min2", "明体", "明朝", "song"]),
        ("书法体", ["书法", "calligraphy"]),
        ("手写体", ["手写", "handwriting"]),
        ("艺术体", ["艺术", "display"]),
        ("黑体", ["黑", "sans", "hei", "gothic"]),
    ]
    for label, keywords in groups:
        if any(word in content for word in keywords):
            return label
    return "其他"

def brand_for(info, rules, override):
    if override.get("brand"):
        value = override["brand"]
        for rule in rules:
            if value in (rule["id"], rule["name"]):
                return rule["id"], rule.get("name_zh") or rule["name"]
        return safe_id(value), value

    # Brand-prefix matching first: a known foundry name at the start of the
    # family/full name is stronger evidence than a keyword appearing anywhere.
    family = unicodedata.normalize("NFKC", info.get("family", "")).strip().casefold()
    full_name = unicodedata.normalize("NFKC", info.get("full_name", "")).strip().casefold()
    manufacturer = unicodedata.normalize("NFKC", info.get("manufacturer", "")).strip()
    prefix_matches = []
    contains_matches = []
    for rule in rules:
        for keyword in rule.get("keywords", []):
            key = unicodedata.normalize("NFKC", keyword).strip().casefold()
            if not key:
                continue
            if family.startswith(key) or full_name.startswith(key):
                prefix_matches.append((len(key), rule["id"], rule.get("name_zh") or rule["name"]))
            elif key in family or key in full_name or key in manufacturer.casefold():
                contains_matches.append((len(key), rule["id"], rule.get("name_zh") or rule["name"]))
    matches = prefix_matches or contains_matches
    if matches:
        _, key, label = sorted(matches, reverse=True)[0]
        return key, label

    # Use the embedded manufacturer only as a fallback when it looks like a
    # genuine foundry, not a font editor or operating-system vendor.
    manufacturer = re.sub(r"\\s+", " ", manufacturer)
    if manufacturer and manufacturer.casefold() not in {
        "unknown", "n/a", "none", "fontforge", "fontlab",
        "microsoft corporation", "adobe systems incorporated"
    }:
        return safe_id(manufacturer), manufacturer
    return "unknown", "未知品牌"

def raw_url(path):
    repo = os.environ.get("GITHUB_REPOSITORY", "520yxl/asfdsfdsgfsdgdsf")
    ref = os.environ.get("FONT_REF", "main")
    return "https://raw.githubusercontent.com/" + repo + "/" + quote(ref, safe="") + "/" + quote(path.as_posix(), safe="/")

def emit(name, obj):
    DIST.mkdir(exist_ok=True)
    (DIST / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    FONTS.mkdir(exist_ok=True)
    INCOMING.mkdir(exist_ok=True)
    rules = read_json(ROOT / "metadata/brands.json", {"rules": []}).get("rules", [])
    overrides = read_json(ROOT / "metadata/overrides.json", {})
    errors = []
    sources = [p for folder in (FONTS, INCOMING) for p in folder.rglob("*")
               if p.is_file() and p.suffix.lower() in EXTENSIONS]
    # Re-evaluate metadata for every font, including previously archived files.
    with tempfile.TemporaryDirectory(prefix="moxyi-stage-") as stage:
        planned = []
        occupied = {}
        for index, path in enumerate(sorted(sources)):
            try:
                info = inspect(path)[0]
                override = overrides.get(path.name, {})
                brand_id, brand_name = brand_for(info, rules, override)
                family_id = safe_id(override.get("family", info["family"]))
                brand_folder = safe_id(brand_name)
                target = FONTS / brand_folder / family_id / path.name
                sha = digest(path)
                if target in occupied and occupied[target] != sha:
                    target = target.with_name(target.stem + "-" + sha[:10] + target.suffix)
                occupied[target] = sha
                temporary = Path(stage) / str(index)
                shutil.copy2(path, temporary)
                planned.append((temporary, target, sha))
            except Exception as exc:
                errors.append({"path": path.relative_to(ROOT).as_posix(), "error": str(exc)})
        if not errors:
            for temporary, target, sha in planned:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or digest(target) != sha:
                    shutil.copy2(temporary, target)
            keep = {target for _, target, _ in planned}
            for path in sources:
                if path not in keep:
                    path.unlink()
            for folder in (FONTS, INCOMING):
                for directory in sorted((p for p in folder.rglob("*") if p.is_dir()),
                                        key=lambda p: len(p.parts), reverse=True):
                    if not any(directory.iterdir()):
                        directory.rmdir()
    for path in []:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            info = inspect(path)[0]
            override = overrides.get(path.name, {})
            brand_id, brand_name = brand_for(info, rules, override)
            family_name = override.get("family", info["family"])
            family_id = safe_id(family_name)
            brand_folder = safe_id(brand_name)
            target = FONTS / brand_folder / family_id / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if digest(target) == digest(path):
                    path.unlink()
                    continue
                target = target.with_name(target.stem + "-" + digest(path)[:10] + target.suffix)
            shutil.move(str(path), str(target))
        except Exception as exc:
            errors.append({"path": path.relative_to(ROOT).as_posix(), "error": str(exc)})
    fonts, brands, hashes = [], {}, {}
    categories = {label: 0 for label in CATEGORIES}
    for path in sorted(FONTS.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        relative = path.relative_to(ROOT)
        try:
            sha = digest(path)
            if sha in hashes:
                errors.append({"path": relative.as_posix(), "warning": "Duplicate of " + hashes[sha]})
                continue
            hashes[sha] = relative.as_posix()
            faces = inspect(path)
            info = faces[0]
            override = overrides.get(path.name, {})
            brand_id, brand_name = brand_for(info, rules, override)
            family_id = safe_id(override.get("family", info["family"]))
            cat = override.get("category") or category(info["family"], info["style"])
            if cat not in categories:
                cat = "其他"
            if brand_id == "unknown":
                errors.append({"path": relative.as_posix(), "warning": "Unknown brand; needs review"})
            brands.setdefault(brand_id, {"id": brand_id, "name": brand_name, "count": 0})["count"] += 1
            categories[cat] += 1
            family_display = override.get("family", info["family"])
            chinese_names = list(dict.fromkeys(info.get("family_zh", []) + info.get("full_name_zh", [])))
            searchable_names = list(dict.fromkeys([family_display, info["full_name"], info["style"]] + chinese_names))
            font_record = {"id": sha[:20], "family": family_display,
                          "family_zh": info.get("family_zh", []), "full_name_zh": info.get("full_name_zh", []),
                          "chinese_names": chinese_names, "searchable_names": searchable_names,
                          "language_tags": info.get("language_tags", []), "script_tags": info.get("script_tags", []),
                          "unicode_codepoint_count": info.get("unicode_codepoint_count", 0),
                          "weight_class": info.get("weight_class", 400), "weight_name": info.get("weight_name", "Regular"),
                          "license_type": info.get("license_type", "unknown"), "license_url": info.get("license_url", ""),
                          "commercial_use": info.get("commercial_use"), "redistribution_allowed": info.get("redistribution_allowed"),
                          "style": info["style"], "full_name": info["full_name"],
                          "manufacturer": info["manufacturer"], "brand_id": brand_id,
                          "family_id": family_id, "category": cat, "faces": faces,
                          "format": path.suffix.lower()[1:], "size": path.stat().st_size,
                          "sha256": sha, "path": relative.as_posix(),
                          "download_url": raw_url(relative)}
            fonts.append(enrich(font_record, regions=override.get("region_tags", override.get("regions", []))))
        except Exception as exc:
            errors.append({"path": relative.as_posix(), "error": str(exc)})
    fonts.sort(key=lambda obj: (obj["brand_id"], obj["family"].casefold(), obj["style"].casefold()))
    emit("fonts.json", fonts)
    emit("brands.json", sorted(brands.values(), key=lambda x: x["id"]))
    emit("categories.json", [{"name": label, "count": count} for label, count in categories.items()])
    weights = sorted({(font.get("weight_class", 400), font.get("weight_name", "Regular")) for font in fonts})
    languages = sorted({tag for font in fonts for tag in font.get("language_tags", [])})
    scripts = sorted({tag for font in fonts for tag in font.get("script_tags", [])})
    licenses = sorted({font.get("license_type", "unknown") for font in fonts})
    taxonomy_indexes = filter_indexes(fonts)
    emit("regions.json", taxonomy_indexes["regions"])
    emit("languages.json", taxonomy_indexes["languages"])
    emit("scripts.json", taxonomy_indexes["scripts"])
    emit("filters.json", {"regions": taxonomy_indexes["regions"], "languages": taxonomy_indexes["languages"], "scripts": taxonomy_indexes["scripts"], "categories": [{"name": label, "count": count} for label, count in categories.items()], "weights": [{"value": value, "label": label} for value, label in weights], "brands": sorted(brands.values(), key=lambda x: x["id"]), "licenses": licenses})
    emit("errors.json", errors)
    emit("manifest.json", {"schema_version": 1, "catalog_count": len(fonts), "brand_count": len(brands),
                           "fonts_url": raw_url(Path("dist/fonts.json")),
                           "brands_url": raw_url(Path("dist/brands.json")),
                           "categories_url": raw_url(Path("dist/categories.json")),
                           "errors_url": raw_url(Path("dist/errors.json")), "filters_url": raw_url(Path("dist/filters.json")), "regions_url": raw_url(Path("dist/regions.json")), "languages_url": raw_url(Path("dist/languages.json")), "scripts_url": raw_url(Path("dist/scripts.json"))})
    print(f"Indexed {len(fonts)} font files, {len(brands)} brands, {len(errors)} warnings/errors")

if __name__ == "__main__":
    main()
