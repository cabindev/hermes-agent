#!/usr/bin/env python3
"""ดึงความเคลื่อนไหวของเพจ Facebook ออกมาเป็นรายงาน

credential อ่านตามลำดับ (แบบเดียวกับ import_facebook_posts.py)
  1. env FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN
  2. ถ้าไม่มี ดึงจาก Azure Bot channel ผ่าน az CLI

ไม่มี dependency นอก stdlib — รันได้ทั้งบนเครื่องและใน container
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone

GRAPH = "https://graph.facebook.com/v21.0/"
TIMEOUT = 30

# ── credential ────────────────────────────────────────────────────────────

def credentials_from_azure():
    """คืน (page_id, token) จาก Facebook channel ของ Azure Bot; (None, None) ถ้าไม่ได้"""
    cmd = ["az", "bot", "facebook", "show", "-g", "civicspace_group",
           "-n", "civic-bot", "--with-secrets", "--only-show-errors", "-o", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if out.returncode != 0:
        return None, None
    try:
        props = json.loads(out.stdout)["properties"]["properties"]
        page = props["pages"][0]
        return page["id"], page["accessToken"]
    except (KeyError, IndexError, ValueError):
        return None, None


# Hermes loads its own .env for config but does not push those values into the
# environment of tools it spawns, so a gateway-driven run sees none of them.
# Read the file directly before falling back to Azure — otherwise every run from
# Telegram lands on the `az` path, and `az` is not in the container image.
ENV_FILES = [
    os.environ.get("HERMES_HOME", "") and Path(os.environ["HERMES_HOME"]) / ".env",
    Path.home() / ".hermes" / ".env",
    Path("/opt/data/.env"),
    Path.cwd() / ".env",
]


def credentials_from_env_file():
    """คืน (page_id, token) จากไฟล์ .env ตัวแรกที่มีค่าครบ"""
    for path in ENV_FILES:
        if not path:
            continue
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        found = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN"):
                found[key.strip()] = value.strip().strip('"').strip("'")
        if found.get("FACEBOOK_PAGE_ID") and found.get("FACEBOOK_PAGE_TOKEN"):
            return found["FACEBOOK_PAGE_ID"], found["FACEBOOK_PAGE_TOKEN"]
    return None, None


def resolve_credentials():
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    token = os.environ.get("FACEBOOK_PAGE_TOKEN")
    if not (page_id and token):
        file_page, file_token = credentials_from_env_file()
        page_id = page_id or file_page
        token = token or file_token
    if not (page_id and token):
        az_page, az_token = credentials_from_azure()
        page_id = page_id or az_page
        token = token or az_token
    if not (page_id and token):
        sys.exit("ไม่พบ credential — ตั้ง FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN "
                 "ใน environment หรือใน .env ของ Hermes (เช่น /opt/data/.env)")
    return page_id, token

# ── graph ────────────────────────────────────────────────────────────────

def graph(path, params, token):
    """คืน (data, error). error เป็น dict ของ Graph หรือ None"""
    params = dict(params, access_token=token)
    url = path if path.startswith("http") else GRAPH + path
    if not path.startswith("http"):
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as exc:
        try:
            return None, json.loads(exc.read())["error"]
        except (ValueError, KeyError):
            return None, {"message": f"HTTP {exc.code}", "code": exc.code}
    except urllib.error.URLError as exc:
        return None, {"message": f"ต่อ Graph API ไม่ได้: {exc.reason}", "code": -1}


def scope_hint(err):
    """แปล error ของ Graph เป็นคำแนะนำที่ทำตามได้"""
    if err and err.get("code") == 10:
        return ("token ไม่มีสิทธิ์ 'pages_read_user_content' — อ่านโพสต์และคอมเมนต์ไม่ได้\n"
                "  ออก token ใหม่ที่ https://developers.facebook.com/tools/explorer/\n"
                "  แล้วตั้ง FACEBOOK_PAGE_TOKEN หรือดู `--check`")
    return None

# ── เวลา ─────────────────────────────────────────────────────────────────

def parse_when(value):
    """'7d' หรือ '2026-03-14' -> datetime (UTC)"""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)d", value.strip())
    if m:
        return datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"รูปแบบวันที่ไม่ถูกต้อง: {value!r} — ใช้ '7d' หรือ '2026-03-14'")

# ── preflight ────────────────────────────────────────────────────────────

NEEDED = {
    "pages_read_engagement": "ข้อมูลเพจพื้นฐาน (ชื่อ, ยอดผู้ติดตาม)",
    "pages_read_user_content": "อ่านโพสต์ + คอมเมนต์ (จำเป็นสำหรับ sentiment)",
    "read_insights": "reach / impressions / ยอดคนเห็น",
}


def run_check(page_id, token):
    print(f"page_id: {page_id}\n")
    data, err = graph("debug_token", {"input_token": token}, token)
    scopes = []
    if data and "data" in data:
        d = data["data"]
        scopes = d.get("scopes", [])
        print(f"token: type={d.get('type')} valid={d.get('is_valid')}")
        expires = d.get("data_access_expires_at")
        if expires:
            when = datetime.fromtimestamp(expires, timezone.utc)
            days = (when - datetime.now(timezone.utc)).days
            flag = "  ⚠️ ใกล้หมดแล้ว" if days < 14 else ""
            print(f"data access หมดอายุ: {when:%d %b %Y} (อีก {days} วัน){flag}")
    else:
        print(f"อ่าน debug_token ไม่ได้: {(err or {}).get('message')}")
    print()
    for scope, why in NEEDED.items():
        mark = "✅" if scope in scopes else "❌"
        print(f"  {mark} {scope:<26} {why}")
    print("\nทดสอบยิงจริง:")
    for label, path, params in (
        ("ข้อมูลเพจ", page_id, {"fields": "name,fan_count,followers_count"}),
        ("โพสต์", f"{page_id}/posts", {"limit": 1}),
        ("insights", f"{page_id}/insights", {"metric": "page_post_engagements", "period": "day"}),
    ):
        _, e = graph(path, params, token)
        print(f"  {'✅' if not e else '❌'} {label:<12} {'' if not e else e.get('message', '')[:90]}")
    return 0 if "pages_read_user_content" in scopes else 1

# ── ดึงโพสต์ ─────────────────────────────────────────────────────────────

# field ที่ token ระดับ pages_read_engagement อ่านได้เสมอ
SAFE_FIELDS = "id,created_time,message,permalink_url,status_type,full_picture,shares"
# ต้องมี pages_read_user_content เพิ่ม — ถ้าไม่มี Graph จะตอบ error code 10
RICH_FIELDS = "reactions.summary(true).limit(0),comments.summary(true).limit(0)"


def fetch_posts(page_id, token, since, until, limit):
    """คืน (posts, degraded). degraded=True แปลว่าไม่มียอด reactions/comments"""
    params = {"fields": f"{SAFE_FIELDS},{RICH_FIELDS}", "limit": min(limit, 100)}
    if since:
        params["since"] = int(since.timestamp())
    if until:
        params["until"] = int(until.timestamp())

    degraded = False
    probe, err = graph(f"{page_id}/posts", dict(params, limit=1), token)
    if err and err.get("code") == 10:
        # token อ่านยอด reactions/comments ไม่ได้ — ถอยไปเอาเท่าที่ได้ ดีกว่าไม่ได้อะไรเลย
        params["fields"] = SAFE_FIELDS
        degraded = True
    elif err:
        sys.exit(f"ดึงโพสต์ไม่สำเร็จ: {err.get('message')}")

    posts, url, first = [], f"{page_id}/posts", True
    while url and len(posts) < limit:
        data, err = graph(url, params if first else {}, token)
        first = False
        if err:
            hint = scope_hint(err)
            sys.exit(f"ดึงโพสต์ไม่สำเร็จ: {err.get('message')}" + (f"\n\n{hint}" if hint else ""))
        posts.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
    return posts[:limit], degraded


def fetch_comments(post_id, token, limit=50):
    data, err = graph(f"{post_id}/comments",
                      {"fields": "message,created_time,like_count", "limit": limit}, token)
    if err:
        return [], err
    return data.get("data", []), None


# Meta ยกเลิก metric เก่าไปหลายตัวใน v21 (page_impressions, page_fans, page_fan_adds
# ตายหมด) เหลือชุดนี้ที่ยังตอบจริง — ยืนยันด้วยการยิงจริงเมื่อ 20 ส.ค. 2026
INSIGHT_METRICS = [
    "page_post_engagements",
    "page_daily_follows_unique",
    "page_views_total",
    "page_actions_post_reactions_total",
]


def fetch_insights(page_id, token, period="day"):
    data, err = graph(f"{page_id}/insights",
                      {"metric": ",".join(INSIGHT_METRICS), "period": period}, token)
    if err:
        return {}, err
    out = {}
    for row in data.get("data", []):
        values = row.get("values") or []
        if values:
            out[row["name"]] = values[-1].get("value")
    return out, None


def summarize(post):
    return {
        "id": post["id"],
        "created_time": post.get("created_time", ""),
        "title": (post.get("message", "") or "").strip().split("\n")[0][:90] or "(ไม่มีข้อความ)",
        "message": post.get("message", ""),
        "url": post.get("permalink_url", ""),
        "type": post.get("status_type", ""),
        "reactions": post.get("reactions", {}).get("summary", {}).get("total_count", 0),
        "comments": post.get("comments", {}).get("summary", {}).get("total_count", 0),
        "shares": post.get("shares", {}).get("count", 0),
    }

# ── output ───────────────────────────────────────────────────────────────

def render_markdown(page, rows, window, degraded=False):
    out = [f"# {page.get('name', 'เพจ')} — ความเคลื่อนไหว{window}", ""]
    out.append(f"ผู้ติดตาม **{page.get('followers_count', 0):,}** · "
               f"โพสต์ในช่วงนี้ **{len(rows)}**")
    if rows:
        ts = sum(r["shares"] for r in rows)
        if degraded:
            out += ["", f"รวม: 🔁 {ts:,} แชร์",
                    "", "> ⚠️ token ปัจจุบันไม่มีสิทธิ์ `pages_read_user_content` "
                        "จึงยังไม่มียอด reactions และคอมเมนต์ — รัน `--check` ดูรายละเอียด",
                    "", "| วันที่ | โพสต์ | 🔁 |", "|---|---|---:|"]
            for r in rows:
                title = r["title"].replace("|", "\\|")
                link = f"[{title}]({r['url']})" if r["url"] else title
                out.append(f"| {r['created_time'][:10]} | {link} | {r['shares']:,} |")
            top = max(rows, key=lambda r: r["shares"])
            out += ["", f"**โพสต์ที่ถูกแชร์มากสุด:** {top['title']} ({top['shares']:,} แชร์)"]
        else:
            tr = sum(r["reactions"] for r in rows)
            tc = sum(r["comments"] for r in rows)
            out += ["", f"รวม: 👍 {tr:,} reactions · 💬 {tc:,} คอมเมนต์ · 🔁 {ts:,} แชร์",
                    f"เฉลี่ยต่อโพสต์: {tr / len(rows):.0f} reactions", "",
                    "| วันที่ | โพสต์ | 👍 | 💬 | 🔁 |", "|---|---|---:|---:|---:|"]
            for r in rows:
                title = r["title"].replace("|", "\\|")
                link = f"[{title}]({r['url']})" if r["url"] else title
                out.append(f"| {r['created_time'][:10]} | {link} | {r['reactions']:,} "
                           f"| {r['comments']:,} | {r['shares']:,} |")
            top = max(rows, key=lambda r: r["reactions"] + r["comments"] * 2 + r["shares"] * 3)
            out += ["", f"**โพสต์ที่คนมีส่วนร่วมสูงสุด:** {top['title']} "
                        f"({top['reactions']:,} reactions, {top['comments']:,} คอมเมนต์)"]
    else:
        out += ["", "ไม่มีโพสต์ในช่วงเวลานี้"]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="รายงานความเคลื่อนไหวเพจ Facebook")
    ap.add_argument("--since", help="'7d' หรือ '2026-03-14'")
    ap.add_argument("--until", help="ขอบบน รูปแบบเดียวกับ --since")
    ap.add_argument("--limit", type=int, default=25, help="จำนวนโพสต์สูงสุด (ค่าเริ่มต้น 25)")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--with-comments", action="store_true",
                    help="ดึงคอมเมนต์มาด้วย เพื่อให้ agent วิเคราะห์ sentiment ต่อ")
    ap.add_argument("--comment-limit", type=int, default=50, help="คอมเมนต์ต่อโพสต์ (ค่าเริ่มต้น 50)")
    ap.add_argument("--insights", action="store_true",
                    help="ดึง Page Insights (engagement, ผู้ติดตามใหม่, ยอดเข้าชม) — ต้องมี read_insights")
    ap.add_argument("--check", action="store_true", help="ตรวจสิทธิ์ของ token แล้วออก")
    args = ap.parse_args()

    page_id, token = resolve_credentials()
    if args.check:
        sys.exit(run_check(page_id, token))

    page, err = graph(page_id, {"fields": "name,username,fan_count,followers_count,link"}, token)
    if err:
        sys.exit(f"อ่านข้อมูลเพจไม่ได้: {err.get('message')}")

    since, until = parse_when(args.since), parse_when(args.until)
    raw, degraded = fetch_posts(page_id, token, since, until, args.limit)
    rows = [summarize(p) for p in raw]

    if args.with_comments:
        for r in rows:
            if r["comments"]:
                comments, cerr = fetch_comments(r["id"], token, args.comment_limit)
                r["comment_sample"] = [c.get("message", "") for c in comments]
                if cerr:
                    r["comment_error"] = cerr.get("message")

    insights, ins_err = ({}, None)
    if args.insights:
        insights, ins_err = fetch_insights(page_id, token)

    if args.format == "json":
        print(json.dumps({"page": page, "degraded": degraded,
                          "insights": insights, "posts": rows},
                         ensure_ascii=False, indent=2))
    else:
        window = f" ({args.since} ถึงปัจจุบัน)" if args.since else ""
        print(render_markdown(page, rows, window, degraded))
        if args.insights:
            if ins_err:
                print(f"\n⚠️ ดึง insights ไม่ได้: {ins_err.get('message')}")
            elif insights:
                labels = {
                    "page_post_engagements": "การมีส่วนร่วมกับโพสต์",
                    "page_daily_follows_unique": "ผู้ติดตามใหม่",
                    "page_views_total": "ยอดเข้าชมเพจ",
                    "page_actions_post_reactions_total": "reactions แยกชนิด",
                }
                print("\n## Page Insights (ล่าสุด 1 วัน)\n")
                for k, v in insights.items():
                    print(f"- {labels.get(k, k)}: **{v}**")
        if args.with_comments:
            errs = {r.get("comment_error") for r in rows if r.get("comment_error")}
            if errs:
                print(f"\n⚠️ ดึงคอมเมนต์ไม่ได้: {errs.pop()}")


if __name__ == "__main__":
    main()
