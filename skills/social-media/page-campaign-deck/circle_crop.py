#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ครอปภาพเป็นวงกลมพื้นหลังโปร่ง สำหรับวางในสไลด์ PPTX

    python3 circle_crop.py out/img out/circ --size 512 --focus 0.24

PPTX วางรูปเป็นสี่เหลี่ยมเสมอ ถ้าอยากได้วงกลมต้องครอปมาก่อน
--focus คือสัดส่วนที่เลื่อนกรอบขึ้นจากขอบบน รีลแนวตั้งมักวางหน้าคนไว้ค่อนบน 0.24 กำลังดี
"""
import argparse, glob, os, sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("ต้องมี Pillow: python3 -m pip install Pillow")


def crop_one(src, dst, size, focus):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    side = min(w, h)
    top = int((h - side) * focus) if h > side else 0
    left = (w - side) // 2 if w > side else 0
    im = im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask.resize((size, size), Image.LANCZOS))
    out.save(dst)


def main():
    ap = argparse.ArgumentParser(description="ครอปภาพเป็นวงกลมโปร่งสำหรับ PPTX")
    ap.add_argument("src_dir")
    ap.add_argument("dst_dir")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--focus", type=float, default=0.24)
    a = ap.parse_args()
    os.makedirs(a.dst_dir, exist_ok=True)
    n = 0
    for p in sorted(glob.glob(os.path.join(a.src_dir, "*.jpg")) +
                    glob.glob(os.path.join(a.src_dir, "*.png"))):
        name = os.path.splitext(os.path.basename(p))[0] + ".png"
        crop_one(p, os.path.join(a.dst_dir, name), a.size, a.focus)
        n += 1
    print(f"ครอปแล้ว {n} ใบ -> {a.dst_dir}")


if __name__ == "__main__":
    main()
