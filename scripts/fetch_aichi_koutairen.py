#!/usr/bin/env python3
"""愛知県高体連バドミントン結果カテゴリの全ファイルを保全する。

https://aichikenkoutairen.jp/download-category/badminton-result/ (全7ページ)
→ 各ダウンロードページ → wp-content/uploads 配下の実ファイルを取得。
1リクエストごとに1秒スリープ（サーバ負荷への配慮）。
"""
import csv
import hashlib
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://aichikenkoutairen.jp/download-category/badminton-result/"
OUT = Path(__file__).resolve().parent.parent / "data" / "aichi" / "koutairen" / "raw"
MANIFEST = OUT.parent / "manifest.csv"
UA = "badminton-archive-bot/0.1 (personal research; contact: ijichiyuuta.adit@gmail.com)"
SLEEP = 1.0

def enc(url: str) -> str:
    """既に%エンコード済みの部分は保ちつつ、生の非ASCII文字だけエンコードする。"""
    return urllib.parse.quote(url, safe=":/?&=%~")

def get(url: str) -> bytes:
    return get2(url)[0]

def get2(url: str):
    req = urllib.request.Request(enc(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
        headers = dict(r.headers)
    time.sleep(SLEEP)
    return data, headers

def listing_urls():
    yield BASE
    for p in range(2, 8):
        yield f"{BASE}page/{p}/"

DL_LINK = re.compile(
    r'<a[^>]+href="(https://aichikenkoutairen\.jp/download/[^"]+)"[^>]*>(.*?)</a>',
    re.S,
)
FILE_LINK = re.compile(
    r'href="(https://aichikenkoutairen\.jp/wp-content/uploads/[^"]+?\.(?:pdf|xlsx?|docx?|zip))"',
    re.I,
)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    entries = {}  # dl_url -> title
    for lp in listing_urls():
        try:
            page = get(lp).decode("utf-8", "replace")
        except Exception as e:
            print(f"[WARN] listing {lp}: {e}", flush=True)
            continue
        found = 0
        for m in DL_LINK.finditer(page):
            url = html.unescape(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if url not in entries or (title and not entries[url]):
                entries[url] = html.unescape(title)
                found += 1
        print(f"[LIST] {lp} -> {found} new ({len(entries)} total)", flush=True)

    rows = []
    for i, (dl_url, title) in enumerate(entries.items(), 1):
        try:
            page = get(dl_url).decode("utf-8", "replace")
        except Exception as e:
            print(f"[WARN] dlpage {dl_url}: {e}", flush=True)
            rows.append({"title": title, "dl_url": dl_url, "file_url": "",
                         "local": "", "sha256": "", "bytes": "", "error": str(e)})
            continue
        m = FILE_LINK.search(page)
        wpdm = re.search(r"wpdmdl=(\d+)", page)
        if m:
            file_url = html.unescape(m.group(1))
            name = urllib.parse.unquote(file_url.rsplit("/", 1)[-1])
        elif wpdm:
            # WordPress Download Manager 形式（ボタン経由の配布）
            file_url = f"{dl_url}?wpdmdl={wpdm.group(1)}"
            name = ""  # Content-Disposition から決める
        else:
            print(f"[MISS] no file link: {title} ({dl_url})", flush=True)
            rows.append({"title": title, "dl_url": dl_url, "file_url": "",
                         "local": "", "sha256": "", "bytes": "", "error": "no-file-link"})
            continue
        # uploads/YYYY/MM をプレフィクスにして衝突回避
        ym = re.search(r"/uploads/(\d{4})/(\d{2})/", file_url)
        prefix = f"{ym.group(1)}-{ym.group(2)}_" if ym else ""
        try:
            if not name:
                data, hdrs = get2(file_url)
                cd = hdrs.get("Content-Disposition", "")
                mcd = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
                name = urllib.parse.unquote(mcd.group(1)) if mcd else \
                    urllib.parse.unquote(dl_url.rstrip("/").rsplit("/", 1)[-1]) + ".pdf"
                local = OUT / (prefix + name)
                if not local.exists():
                    local.write_bytes(data)
                    status = "saved"
                else:
                    data = local.read_bytes()
                    status = "cached"
            else:
                local = OUT / (prefix + name)
                if local.exists():
                    data = local.read_bytes()
                    status = "cached"
                else:
                    data = get(file_url)
                    local.write_bytes(data)
                    status = "saved"
            digest = hashlib.sha256(data).hexdigest()
            rows.append({"title": title, "dl_url": dl_url, "file_url": file_url,
                         "local": str(local.relative_to(OUT.parent)),
                         "sha256": digest, "bytes": len(data), "error": ""})
            print(f"[{status.upper()}] {i}/{len(entries)} {local.name} ({len(data)//1024}KB)", flush=True)
        except Exception as e:
            print(f"[WARN] file {file_url}: {e}", flush=True)
            rows.append({"title": title, "dl_url": dl_url, "file_url": file_url,
                         "local": "", "sha256": "", "bytes": "", "error": str(e)})

    with MANIFEST.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title", "dl_url", "file_url", "local",
                                          "sha256", "bytes", "error"])
        w.writeheader()
        w.writerows(rows)
    ok = sum(1 for r in rows if r["local"])
    print(f"[DONE] {ok}/{len(rows)} files saved. manifest: {MANIFEST}", flush=True)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
