"""Hent de to originale CC0-arkiver, og verificer appens fastlåste SHA256.

Kræver ingen konto. Henter kun offentlige filer; udpakker eller kører intet.
"""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

LIBRARY = Path(__file__).resolve().parents[1] / "library"
ARCHIVES = LIBRARY.parent / "archive-cache"


def verify(data, pack):
    if len(data) != pack["bytes"] or hashlib.sha256(data).hexdigest() != pack["sha256"]:
        raise ValueError("Arkivets størrelse/SHA256 matcher ikke: " + pack["archive"])


def fetch_archives(output_dir=ARCHIVES):
    output_dir = Path(output_dir)
    packs = json.loads((LIBRARY / "sources.json").read_text(encoding="utf-8"))["packs"]
    output_dir.mkdir(parents=True, exist_ok=True)
    for pack in packs:
        target = output_dir / pack["archive"]
        if target.is_symlink():
            raise ValueError("Afviser et arkivlink: " + str(target))
        if target.exists():
            verify(target.read_bytes(), pack)
            continue
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        opener.addheaders = [("User-Agent", "Makervaerkstedet-Scratch-library-build/1")]
        url = pack.get("download_url")
        if not url:
            page = pack["source_url"]
            with opener.open(page, timeout=30) as response:
                html = response.read(3 * 1024 * 1024).decode("utf-8")
            token = re.search(r'<meta name="csrf_token" value="([^"]+)"', html)
            if not token:
                raise ValueError("Udgivelsessidens downloadformular er ændret")
            headers = {"Referer": page, "Origin": "https://pixelfrog-assets.itch.io"}
            form = urllib.parse.urlencode({"csrf_token": token.group(1), "source": "view_game"}).encode()
            # This is the public, zero-price download flow from the creator's page.
            request = urllib.request.Request(page + "/download_url", data=form, headers=headers)
            with opener.open(request, timeout=30) as response:
                landing = json.load(response)["url"]
            with opener.open(landing, timeout=30) as response:
                html = response.read(3 * 1024 * 1024).decode("utf-8")
            if not re.search(r'data-upload_id=["\']' + re.escape(pack["upload_id"]) + r'["\']', html):
                raise ValueError("Det fastlåste offentlige upload findes ikke")
            request = urllib.request.Request(page + "/file/" + pack["upload_id"], data=form, headers=headers)
            with opener.open(request, timeout=30) as response:
                url = json.load(response)["url"]
        with opener.open(url, timeout=30) as response:
            data = response.read(pack["bytes"] + 1)
        verify(data, pack)
        with target.open("xb") as stream:
            stream.write(data)
    return {"archives": len(packs), "bytes": sum(pack["bytes"] for pack in packs)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ARCHIVES)
    args = parser.parse_args()
    print(json.dumps(fetch_archives(args.output_dir)))
