#!/usr/bin/env python3
"""Scan and organize fonts for MoxYi."""
import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import quote
from fontTools.ttLib import TTFont, TTCollection

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
                            # Some legacy fonts encode GBK bytes in name records marked as Unicode.
                            if record.platformID == 3 and record.platEncID in (1, 10):
                                if any("㐀" <= ch <= "鿿" for ch in value) and not any(ch in value for ch in ("汉", "鼎")):
                                    try:
                                        repaired = record.string.decode("gb18030")
                                        if repaired.startswith("汉鼎"):
                                            value = repaired
                                    except UnicodeError:
                                        pass
                            if value and value not in strings:
                                strings.append(value)
                        except Exception:
                            pass
                if strings:
                    break
            data[key] = strings[0] if strings else ""
        data["family"] = data["family"] or "Unknown"
        data["style"] = data["style"] or "Regular"
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
        ("宋体", ["宋", "serif", "ming"]),
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
        return safe_id(override["brand"]), override["brand"]
    content = " ".join([info["family"], info["manufacturer"], info["full_name"]]).lower()
    matches = []
    for rule in rules:
        for keyword in rule.get("keywords", []):
            if keyword.lower() in content:
                matches.append((len(keyword), rule["id"], rule["name"]))
    if matches:
        _, key, label = sorted(matches, reverse=True)[0]
        return key, label
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
    for path in sorted(INCOMING.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            info = inspect(path)[0]
            override = overrides.get(path.name, {})
            brand_id, _ = brand_for(info, rules, override)
            if brand_id == "unknown" and path.name.startswith("汉鼎"):
                brand_id = "handing"
            family_name = override.get("family", info["family"])
            if path.name.startswith("汉鼎") and (family_name == "Unknown" or not any("㐀" <= c <= "鿿" for c in family_name)):
                family_name = path.stem
            family_id = safe_id(family_name)
            target = FONTS / brand_id / family_id / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if digest(target) == digest(path):
                    path.unlink()
                    continue
                target = target.with_name(target.stem + "-" + digest(path)[:10] + target.suffix)
            shutil.move(str(path), str(target))
        except Exception as exc:
            errors.append({"path": path.relative_to(ROOT).as_posix(), "error": str(exc)})
    # Migrate previously misclassified HanDing files to individual family folders.
    old_dir = FONTS / "unknown" / "u-aeacf6e2e1ea"
    if old_dir.exists():
        for old in sorted(old_dir.iterdir()):
            if old.is_file() and old.suffix.lower() in EXTENSIONS and old.name.startswith("汉鼎"):
                new = FONTS / "handing" / safe_id(old.stem) / old.name
                new.parent.mkdir(parents=True, exist_ok=True)
                if not new.exists():
                    shutil.move(str(old), str(new))
        if not any(old_dir.iterdir()):
            old_dir.rmdir()
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
            if brand_id == "unknown" and path.name.startswith("汉鼎"):
                brand_id, brand_name = "handing", "汉鼎"
            if len(relative.parts) >= 4:
                brand_id, family_id = relative.parts[1:3]
                if brand_id == "unknown" and path.name.startswith("汉鼎"):
                    brand_id, family_id = "handing", safe_id(path.stem)
            else:
                family_id = safe_id(info["family"])
            cat = override.get("category") or category(info["family"], info["style"])
            if cat not in categories:
                cat = "其他"
            if brand_id == "unknown":
                errors.append({"path": relative.as_posix(), "warning": "Unknown brand; needs review"})
            brands.setdefault(brand_id, {"id": brand_id, "name": brand_name, "count": 0})["count"] += 1
            categories[cat] += 1
            family_display = override.get("family", info["family"])
            if path.name.startswith("汉鼎") and (family_display == "Unknown" or not any("㐀" <= c <= "鿿" for c in family_display)):
                family_display = path.stem
            fonts.append({"id": sha[:20], "family": family_display,
                          "style": info["style"], "full_name": info["full_name"],
                          "manufacturer": info["manufacturer"], "brand_id": brand_id,
                          "family_id": family_id, "category": cat, "faces": faces,
                          "format": path.suffix.lower()[1:], "size": path.stat().st_size,
                          "sha256": sha, "path": relative.as_posix(),
                          "download_url": raw_url(relative)})
        except Exception as exc:
            errors.append({"path": relative.as_posix(), "error": str(exc)})
    fonts.sort(key=lambda obj: (obj["brand_id"], obj["family"].casefold(), obj["style"].casefold()))
    emit("fonts.json", fonts)
    emit("brands.json", sorted(brands.values(), key=lambda x: x["id"]))
    emit("categories.json", [{"name": label, "count": count} for label, count in categories.items()])
    emit("errors.json", errors)
    emit("manifest.json", {"schema_version": 1, "catalog_count": len(fonts), "brand_count": len(brands),
                           "fonts_url": raw_url(Path("dist/fonts.json")),
                           "brands_url": raw_url(Path("dist/brands.json")),
                           "categories_url": raw_url(Path("dist/categories.json")),
                           "errors_url": raw_url(Path("dist/errors.json"))})
    print(f"Indexed {len(fonts)} font files, {len(brands)} brands, {len(errors)} warnings/errors")

if __name__ == "__main__":
    main()
