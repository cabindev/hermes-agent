#!/usr/bin/env python3
"""กดถูกใจคอมเมนต์บนโพสต์ของเพจ CivicSpace

สองโหมด:
  --list            พิมพ์คอมเมนต์ที่เพจยังไม่ได้กดถูกใจ เป็น JSON (ไม่แก้อะไรบนเพจ)
  --like ID [ID..]  กดถูกใจคอมเมนต์ตาม id ที่ให้มา

การเลือกว่าคอมเมนต์ไหนควรกด เป็นหน้าที่ของ agent ไม่ใช่สคริปต์นี้
สคริปต์คัดออกเฉพาะที่ชัดเจน: คอมเมนต์ของเพจเอง, ที่กดไปแล้ว, และที่ว่างเปล่า

credential: FACEBOOK_PAGE_ID + FACEBOOK_ENGAGE_TOKEN จาก env หรือ .env ของ Hermes
FACEBOOK_ENGAGE_TOKEN มาจากผู้ใช้ระบบ hermes-engage และเป็น token เดียวที่มีสิทธิ์
pages_manage_engagement — token อ่านอย่างเดียว (FACEBOOK_PAGE_TOKEN) ไม่ถูกใช้ที่นี่

ไม่มี dependency นอก stdlib
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRAPH = "https://graph.facebook.com/v21.0/"
TIMEOUT = 30
KEYS = ("FACEBOOK_PAGE_ID", "FACEBOOK_ENGAGE_TOKEN")

# Hermes ไม่ส่งค่าใน .env ต่อเข้า environment ของ tool ที่มันเรียก จึงต้องอ่านไฟล์เอง
ENV_FILES = [
    os.environ.get("HERMES_HOME", "") and Path(os.environ["HERMES_HOME"]) / ".env",
    Path.home() / ".hermes" / ".env",
    Path("/opt/data/.env"),
    Path.cwd() / ".env",
]


def resolve_credentials():
    found = {k: os.environ.get(k) for k in KEYS}
    for path in ENV_FILES:
        if all(found.values()):
            break
        if not path:
            continue
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in KEYS and value and not found.get(key):
                found[key] = value
    missing = [k for k in KEYS if not found.get(k)]
    if missing:
        sys.exit("ไม่พบ " + ", ".join(missing) + " — ตั้งใน .env ของ Hermes")
    return found["FACEBOOK_PAGE_ID"], found["FACEBOOK_ENGAGE_TOKEN"]


def graph(path, token, params=None, method="GET"):
    """คืน (data, error)"""
    params = dict(params or {}, access_token=token)
    url = path if path.startswith("http") else GRAPH + path
    data = None
    if method == "GET":
        if not path.startswith("http"):
            url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as exc:
        try:
            return None, json.loads(exc.read())["error"]
        except (ValueError, KeyError):
            return None, {"message": f"HTTP {exc.code}", "code": exc.code}
    except urllib.error.URLError as exc:
        return None, {"message": f"ต่อ Graph API ไม่ได้: {exc.reason}", "code": -1}


def as_page_token(page_id, token):
    """ไลก์ต้องทำในนามเพจ — token ของผู้ใช้ระบบจะไลก์เป็นชื่อผู้ใช้ระบบแทน
    จึงแลกเป็น page token ก่อน ถ้าแลกไม่ได้ (เป็น page token อยู่แล้ว) ใช้ตัวเดิม"""
    data, err = graph(page_id, token, {"fields": "access_token"})
    return (data or {}).get("access_token") or token


def paged(path, token, params, cap):
    """เดินตาม paging.next จนครบหรือถึง cap"""
    out, data, err = [], *graph(path, token, params)
    while data and not err:
        out.extend(data.get("data", []))
        nxt = data.get("paging", {}).get("next")
        if not nxt or len(out) >= cap:
            break
        data, err = graph(nxt, token)
    return out[:cap], err


def list_candidates(page_id, token, days, post_limit):
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    posts, err = paged(f"{page_id}/posts", token,
                       {"fields": "id,message,permalink_url,created_time",
                        "since": since, "limit": 25}, post_limit)
    if err:
        sys.exit(f"ดึงโพสต์ไม่ได้: {err.get('message')}")

    candidates = []
    for post in posts:
        comments, err = paged(f"{post['id']}/comments", token,
                              {"fields": "id,message,from,created_time,user_likes",
                               "filter": "stream", "limit": 100}, 500)
        if err:
            print(f"ข้ามโพสต์ {post['id']}: {err.get('message')}", file=sys.stderr)
            continue
        for c in comments:
            author = c.get("from") or {}
            if c.get("user_likes") or author.get("id") == page_id:
                continue
            text = (c.get("message") or "").strip()
            if not text:  # สติกเกอร์/รูปล้วน — agent ตัดสินจากข้อความไม่ได้
                continue
            candidates.append({
                "id": c["id"],
                "message": text,
                "created_time": c.get("created_time"),
                "post": (post.get("message") or "")[:120],
                "post_url": post.get("permalink_url"),
            })
    return candidates


def like(ids, token):
    ok, failed = [], []
    for cid in ids:
        data, err = graph(f"{cid}/likes", token, method="POST")
        if err or not (data or {}).get("success"):
            failed.append({"id": cid, "error": (err or {}).get("message", "unknown")})
        else:
            ok.append(cid)
    return ok, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true",
                      help="แสดงคอมเมนต์ที่ยังไม่ได้กดถูกใจ (JSON)")
    mode.add_argument("--like", nargs="+", metavar="ID",
                      help="กดถูกใจคอมเมนต์ตาม id")
    ap.add_argument("--days", type=int, default=14,
                    help="ดูโพสต์ย้อนหลังกี่วัน (ค่าเริ่มต้น 14)")
    ap.add_argument("--posts", type=int, default=30,
                    help="จำนวนโพสต์สูงสุด (ค่าเริ่มต้น 30)")
    args = ap.parse_args()

    page_id, token = resolve_credentials()
    token = as_page_token(page_id, token)
    if args.list:
        print(json.dumps(list_candidates(page_id, token, args.days, args.posts),
                         ensure_ascii=False, indent=1))
    else:
        ok, failed = like(args.like, token)
        print(json.dumps({"liked": ok, "failed": failed}, ensure_ascii=False, indent=1))
        if failed and not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
