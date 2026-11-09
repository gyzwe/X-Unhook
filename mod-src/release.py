#!/usr/bin/env python3
"""Publish build/out as GitHub release v<mod_version> (repo from mod-src/xmod.json).

    python3 -I mod-src/release.py [--notes "text" | --notes-file F] [--push]

Token: secrets/release_token (fine-grained PAT, this repo only, Contents: read & write)
or $GITHUB_TOKEN. --push also pushes the current git branch (sources only, see .gitignore).
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
API = "https://api.github.com"


def token():
    f = ROOT / "secrets" / "release_token"
    t = f.read_text().strip() if f.exists() else os.environ.get("GITHUB_TOKEN", "")
    if not t:
        sys.exit("no token: put it in secrets/release_token or $GITHUB_TOKEN")
    return t


def call(method, url, tok, body=None, data=None, ctype=None, length=None):
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "xmod-release"}
    if body is not None:
        data = json.dumps(body).encode()
        ctype = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "build" / "out"))
    a = ap.parse_args()

    cfg = json.loads((HERE / "xmod.json").read_text())
    repo = cfg.get("repo")
    if not repo:
        sys.exit('set "repo": "owner/name" in mod-src/xmod.json')
    out = Path(a.out)
    meta = json.loads((out / "xmod-release.json").read_text())
    if meta["mod_version"] != cfg["mod_version"]:
        sys.exit(f"build/out is v{meta['mod_version']} but xmod.json says v{cfg['mod_version']}: rebuild first")
    tok = token()
    notes = Path(a.notes_file).read_text() if a.notes_file else a.notes
    tag = f"v{cfg['mod_version']}"

    if a.push:
        auth = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        subprocess.run(["git", "-C", str(ROOT), "-c", f"http.extraheader=AUTHORIZATION: basic {auth}",
                        "push", f"https://github.com/{repo}.git", "HEAD:main", "--tags"], check=True)

    st, rel = call("GET", f"{API}/repos/{repo}/releases/tags/{tag}", tok)
    if st == 404:
        st, rel = call("POST", f"{API}/repos/{repo}/releases", tok, body={
            "tag_name": tag, "name": f"X Mod {tag} (X {meta['x_version']})",
            "body": notes or f"X Mod {tag} for X {meta['x_version']}", "draft": False, "prerelease": False})
    if st not in (200, 201):
        sys.exit(f"release: HTTP {st}: {rel}")
    print(f"release {tag}: {rel['html_url']}")

    existing = {x["name"]: x["id"] for x in rel.get("assets", [])}
    files = sorted(out.glob("*.apk")) + [out / "xmod-release.json"] + sorted(out.glob("*.apks"))
    upload = rel["upload_url"].split("{")[0]
    for f in files:
        if f.name in existing:
            call("DELETE", f"{API}/repos/{repo}/releases/assets/{existing[f.name]}", tok)
        ctype = "application/json" if f.suffix == ".json" else "application/vnd.android.package-archive"
        with open(f, "rb") as fh:
            st, res = call("POST", f"{upload}?name={urllib.parse.quote(f.name)}", tok,
                           data=fh, ctype=ctype, length=f.stat().st_size)
        print(f"  {'ok ' if st == 201 else 'ERR'} {f.name} ({f.stat().st_size / 1048576:.1f} MB)"
              + ("" if st == 201 else f" HTTP {st}: {res}"))


if __name__ == "__main__":
    main()
