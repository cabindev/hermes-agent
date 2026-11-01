#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ดึงชุดโพสต์ของเพจตามคำค้น พร้อมยอดมีส่วนร่วมและภาพปก เพื่อเอาไปทำงานออกแบบต่อ

    python3 campaign_pull.py --keyword ร้อยเอ็ด --since 365d --out out/ --thumbs

ผลลัพธ์: out/campaign.json + out/img/*.jpg (ถ้าใส่ --thumbs)
สกริปต์ดึงข้อมูลดิบอย่างเดียว การตีความ การจัดธีม และการออกแบบเป็นหน้าที่ของ agent
"""
import argparse, json, os, re, subprocess, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

GRAPH = "https://graph.facebook.com/v21.0/"
_HERE = os.path.dirname(os.path.abspath(__file__))
# รากรีโปอยู่สามชั้นเหนือ skills/social-media/<skill>/ — เผื่อถูกเรียกจากที่อื่น
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
ENV_FILES = ["$HERMES_HOME/.env", "~/.hermes/.env", "/opt/hermes-data/.env", "/opt/data/.env",
             os.path.join(_REPO, ".env"), ".env"]
FIELDS = ("id,created_time,message,permalink_url,status_type,shares,full_picture,"
          "reactions.summary(true).limit(0),comments.summary(true).limit(0),"
          "attachments{media_type,media{image{src,width,height}}}")
TH_MON = {1: "ม.ค.", 2: "ก.พ.", 3: "มี.ค.", 4: "เม.ย.", 5: "พ.ค.", 6: "มิ.ย.",
          7: "ก.ค.", 8: "ส.ค.", 9: "ก.ย.", 10: "ต.ค.", 11: "พ.ย.", 12: "ธ.ค."}


def credentials():
    """env ก่อน แล้วค่อยไล่หา .env ตามลำดับ — เหมือน facebook-page-monitor"""
    page = os.environ.get("FACEBOOK_PAGE_ID")
    token = os.environ.get("FACEBOOK_PAGE_TOKEN")
    for raw in ENV_FILES:
        if page and token:
            break
        path = os.path.expanduser(os.path.expandvars(raw))
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k.strip() == "FACEBOOK_PAGE_ID" and not page:
                page = v
            elif k.strip() == "FACEBOOK_PAGE_TOKEN" and not token:
                token = v
    if not (page and token):
        sys.exit("ไม่พบ FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN — ดู SKILL.md หัวข้อ Setup")
    return page, token


def graph(path, params, token):
    """path เป็นชื่อ edge หรือเป็น URL หน้าถัดไปจาก paging.next ซึ่งมี query ครบอยู่แล้ว"""
    if path.startswith("http"):
        full = path if "access_token=" in path else (
            path + ("&" if "?" in path else "?") + urllib.parse.urlencode({"access_token": token}))
    else:
        full = GRAPH + path + "?" + urllib.parse.urlencode(dict(params, access_token=token))
    try:
        with urllib.request.urlopen(full, timeout=45) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            return None, json.load(e).get("error", {})
        except Exception:
            return None, {"message": f"HTTP {e.code}"}


def since_stamp(value):
    if re.fullmatch(r"\d+d", value):
        return (date.today() - timedelta(days=int(value[:-1]))).isoformat()
    return value


def with_page_size(url, size):
    """เปลี่ยนค่า limit ใน URL หน้าถัดไป โดยคง query อื่นไว้ครบ"""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query), limit=str(size))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch_posts(page, token, since, limit, page_size=25):
    """ไล่ทุกหน้าของ /posts — ครอบคลุมทั้งรีลและอัลบั้มภาพ ไม่ต้องยิง /video_reels แยก

    FIELDS หนัก (สรุปยอด + ภาพ) Graph จึงตอบ code 1 "reduce the amount of data" เมื่อหน้าใหญ่
    เจอแล้วลดขนาดหน้าลงครึ่งหนึ่งแล้วยิงหน้าเดิมซ้ำ จนเหลือ 1 ก็ยังไม่ผ่านถึงยอมแพ้
    """
    out, url = [], None
    params = {"fields": FIELDS, "limit": page_size, "since": since_stamp(since)}
    while len(out) < limit:
        data, err = graph(url or f"{page}/posts", {} if url else params, token)
        if err and err.get("code") == 1 and page_size > 1:
            page_size //= 2
            params["limit"] = page_size
            url = with_page_size(url, page_size) if url else None
            continue
        if err:
            sys.exit(f"Graph ตอบกลับผิดพลาด: {err.get('message')} (code {err.get('code')})")
        out += data.get("data", [])
        url = (data.get("paging") or {}).get("next")
        if not url:
            break
        # ลิงก์หน้าถัดไปของ Graph ยังขอขนาดเดิม ต้องปรับเป็นขนาดที่เพิ่งผ่าน ไม่งั้นโดนปฏิเสธซ้ำทุกหน้า
        url = with_page_size(url, page_size)
    return out[:limit]


def shrink(src, dst, width, quality):
    """ย่อด้วย sips (macOS) ถ้าไม่มีก็ลอง Pillow ไม่มีทั้งคู่ก็เก็บไฟล์เดิมแล้วเตือน"""
    if subprocess.run(["which", "sips"], capture_output=True).returncode == 0:
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality),
                        "-Z", str(width), src, "--out", dst],
                       capture_output=True)
        return os.path.isfile(dst)
    try:
        from PIL import Image
        im = Image.open(src).convert("RGB")
        im.thumbnail((width, width * 4))
        im.save(dst, "JPEG", quality=quality)
        return True
    except ImportError:
        os.replace(src, dst)
        print("  ! ไม่มีทั้ง sips และ Pillow — เก็บภาพขนาดเต็ม ระวังไฟล์ใหญ่เกิน 70 KB ต่อใบ")
        return True


def main():
    ap = argparse.ArgumentParser(description="ดึงชุดโพสต์ตามคำค้นจากเพจ Facebook")
    ap.add_argument("--keyword", required=True, help="คำที่ต้องมีในข้อความโพสต์ เช่น ร้อยเอ็ด")
    ap.add_argument("--since", default="365d", help="'365d' หรือ '2026-05-01' (ค่าเริ่มต้น 365d)")
    ap.add_argument("--limit", type=int, default=600, help="เพดานจำนวนโพสต์ที่ไล่ดู")
    ap.add_argument("--out", default="out", help="โฟลเดอร์ผลลัพธ์")
    ap.add_argument("--thumbs", action="store_true", help="ดาวน์โหลดภาพปกแล้วย่อ")
    ap.add_argument("--width", type=int, default=420, help="ความกว้างภาพหลังย่อ")
    ap.add_argument("--quality", type=int, default=60, help="คุณภาพ JPEG 0-100")
    a = ap.parse_args()

    page_id, token = credentials()
    info, err = graph(page_id, {"fields": "name,fan_count,followers_count,link"}, token)
    if err:
        sys.exit(f"อ่านข้อมูลเพจไม่ได้: {err.get('message')}")

    posts = fetch_posts(page_id, token, a.since, a.limit)
    hits = [p for p in posts if a.keyword in (p.get("message") or "")]
    hits.sort(key=lambda p: p["created_time"])

    os.makedirs(a.out, exist_ok=True)
    imgdir = os.path.join(a.out, "img")
    if a.thumbs:
        os.makedirs(imgdir, exist_ok=True)

    items = []
    for i, p in enumerate(hits, 1):
        att = ((p.get("attachments") or {}).get("data") or [{}])[0]
        img = ((att.get("media") or {}).get("image") or {})
        r = ((p.get("reactions") or {}).get("summary") or {}).get("total_count", 0)
        c = ((p.get("comments") or {}).get("summary") or {}).get("total_count", 0)
        sh = (p.get("shares") or {}).get("count", 0)
        d = datetime.strptime(p["created_time"][:10], "%Y-%m-%d").date()
        item = dict(n=i, id=p["id"], iso=d.isoformat(), date=f"{d.day} {TH_MON[d.month]}",
                    month=TH_MON[d.month], url=p.get("permalink_url"),
                    media_type=att.get("media_type") or p.get("status_type"),
                    kind="รีล" if att.get("media_type") == "video" else "อัลบั้มภาพ",
                    reactions=r, comments=c, shares=sh, e=r + c + sh,
                    message=p.get("message") or "", img=None)
        src_url = img.get("src") or p.get("full_picture")
        if a.thumbs and src_url:
            name = f"{i:02d}.jpg"
            tmp = os.path.join(imgdir, f".raw-{name}")
            req = urllib.request.Request(src_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as rr, open(tmp, "wb") as f:
                f.write(rr.read())
            if shrink(tmp, os.path.join(imgdir, name), a.width, a.quality):
                item["img"] = name
            if os.path.isfile(tmp):
                os.remove(tmp)
        items.append(item)

    total = sum(x["e"] for x in items)
    span = (date.fromisoformat(items[-1]["iso"]) - date.fromisoformat(items[0]["iso"])).days + 1 if items else 0
    payload = dict(page=info, keyword=a.keyword, pulled=date.today().isoformat(),
                   window_posts=len(posts), matched=len(items), engagement=total,
                   span_days=span, items=items)
    with open(os.path.join(a.out, "campaign.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    kinds = {}
    for x in items:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    print(f'เพจ {info.get("name")} · ผู้ติดตาม {info.get("followers_count", 0):,}')
    print(f'คำค้น "{a.keyword}" พบ {len(items)} ชิ้น จาก {len(posts)} โพสต์ในช่วงที่ดู')
    print(f'ช่วง {items[0]["date"] if items else "-"} ถึง {items[-1]["date"] if items else "-"} '
          f'({span} วัน) · การมีส่วนร่วมรวม {total:,}')
    print("แยกตามชนิด: " + " · ".join(f"{k} {v}" for k, v in kinds.items()))
    print("\nห้าชิ้นที่แรงที่สุด")
    for x in sorted(items, key=lambda y: -y["e"])[:5]:
        head = (x["message"].splitlines() or [""])[0][:48]
        print(f'  {x["date"]:>9}  {x["e"]:>6,}  {head}')
    print(f'\nเขียนไฟล์แล้ว: {os.path.join(a.out, "campaign.json")}'
          + (f' + ภาพ {sum(1 for x in items if x["img"])} ใบใน {imgdir}' if a.thumbs else ""))


if __name__ == "__main__":
    main()
