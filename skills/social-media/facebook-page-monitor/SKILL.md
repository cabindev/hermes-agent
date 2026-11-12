---
name: facebook-page-monitor
description: "Track CivicSpace page posts, engagement, and comments."
version: 1.0.0
author: cabindev + Hermes Agent
license: MIT
platforms: [macos, linux]
prerequisites:
  commands: [python3]
metadata:
  hermes:
    tags: [facebook, monitoring, analytics, sentiment, civicspace]
    category: social-media
---

# Facebook Page Monitor

รายงานความเคลื่อนไหวของเพจ CivicSpace — โพสต์ใหม่, ยอด engagement, และ
sentiment จากคอมเมนต์ ตัวสกริปต์ดึงข้อมูลดิบมาให้ ส่วนการสรุปและตีความ
เป็นหน้าที่ของ agent

อ่านอย่างเดียว ไม่โพสต์ ไม่ตอบคอมเมนต์ — ยกเว้นการกดถูกใจคอมเมนต์ (ดูหัวข้อด้านล่าง)

## When to Use

- "เพจสัปดาห์นี้เป็นไง" / "มีโพสต์อะไรใหม่บ้าง"
- สรุปประจำสัปดาห์หรือประจำเดือน
- ดูว่าโพสต์ไหนคนมีส่วนร่วมเยอะ
- วิเคราะห์ sentiment ของคอมเมนต์ (ต้องมีสิทธิ์เพิ่ม — ดู Limits)

คู่กับ `civicspace-import` — สกิลนั้น *นำเข้า* โพสต์เข้าเว็บ สกิลนี้แค่ *อ่านและรายงาน*

## Setup

สกริปต์อยู่ข้าง SKILL.md นี้ ตำแหน่งต่างกันตามเครื่อง — หาแบบนี้:

```bash
SCRIPT=$(ls /opt/hermes/skills/social-media/facebook-page-monitor/page_report.py \
            ~/.hermes/skills/facebook-page-monitor/page_report.py 2>/dev/null | head -1)
```

credential อ่านตามลำดับ:
1. env `FACEBOOK_PAGE_ID` / `FACEBOOK_PAGE_TOKEN`
2. ไฟล์ `.env` ของ Hermes (`$HERMES_HOME/.env`, `~/.hermes/.env`, `/opt/data/.env`)
3. Azure Bot channel (`civic-bot` / RG `civicspace_group`) ผ่าน `az` — ต้อง `az login`

ข้อ 2 มีไว้เพราะ Hermes อ่าน `.env` ไปใช้เองแต่**ไม่ส่งต่อเข้า environment ของ tool
ที่มันเรียก** ถ้าไม่มีขั้นนี้ ทุกครั้งที่สั่งผ่าน Telegram จะตกไปข้อ 3 แล้วพัง
เพราะ container ไม่ได้ติดตั้ง `az`

**เช็คสิทธิ์ก่อนเสมอเมื่อเจอผลแปลกๆ:**

```bash
python3 "$SCRIPT" --check
```

บอกว่า token มี scope อะไร เหลืออายุกี่วัน และยิง endpoint จริงให้ดูว่าอันไหนผ่าน

## Usage

```bash
python3 "$SCRIPT" --since 7d                    # รายงานสัปดาห์ที่ผ่านมา
python3 "$SCRIPT" --since 2026-08-01 --until 2026-09-01
python3 "$SCRIPT" --since 30d --format json     # ให้ agent ประมวลผลต่อ
python3 "$SCRIPT" --since 7d --with-comments    # ดึงคอมเมนต์มาทำ sentiment
```

| Option | Notes |
|---|---|
| `--since` | `7d` หรือ `2026-08-01` — **ควรใส่เสมอ** ไม่งั้นได้ย้อนหลังทั้งหมดเท่าที่ limit ให้ |
| `--until` | ขอบบน รูปแบบเดียวกัน |
| `--limit` | จำนวนโพสต์สูงสุด ค่าเริ่มต้น 25 |
| `--format` | `md` (ค่าเริ่มต้น) หรือ `json` |
| `--with-comments` | ดึงคอมเมนต์มาใส่ `comment_sample` ให้ agent วิเคราะห์ต่อ |
| `--comment-limit` | คอมเมนต์ต่อโพสต์ ค่าเริ่มต้น 50 |
| `--check` | ตรวจสิทธิ์ token แล้วออก |

## การทำ Sentiment

สกริปต์ **ไม่ตัดสิน sentiment เอง** — มันดึงคอมเมนต์มาให้ใน `comment_sample`
แล้ว agent เป็นคนอ่านและจัดกลุ่ม เพราะคอมเมนต์ภาษาไทยมีประชด สแลง และบริบท
ท้องถิ่นที่ classifier สำเร็จรูปอ่านพลาดบ่อย

เวลาสรุปให้แยกเป็น บวก / กลาง / ลบ พร้อมยกคอมเมนต์ตัวอย่างจริงประกอบ
อย่าสรุปเป็นเปอร์เซ็นต์ลอยๆ โดยไม่มีตัวอย่าง

## Limits — สิทธิ์ของ token ปัจจุบัน

token ที่มาจาก Azure Bot channel ออกมาเพื่อ Messenger จึงมีแค่
`pages_read_engagement` ผลคือ:

| ดึงได้ | ดึงไม่ได้ |
|---|---|
| โพสต์ (`message`, วันที่, ลิงก์, รูป, ชนิดโพสต์) | ยอด reactions / ไลก์ |
| ยอด **แชร์** | จำนวนและเนื้อคอมเมนต์ |
| ชื่อเพจ, ยอดผู้ติดตาม | reach / impressions |

เจอแบบนี้สกริปต์จะ **ลดระดับอัตโนมัติ** — รายงานเท่าที่ได้พร้อมคำเตือน
ไม่ crash

**ปลดล็อกให้ครบ:** ออก page token ใหม่ที่มี `pages_read_user_content`
(+ `read_insights` ถ้าอยากได้ reach) จาก
https://developers.facebook.com/tools/explorer/ แล้วตั้ง:

```bash
export FACEBOOK_PAGE_ID=728353493701593
export FACEBOOK_PAGE_TOKEN=<token ใหม่>
```

env จะถูกใช้ก่อน token ของ Azure Bot เสมอ **บอท Messenger จึงไม่กระทบ**

## กดถูกใจคอมเมนต์อัตโนมัติ

ข้อยกเว้นเดียวของ "อ่านอย่างเดียว" ข้างบน: `like_comments.py` กดถูกใจคอมเมนต์
ในนามเพจ ใช้ `FACEBOOK_ENGAGE_TOKEN` (ไม่ใช่ `FACEBOOK_PAGE_TOKEN`)

```bash
python3 like_comments.py --list --days 14        # คอมเมนต์ที่เพจยังไม่ได้ไลก์ (JSON)
python3 like_comments.py --like <id> <id> ...    # กดถูกใจ
```

บน VM รันผ่าน cron `fb-like-comments` (`0 2,11 * * *` UTC = 09:00 และ 18:00 เวลาไทย)
pre-script `fb_like_candidates.py` ส่งรายการเข้า prompt แล้ว agent เลือกกดเฉพาะ
คอมเมนต์ที่เป็นมิตรหรือสุภาพ ข้ามคำด่า สแปม และโฆษณาเหล้า/พนัน ไม่แน่ใจ = ไม่กด

- สคริปต์**แลกเป็น page token เองก่อนกด** token ของผู้ใช้ระบบกดตรงๆ จะไลก์เป็นชื่อ
  ผู้ใช้ระบบ ไม่ใช่เพจ
- สคริปต์ไม่ตอบและไม่ลบคอมเมนต์ แม้ token จะมีสิทธิ์ทำได้

## Pitfalls

- **อย่าเอา token ของ Azure Bot ไปใช้ที่อื่น** มันมี `pages_messaging` +
  `pages_manage_metadata` ติดมาด้วย ถ้ารั่ว = ส่งข้อความในนามเพจได้ และถ้าถูก
  revoke บอท Messenger พังตาม ใช้ token แยกสำหรับอ่านเสมอ
- **token มาจากผู้ใช้ระบบ `hermes-insights`** ใน Business Manager CivicSpace —
  ไม่มีวันหมดอายุ (`expires_at` และ `data_access_expires_at` เป็น 0 ทั้งคู่) ตั้งแต่
  12 พ.ย. 2026 มันมี `pages_manage_engagement` ด้วย เพราะใช้กับงานกดไลก์ (ดูด้านล่าง)
  ถ้าต้องออกใหม่: Business settings → ผู้ใช้ระบบ → hermes-insights → สร้างโทเค็น →
  "ไม่เลย" แล้วแลกเป็น page token ด้วย `GET /{page-id}?fields=access_token`
- **token นี้ใช้ร่วมกับบอท Messenger (civicbot)** — ตั้งแต่ 14 ต.ค. 2026 civicbot ใช้
  token อ่านอย่างเดียวตัวเดียวกันเป็น `FACEBOOK_PAGE_TOKEN` เพื่อตอบผู้ใช้เรื่องโพสต์ล่าสุด
  ต่ออายุแล้วต้องอัปเดต **ทั้งสองที่**: `.env` ของ Hermes และรัน
  `tools/set-page-feed-token.sh` ใน repo civicbot ไม่งั้นบอทจะตอบว่าดึงโพสต์ไม่ได้
- **Graph API เก็บประวัติเพจได้ราว 12 เดือน** ขอย้อนไกลกว่านั้นจะได้ไม่ครบ
- **ชื่อ metric ของ Insights เปลี่ยนบ่อย** ตัวที่เคยใช้อย่าง `page_impressions`
  ถูกยกเลิกไปแล้วใน v21 ถ้าจะเพิ่ม insights ให้เช็คชื่อ metric ปัจจุบันก่อน
- **โพสต์ประเภท reel** ลิงก์จะเป็น `/reel/<id>` ไม่ใช่ `/posts/<id>` — ปกติ

## Examples

> "เพจสัปดาห์นี้เป็นไงบ้าง"

```bash
python3 "$SCRIPT" --since 7d
```
รายงานตารางมาแล้วสรุปสั้นๆ ว่าโพสต์ไหนเด่น ธีมเนื้อหาเป็นแนวไหน

> "คนคิดยังไงกับโพสต์เดือนนี้"

```bash
python3 "$SCRIPT" --since 30d --with-comments --format json
```
ถ้าขึ้นว่าดึงคอมเมนต์ไม่ได้ ให้บอกผู้ใช้ตรงๆ ว่าติดสิทธิ์ อย่าเดา sentiment
จากตัวเนื้อโพสต์แทน
