#!/usr/bin/env python3
"""One-shot importer from Markjinli's downloadable font index. Never run on a schedule."""
import hashlib, io, json, os, pathlib, re, urllib.request, urllib.error, zipfile
from fontTools.ttLib import TTFont
ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=ROOT/"incoming"
API="https://api.github.com"
INDEX="https://raw.githubusercontent.com/Markjinli/free-commercial-fonts/main/download_fonts.ps1"
HEAD={"User-Agent":"curated-font-importer","Accept":"application/vnd.github+json"}
if os.getenv("GITHUB_TOKEN"): HEAD["Authorization"]="Bearer "+os.environ["GITHUB_TOKEN"]
def fetch(url,limit=140*1024*1024):
    with urllib.request.urlopen(urllib.request.Request(url,headers=HEAD),timeout=70) as r:
        data=r.read(limit+1)
    if len(data)>limit: raise ValueError("file exceeds size limit")
    return data
def api(repo,path=""):
    return json.loads(fetch(f"{API}/repos/{repo}/{path}",4*1024*1024))
def licenses(repo):
    info=api(repo,"license")
    key=(info.get("license") or {}).get("key","")
    if key not in {"ofl-1.1","apache-2.0","mit","ubuntu-font-1.0"}: raise ValueError("license not in approved redistribution allowlist: "+key)
    raw=info.get("download_url")
    if not raw: raise ValueError("no official license file")
    return key,fetch(raw,500000),pathlib.PurePosixPath(info.get("path") or "LICENSE").name
def font_valid(data):
    f=TTFont(io.BytesIO(data),lazy=True)
    try:
        if "name" not in f: raise ValueError("font missing name table")
    finally:f.close()
def source_repo(url):
    m=re.match(r"https://(?:raw.githubusercontent.com|github.com)/([^/]+)/([^/]+)/(?:raw/|releases/|blob/)?",url)
    return (m.group(1)+"/"+m.group(2)) if m else None
def entries(ps):
    # Only actual DL commands from the maintained upstream downloader.
    pattern=r'DL\s+"(https://[^"]+)"\s+"[^"]*\\([^\\"]+)"\s+"[^"]+"'
    for url,name in re.findall(pattern,ps):
        if "$" in url or "$" in name: continue
        if not name.lower().endswith((".ttf",".otf",".ttc",".zip")): continue
        repo=source_repo(url)
        if repo: yield repo,url,name
def main():
    BASE.mkdir(exist_ok=True)
    ps=fetch(INDEX,2*1024*1024).decode("utf-8-sig")
    existing={p.name.lower() for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() in (".ttf",".otf",".ttc")}
    hashes={hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() in (".ttf",".otf",".ttc")}
    report={"source":INDEX,"downloaded":[],"skipped":[]}
    seen=set(); limit=int(os.getenv("MAX_NEW_FONTS","25"))
    for repo,url,name in entries(ps):
        if len(report["downloaded"])>=limit: break
        if (repo,url) in seen:continue
        seen.add((repo,url))
        try:
            key,lic,licname=licenses(repo)
            if name.lower() in existing:raise ValueError("duplicate filename")
            blob=fetch(url)
            items=[]
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    for f in z.infolist():
                        if f.is_dir() or f.file_size>65*1024*1024:continue
                        if pathlib.PurePosixPath(f.filename).suffix.lower() not in (".ttf",".otf",".ttc"):continue
                        items.append((pathlib.PurePosixPath(f.filename).name,z.read(f)))
            else:items=[(name,blob)]
            for filename,data in items:
                if len(report["downloaded"])>=limit:break
                if filename.lower() in existing:continue
                digest=hashlib.sha256(data).hexdigest()
                if digest in hashes:continue
                font_valid(data)
                dest=BASE/repo.replace("/","-")
                dest.mkdir(parents=True,exist_ok=True)
                (dest/filename).write_bytes(data)
                (dest/licname).write_bytes(lic)
                existing.add(filename.lower());hashes.add(digest)
                report["downloaded"].append({"font":filename,"repo":repo,"license":key,"sha256":digest})
                print("IMPORTED",repo,filename,flush=True)
        except Exception as exc:
            report["skipped"].append({"font":name,"repo":repo,"reason":str(exc)[:250]})
            print("SKIPPED",repo,name,str(exc)[:120],flush=True)
    (BASE/"bulk-import-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print("TOTAL",len(report["downloaded"]),"SKIPPED",len(report["skipped"]))
if __name__=="__main__":main()
