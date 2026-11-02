"""Index verified upstream desktop binaries without copying them into this repository."""
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

from build_index import inspect, category, safe_id
from taxonomy import enrich

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CACHE = ROOT / ".cache" / "desktop-fonts-v1"
SOURCES = {"google-fonts": ("google/fonts", ("ofl", "apache", "ufl", "cc-by-sa")),
           "fontsource": ("fontsource/font-files", ("sources",))}
MAX_FILE = 128 * 1024 * 1024


def read_url(url, limit):
    headers = {"User-Agent": "MoxYi-Desktop-Font-Index/2.0"}
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = "Bearer " + token
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Upstream resource exceeds size limit")
                return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def api(repo, path):
    return json.loads(read_url("https://api.github.com/repos/" + repo + "/" + path, 48 * 1024 * 1024))


def walk_tree(repo, sha, prefix):
    data = api(repo, "git/trees/" + sha + "?recursive=1")
    if data.get("truncated"):
        # GitHub may truncate large trees: never publish an incomplete catalog.
        for item in api(repo, "git/trees/" + sha)["tree"]:
            path = prefix + "/" + item["path"]
            if item["type"] == "tree":
                yield from walk_tree(repo, item["sha"], path)
            elif item["type"] == "blob":
                yield {**item, "path": path}
    else:
        for item in data["tree"]:
            if item["type"] == "blob":
                yield {**item, "path": prefix + "/" + item["path"]}


def desktop_files(repo, roots):
    # Resolve a commit, then enumerate its immutable tree and use that commit in URLs.
    commit = api(repo, "commits/main")["sha"]
    tree = api(repo, "git/trees/" + commit)["tree"]
    files = []
    for item in tree:
        if item["path"] in roots and item["type"] == "tree":
            files.extend(x for x in walk_tree(repo, item["sha"], item["path"])
                         if Path(x["path"]).suffix.lower() in {".ttf", ".otf"})
    if not files:
        raise RuntimeError(repo + " returned no desktop fonts; refusing to replace previous catalog")
    return commit, sorted(files, key=lambda x: x["path"])


def raw_url(repo, commit, path):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected immutable upstream commit")
    return "https://raw.githubusercontent.com/" + repo + "/" + commit + "/" + quote(path, safe="/")


def inspect_blob(repo, commit, item):
    path = item["path"]
    cache_path = CACHE / (item["sha"] + ".json")
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("blob") == item["sha"] and data.get("size") == item["size"]:
            return data
    binary = read_url(raw_url(repo, commit, path), MAX_FILE)
    blob = hashlib.sha1(b"blob " + str(len(binary)).encode("ascii") + b"\0" + binary).hexdigest()
    if blob != item["sha"] or len(binary) != item["size"]:
        raise ValueError("Upstream blob verification failed: " + path)
    if binary[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true"}:
        raise ValueError("Not an original TTF/OTF binary: " + path)
    with tempfile.TemporaryDirectory(prefix="moxyi-upstream-") as folder:
        font_path = Path(folder) / Path(path).name
        font_path.write_bytes(binary)
        faces = inspect(font_path)
    if not faces or not faces[0].get("unicode_codepoint_count"):
        raise ValueError("Font has no usable Unicode character map: " + path)
    data = {"blob": blob, "sha256": hashlib.sha256(binary).hexdigest(), "size": len(binary), "faces": faces}
    CACHE.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(cache_path)
    return data


def make_record(source, repo, commit, item, data):
    info = data["faces"][0]
    chinese = list(dict.fromkeys(info.get("family_zh", []) + info.get("full_name_zh", [])))
    manufacturer = info.get("manufacturer") or "未知品牌"
    path = item["path"]
    return enrich({**info, "id": data["sha256"][:20], "sha256": data["sha256"],
        "size": data["size"], "faces": data["faces"], "format": Path(path).suffix.lower()[1:],
        "brand_id": safe_id(manufacturer), "brand_name": manufacturer,
        "family_id": safe_id(info["family"]), "category": category(info["family"], info["style"]),
        "chinese_names": chinese, "searchable_names": list(dict.fromkeys([info["family"], info.get("full_name", "")] + chinese)),
        "path": "fonts/" + source + "/" + path, "download_url": raw_url(repo, commit, path),
        "source": source, "source_repository": "https://github.com/" + repo,
        "source_commit": commit, "source_blob": item["sha"],
        "source_url": "https://github.com/" + repo + "/blob/" + commit + "/" + quote(path, safe="/")})


def main(source):
    repo, roots = SOURCES[source]
    commit, files = desktop_files(repo, roots)
    # An explicit limit is for local smoke tests only; production always processes all files.
    limit = int(os.environ.get("FONT_INDEX_TEST_LIMIT", "0"))
    if limit:
        files = files[:limit]
    index_path = DIST / "fonts.json"
    existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    merged = [x for x in existing if x.get("source") != source]
    seen = {x.get("sha256") for x in merged}
    # Deduplicate exact binary content, never whole family names (which loses weights/styles).
    unique = {}
    for item in files:
        if 0 < item.get("size", 0) <= MAX_FILE:
            unique.setdefault(item["sha"], item)
    def process(item):
        return make_record(source, repo, commit, item, inspect_blob(repo, commit, item))
    added = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for number, record in enumerate(pool.map(process, unique.values()), 1):
            if number % 100 == 0:
                print(source + ": verified " + str(number) + "/" + str(len(unique)), flush=True)
            if record["sha256"] not in seen:
                merged.append(record)
                seen.add(record["sha256"])
                added += 1
    merged.sort(key=lambda x: (x.get("brand_id", ""), x.get("family", ""), x.get("style", ""), x["sha256"]))
    DIST.mkdir(exist_ok=True)
    # Compact JSON keeps the shared catalog within the client's bounded response size.
    index_path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    report = {"source": source, "upstream_commit": commit, "desktop_files": len(files),
              "unique_blobs": len(unique), "added": added, "index_count": len(merged),
              "formats": ["ttf", "otf"], "test_limit": limit}
    (DIST / (source + "-index-report.json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
