#!/usr/bin/env python3
"""Import one font per scheduled run using sources listed by Markjinli.
Official upstream binary and license are required; the aggregator is only an index.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG_URL = "https://raw.githubusercontent.com/Markjinli/free-commercial-fonts/master/README.md"
PROGRESS = ROOT / "metadata/import-progress.json"
# Each candidate links to the maintained upstream repository and its actual OFL.
# Extend this list only after verifying the upstream license and actual file path.
CANDIDATES = [
    ("lxgw-wenkai-medium", "lxgw/LxgwWenKai",
     "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/fonts/TTF/LXGWWenKai-Medium.ttf",
     "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt",
     "LXGWWenKai-Medium.ttf"),
    ("buttaiwan-genyo-bold", "ButTaiwan/genyo-font",
     "https://raw.githubusercontent.com/ButTaiwan/genyo-font/master/otf/TC/GenYoMin2TC-B.otf",
     "https://raw.githubusercontent.com/ButTaiwan/genyo-font/master/SIL_Open_Font_License_1.1.txt",
     "GenYoMin2TC-B.otf"),
    ("buttaiwan-genryu-bold", "ButTaiwan/genryu-font",
     "https://raw.githubusercontent.com/ButTaiwan/genryu-font/master/otf/TC/GenRyuMin2TC-B.otf",
     "https://raw.githubusercontent.com/ButTaiwan/genryu-font/master/SIL_Open_Font_License_1.1.txt",
     "GenRyuMin2TC-B.otf"),
    ("lxgw-bright-medium", "lxgw/LxgwBright",
     "https://raw.githubusercontent.com/lxgw/LxgwBright/main/LXGWBrightGB/LXGWBrightGB-Medium.ttf",
     "https://raw.githubusercontent.com/lxgw/LxgwBright/main/OFL.txt",
     "LXGWBrightGB-Medium.ttf"),
    ("lxgw-bright-light", "lxgw/LxgwBright",
     "https://raw.githubusercontent.com/lxgw/LxgwBright/main/LXGWBrightGB/LXGWBrightGB-Light.ttf",
     "https://raw.githubusercontent.com/lxgw/LxgwBright/main/OFL.txt",
     "LXGWBrightGB-Light.ttf"),
]
def download(url, destination, min_bytes):
    if not url.startswith("https://raw.githubusercontent.com/"):
        raise ValueError("Only approved GitHub raw sources are accepted")
    req = Request(url, headers={"User-Agent":"MoxYi-font-importer"})
    with urlopen(req, timeout=100) as response:
        data = response.read(95 * 1024 * 1024 + 1)
    if not min_bytes <= len(data) <= 95 * 1024 * 1024:
        raise ValueError("Invalid or oversized upstream content: " + url)
    destination.write_bytes(data)

def main():
    # Confirm aggregator is online and mentions the upstream font projects.
    req = Request(CATALOG_URL, headers={"User-Agent":"MoxYi-font-importer"})
    with urlopen(req, timeout=30) as response:
        catalog = response.read(1024 * 1024).decode("utf-8")
    progress = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.exists() else {"imported":[]}
    imported = set(progress["imported"])
    existing = {p.name for p in (ROOT / "fonts").rglob("*") if p.is_file()}
    existing.update(p.name for p in (ROOT / "incoming").rglob("*") if p.is_file())
    for item_id, source_repo, font_url, license_url, filename in CANDIDATES:
        if source_repo not in catalog:
            print("Source not indexed, skip:", source_repo)
            continue
        if item_id in imported or filename in existing:
            continue
        with tempfile.TemporaryDirectory() as temp:
            font = Path(temp) / filename
            license_file = Path(temp) / "OFL.txt"
            download(font_url, font, 100000)
            download(license_url, license_file, 100)
            if b"SIL OPEN FONT LICENSE" not in license_file.read_bytes().upper():
                raise ValueError("Expected SIL OFL license: " + source_repo)
            # Validate font before moving into the repository.
            from fontTools.ttLib import TTFont
            face = TTFont(str(font), lazy=True)
            try:
                if "name" not in face:
                    raise ValueError("Invalid font name table")
            finally:
                face.close()
            incoming = ROOT / "incoming" / filename
            incoming.parent.mkdir(exist_ok=True)
            incoming.write_bytes(font.read_bytes())
            lic_path = ROOT / "licenses" / source_repo.replace("/", "-") / "OFL.txt"
            lic_path.parent.mkdir(parents=True, exist_ok=True)
            lic_path.write_bytes(license_file.read_bytes())
        subprocess.run([sys.executable, str(ROOT / "scripts/build_index.py")], check=True)
        progress["imported"].append(item_id)
        PROGRESS.parent.mkdir(exist_ok=True)
        PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=2)+"\\n", encoding="utf-8")
        print("SUCCESS: imported and indexed one font:", filename)
        return
    print("No remaining eligible candidates. Catalog checked successfully.")

if __name__ == "__main__":
    main()
