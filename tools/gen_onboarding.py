#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 App 首次启动引导页的三张全屏插图（1200x2400, WebP）。

主题：
  1. 远程查看 Linux 设备详情 / 异常监控 / 多设备管理
  2. 远程查看摄像头画面 / 控制电机舵机等外设
  3. Agent 帮你管理设备 / 下发任务 / 专属智能机器人

用法（需要 Pillow）：
  cd walle_android
  PYTHONPATH=.tools python3 tools/gen_onboarding.py
输出：
  app/src/main/res/drawable-nodpi/onboarding_1.webp
  app/src/main/res/drawable-nodpi/onboarding_2.webp
  app/src/main/res/drawable-nodpi/onboarding_3.webp
"""

import math
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- 基础配置
W, H = 1200, 2400          # 逻辑画布（与手机 1080x2400 比例一致）
S = 2                      # 超采样倍数，消除锯齿
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "src", "main", "res", "drawable-nodpi")

CN = "/System/Library/Fonts/Hiragino Sans GB.ttc"      # index 0=W3, 2=W6
MONO = "/System/Library/Fonts/Menlo.ttc"               # index 0=Regular, 1=Bold
SF = "/System/Library/Fonts/SFNS.ttf"

# 颜色
INK = (5, 7, 15)
TEXT = (240, 245, 255)
MUTED = (147, 164, 196)
DIM = (107, 122, 153)
CYAN = (34, 211, 238)
SKY = (56, 189, 248)
BLUE = (59, 130, 246)
VIOLET = (167, 139, 250)
MAGENTA = (232, 121, 249)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
WHITE = (255, 255, 255)


def font(kind, size, bold=False):
    if kind == "mono":
        return ImageFont.truetype(MONO, int(size * S), index=1 if bold else 0)
    if kind == "sf":
        return ImageFont.truetype(SF, int(size * S))
    return ImageFont.truetype(CN, int(size * S), index=2 if bold else 0)


# ---------------------------------------------------------------- 绘制封装
class Canvas:
    def __init__(self, w=W, h=H):
        self.img = Image.new("RGB", (w * S, h * S), INK)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.w, self.h = w, h

    # 坐标缩放
    @staticmethod
    def _b(box):
        return tuple(int(v * S) for v in box)

    def rrect(self, box, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._b(box), radius=int(r * S), fill=fill,
                                 outline=outline, width=max(1, int(width * S)))

    def rect(self, box, fill=None, outline=None, width=1):
        self.d.rectangle(self._b(box), fill=fill, outline=outline,
                         width=max(1, int(width * S)))

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.d.ellipse(self._b(box), fill=fill, outline=outline,
                       width=max(1, int(width * S)))

    def line(self, pts, fill, width=1):
        self.d.line([(int(x * S), int(y * S)) for x, y in pts], fill=fill,
                    width=max(1, int(width * S)), joint="curve")

    def polygon(self, pts, fill=None, outline=None):
        self.d.polygon([(int(x * S), int(y * S)) for x, y in pts], fill=fill,
                       outline=outline)

    def arc(self, box, a0, a1, fill, width=1):
        self.d.arc(self._b(box), a0, a1, fill=fill, width=max(1, int(width * S)))

    def text(self, xy, s, f, fill=TEXT, anchor="la", spacing=0):
        x, y = int(xy[0] * S), int(xy[1] * S)
        if spacing and len(s) > 1:
            # 字间距（英文小标题）
            cx = x
            for ch in s:
                self.d.text((cx, y), ch, font=f, fill=fill, anchor=anchor)
                cx += self.d.textlength(ch, font=f) + spacing * S
        else:
            self.d.text((x, y), s, font=f, fill=fill, anchor=anchor)

    def tw(self, s, f):
        return self.d.textlength(s, font=f) / S

    def paste(self, im, box):
        self.img.paste(im, self._b(box)[:2])

    def blur(self, radius):
        self.img = self.img.filter(ImageFilter.GaussianBlur(radius * S))
        self.d = ImageDraw.Draw(self.img, "RGBA")

    # ---------------- 高级效果 ----------------
    def glow(self, box, color, alpha=110, blur=110):
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse(self._b(box), fill=color + (alpha,))
        layer = layer.filter(ImageFilter.GaussianBlur(int(blur * S)))
        self.img = self.img.convert("RGBA")
        self.img.alpha_composite(layer)
        self.img = self.img.convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def round_gradient(self, box, r, c1, c2, vertical=True, outline=None,
                       outline_width=1):
        box = self._b(box)
        x0, y0, x1, y1 = box
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        if vertical:
            grad = Image.new("RGB", (1, h))
            for y in range(h):
                t = y / max(1, h - 1)
                grad.putpixel((0, y), tuple(int(c1[i] + (c2[i] - c1[i]) * t)
                                            for i in range(3)))
            grad = grad.resize((w, h), Image.BILINEAR)
        else:
            grad = Image.new("RGB", (w, 1))
            for x in range(w):
                t = x / max(1, w - 1)
                grad.putpixel((x, 0), tuple(int(c1[i] + (c2[i] - c1[i]) * t)
                                            for i in range(3)))
            grad = grad.resize((w, h), Image.BILINEAR)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r * S,
                                               fill=255)
        self.img.paste(grad, (x0, y0), mask)
        if outline:
            self.rrect([box[0] / S, box[1] / S, box[2] / S, box[3] / S], r,
                       outline=outline, width=outline_width)

    def shadow(self, box, r, alpha=120, blur=40, dy=18):
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        b = self._b(box)
        ld.rounded_rectangle((b[0], b[1] + int(dy * S), b[2], b[3] + int(dy * S)),
                             radius=int(r * S), fill=(0, 0, 0, alpha))
        layer = layer.filter(ImageFilter.GaussianBlur(int(blur * S)))
        self.img = self.img.convert("RGBA")
        self.img.alpha_composite(layer)
        self.img = self.img.convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")


def vertical_gradient(w, h, stops):
    """stops: [(pos0..1, (r,g,b)), ...]"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                c = tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                break
        for x in range(w):
            px[x, y] = c
    return img


# ---------------------------------------------------------------- 通用元素
def background(accent, accent2, page, seed=0):
    cv = Canvas()
    bg = vertical_gradient(W * S, H * S, [
        (0.00, (5, 8, 20)),
        (0.18, (8, 14, 34)),
        (0.38, (9, 16, 40)),
        (0.55, (7, 11, 26)),
        (0.72, (5, 7, 16)),
        (1.00, (4, 5, 12)),
    ])
    cv.img.paste(bg, (0, 0))

    # 主色光晕
    cv.glow([520, 120, 1500, 1100], accent, alpha=95, blur=170)
    cv.glow([-420, 420, 620, 1420], accent2, alpha=80, blur=190)
    cv.glow([420, 1080, 1180, 1600], accent2, alpha=55, blur=170)
    cv.glow([-300, 1420, 500, 2000], accent, alpha=40, blur=180)

    # 细网格
    for x in range(0, W, 100):
        cv.line([(x, 0), (x, 1560)], (255, 255, 255, 7), 1)
    for y in range(0, 1560, 100):
        cv.line([(0, y), (W, y)], (255, 255, 255, 7), 1)

    # 大圆环装饰
    cv.ellipse([-280, 110, 720, 1110], outline=(255, 255, 255, 14), width=1.4)
    cv.ellipse([-190, 200, 630, 1020], outline=(255, 255, 255, 10), width=1)
    cv.ellipse([760, 620, 1420, 1280], outline=(255, 255, 255, 11), width=1.2)
    cv.arc([760, 620, 1420, 1280], 200, 330, accent + (150,), 2.4)

    # 细小光点
    dots = [(150, 260, 3.2), (1010, 300, 2.4), (1120, 760, 3.0),
            (70, 900, 2.2), (240, 1180, 2.8), (1150, 1180, 2.2),
            (980, 1460, 3.4), (180, 1480, 2.6), (520, 240, 2.0),
            (860, 1300, 2.2), (60, 590, 2.0), (1160, 480, 2.6)]
    for i, (x, y, r) in enumerate(dots):
        c = accent if i % 3 else WHITE
        cv.ellipse([x - r, y - r, x + r, y + r], fill=c + (150,))

    # 顶部与底部压暗（保证状态栏 / 文案可读）
    top = Image.new("L", (1, int(340 * S)), 0)
    for i in range(top.height):
        top.putpixel((0, i), int(120 * (1 - i / top.height)))
    top = top.resize((W * S, int(340 * S)))
    dark = Image.new("RGB", (W * S, int(340 * S)), (4, 6, 14))
    cv.img.paste(dark, (0, 0), top)

    bottom = Image.new("L", (1, int((H - 1320) * S)), 0)
    bh = bottom.height
    for i in range(bh):
        t = i / bh
        bottom.putpixel((0, i), int(255 * min(1.0, (t / 0.42) ** 1.25)))
    bottom = bottom.resize((W * S, bh))
    dark2 = Image.new("RGB", (W * S, bh), (4, 5, 12))
    cv.img.paste(dark2, (0, int(1320 * S)), bottom)

    # 底部主色微光，避免死黑
    cv.glow([200, 1420, 1000, 1900], accent, alpha=26, blur=150)
    return cv


def card(cv, box, r=44):
    cv.shadow(box, r, alpha=150, blur=38, dy=22)
    cv.round_gradient(box, r, (20, 29, 54), (12, 18, 36), vertical=True,
                      outline=(255, 255, 255, 34), outline_width=1.2)
    # 顶部高光
    cv.line([(box[0] + r * 0.8, box[1] + 1.2),
             (box[2] - r * 0.8, box[1] + 1.2)],
            (255, 255, 255, 40), 1.2)


def glass(cv, box, r=24, alpha=14, outline=34):
    layer = Image.new("RGBA", cv.img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    b = tuple(int(v * S) for v in box)
    ld.rounded_rectangle(b, radius=int(r * S), fill=(255, 255, 255, alpha),
                         outline=(255, 255, 255, outline), width=int(1.2 * S))
    cv.img = cv.img.convert("RGBA")
    cv.img.alpha_composite(layer)
    cv.img = cv.img.convert("RGB")
    cv.d = ImageDraw.Draw(cv.img, "RGBA")


def chip(cv, x, y, label, fg, bg=None, f=None, padx=18, h=40, dot=False):
    f = f or font("cn", 19, True)
    w = cv.tw(label, f) + padx * 2 + (16 if dot else 0)
    bg = bg or (fg[0], fg[1], fg[2], 30)
    cv.rrect([x, y, x + w, y + h], h / 2, fill=bg,
             outline=(fg[0], fg[1], fg[2], 90), width=1)
    tx = x + padx
    if dot:
        cy = y + h / 2
        cv.ellipse([tx, cy - 3.6, tx + 7.2, cy + 3.6], fill=fg)
        tx += 16
    cv.text((tx, y + h / 2 + 1), label, f, fill=fg, anchor="lm")
    return w


def logo_tile(cv, box, c1, c2, kind="monitor"):
    cv.round_gradient(box, 18, c1, c2, vertical=False,
                      outline=(255, 255, 255, 80), outline_width=1)
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if kind == "monitor":
        cv.rrect([cx - 14, cy - 12, cx + 14, cy + 8], 3,
                 outline=WHITE + (235,), width=2)
        cv.line([(cx, cy + 8), (cx, cy + 14)], WHITE + (235,), 2)
        cv.line([(cx - 8, cy + 15), (cx + 8, cy + 15)], WHITE + (235,), 2)
        cv.line([(cx - 9, cy - 6), (cx - 4, cy - 6)], WHITE + (200,), 2)
    elif kind == "chip":
        cv.rrect([cx - 13, cy - 13, cx + 13, cy + 13], 4,
                 outline=WHITE + (235,), width=2)
        cv.rrect([cx - 5, cy - 5, cx + 5, cy + 5], 1.5, fill=WHITE + (220,))
        for k in (-8, 0, 8):
            cv.line([(cx + k, cy - 19), (cx + k, cy - 13)], WHITE + (200,), 2)
            cv.line([(cx + k, cy + 13), (cx + k, cy + 19)], WHITE + (200,), 2)
            cv.line([(cx - 19, cy + k), (cx - 13, cy + k)], WHITE + (200,), 2)
            cv.line([(cx + 13, cy + k), (cx + 19, cy + k)], WHITE + (200,), 2)


def arrow(cv, x, y, color, size=7, width=2.2, dir="right"):
    if dir == "right":
        cv.line([(x - size, y - size), (x, y), (x - size, y + size)], color, width)
    elif dir == "left":
        cv.line([(x + size, y - size), (x, y), (x + size, y + size)], color, width)
    elif dir == "down":
        cv.line([(x - size, y - size), (x, y), (x + size, y - size)], color, width)


def check(cv, x, y, color, s=8, width=2.6):
    cv.line([(x - s, y), (x - s * 0.25, y + s * 0.75), (x + s, y - s * 0.8)],
            color, width)


def warning(cv, cx, cy, color, s=13):
    cv.polygon([(cx, cy - s), (cx + s, cy + s * 0.85), (cx - s, cy + s * 0.85)],
               fill=color + (235,))
    cv.line([(cx, cy - s * 0.35), (cx, cy + s * 0.3)], (15, 18, 30, 255), 2.6)
    cv.ellipse([cx - 1.6, cy + s * 0.5, cx + 1.6, cy + s * 0.5 + 3.2],
               fill=(15, 18, 30, 255))


def sparkline(cv, box, values, color, fill_alpha=60, width=2.6, smooth=True):
    x0, y0, x1, y1 = box
    pts = []
    n = len(values)
    for i, v in enumerate(values):
        x = x0 + (x1 - x0) * i / (n - 1)
        y = y1 - (y1 - y0) * v
        pts.append((x, y))
    if smooth:
        pts = catmull_rom(pts)
    # 面积
    poly = pts + [(x1, y1), (x0, y1)]
    layer = Image.new("RGBA", cv.img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    lp = [(int(x * S), int(y * S)) for x, y in poly]
    ld.polygon(lp, fill=color + (fill_alpha,))
    for i in range(len(pts) - 1):
        t = i / max(1, len(pts) - 2)
        a = int(fill_alpha * (1 - t))
        ld.line([(int(pts[i][0] * S), int(pts[i][1] * S)),
                 (int(pts[i + 1][0] * S), int(pts[i + 1][1] * S))],
                fill=color + (min(255, a + 100),), width=int(width * S))
    cv.img = cv.img.convert("RGBA")
    cv.img.alpha_composite(layer)
    cv.img = cv.img.convert("RGB")
    cv.d = ImageDraw.Draw(cv.img, "RGBA")
    return pts


def catmull_rom(points, samples=12):
    if len(points) < 3:
        return points
    pts = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for j in range(samples):
            t = j / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(points[-1])
    return out


def delta_arrow(cv, cx, cy, up=True):
    s = 5
    if up:
        cv.polygon([(cx, cy - s), (cx + s, cy + s * 0.8), (cx - s, cy + s * 0.8)],
                   fill=(255, 255, 255, 210))
    else:
        cv.polygon([(cx, cy + s), (cx + s, cy - s * 0.8), (cx - s, cy - s * 0.8)],
                   fill=(255, 255, 255, 210))


def slider(cv, x, y, w, ratio, color, label, value):
    f_lab = font("cn", 18)
    f_val = font("mono", 20, True)
    cv.text((x, y), label, f_lab, fill=MUTED, anchor="la")
    cv.text((x + w, y - 2), value, f_val, fill=TEXT, anchor="ra")
    ty = y + 36
    cv.rrect([x, ty - 3, x + w, ty + 3], 6, fill=(255, 255, 255, 28))
    cv.round_gradient([x, ty - 3, x + w * ratio, ty + 3], 6,
                      color, color, vertical=False)
    kx = x + w * ratio
    cv.ellipse([kx - 11, ty - 11, kx + 11, ty + 11], fill=(255, 255, 255, 240))
    cv.ellipse([kx - 5, ty - 5, kx + 5, ty + 5], fill=color)


# ---------------------------------------------------------------- 页面 1：设备
def page1():
    accent, accent2 = SKY, (99, 102, 241)
    cv = background(accent, accent2, 1)

    # 顶部品牌
    logo_tile(cv, [120, 118, 186, 184], accent, (79, 70, 229))
    cv.text((202, 128), "WALLE", font("mono", 26, True), fill=TEXT)
    cv.text((202, 166), "树莓派智能控制台", font("cn", 17), fill=(150, 165, 195))


    # 主卡片
    box = [80, 300, 1120, 1450]
    card(cv, box)

    # 卡片标题
    logo_tile(cv, [120, 344, 184, 408], accent, (56, 120, 246), kind="monitor")
    cv.text((204, 348), "设备中枢", font("cn", 31, True), fill=TEXT)
    cv.text((204, 390), "LINUX DEVICE HUB", font("mono", 15), fill=(126, 146, 182))
    # 在线 pill
    chip(cv, 928, 356, "3 台在线", GREEN, dot=True, h=40)

    cv.line([(120, 434), (1080, 434)], (255, 255, 255, 26), 1)

    devices = [
        ("Raspberry Pi 5", "192.168.31.234", True, "CPU 12%", "MEM 45%", "48 °C", GREEN),
        ("工控机 · x86", "192.168.31.88", True, "CPU 78%", "MEM 62%", "64 °C", AMBER),
        ("Raspberry Pi 4B", "192.168.31.50", False, "—", "—", "—", (120, 132, 160)),
    ]
    y = 456
    for i, (name, ip, online, c1, c2, c3, col) in enumerate(devices):
        glass(cv, [120, y, 1080, y + 128], r=20, alpha=16 if i == 0 else 11,
              outline=40 if i == 0 else 28)
        if i == 0:
            cv.round_gradient([120, y + 16, 124.5, y + 112], 2, accent, accent,
                              vertical=True)
        # 设备图标
        cv.rrect([142, y + 34, 192, y + 84], 12, fill=(255, 255, 255, 18),
                 outline=(255, 255, 255, 40), width=1)
        cv.rrect([156, y + 46, 178, y + 62], 2, outline=col + (230,), width=2)
        cv.line([(167, y + 62), (167, y + 68)], col + (230,), 2)
        cv.line([(161, y + 69), (173, y + 69)], col + (230,), 2)

        cv.text((212, y + 26), name, font("cn", 24, True),
                fill=TEXT if online else (170, 180, 200))
        cv.text((212, y + 62), ip, font("mono", 18), fill=(126, 146, 182))
        if online:
            cv.text((212, y + 90), "uuid c004cb13…", font("mono", 15),
                    fill=(100, 116, 148))
        else:
            cv.text((212, y + 90), "上次在线 2 天前", font("cn", 15),
                    fill=(100, 116, 148))

        # 右侧状态
        cy = y + 34
        cv.ellipse([1046, cy - 5, 1056, cy + 5], fill=col)
        cv.text((1036, cy), "在线" if online else "离线", font("cn", 18),
                fill=col, anchor="ra")

        # 三项指标
        vals = [c1, c2, c3]
        vx = 700
        for j, v in enumerate(vals):
            c = col if (online and j == 0 and i == 1) else TEXT
            if not online:
                c = (135, 145, 165)
            cv.text((vx, y + 58), v, font("mono", 18, True), fill=c)
            if j < 2:
                cv.line([(vx + 88, y + 48), (vx + 88, y + 74)],
                        (255, 255, 255, 26), 1)
            vx += 112
        y += 140

    # 折线图卡片
    gb = [120, 900, 1080, 1210]
    glass(cv, gb, r=22, alpha=10, outline=26)
    cv.text((144, 922), "CPU 使用率", font("cn", 19, True), fill=(200, 212, 235))
    cv.text((144, 950), "近 1 小时", font("cn", 16), fill=(112, 130, 162))
    cv.text((322, 936), "42.6%", font("mono", 40, True), fill=accent)
    cv.text((508, 954), "峰值 68%", font("cn", 16), fill=(120, 138, 170))
    chip(cv, 958, 930, "正常", GREEN, h=34, f=font("cn", 16, True), padx=14)

    # 网格
    for gy in (1010, 1060, 1110):
        for gx in range(150, 1050, 26):
            cv.line([(gx, gy), (gx + 10, gy)], (255, 255, 255, 22), 1)
    cv.line([(150, 1000), (150, 1170)], (255, 255, 255, 20), 1)
    cv.line([(150, 1170), (1050, 1170)], (255, 255, 255, 24), 1)
    # 阈值虚线
    for gx in range(150, 1050, 22):
        cv.line([(gx, 1024), (gx + 11, 1024)], AMBER + (110,), 1.2)
    cv.text((1050, 1024), "90%", font("mono", 14), fill=AMBER + (170,), anchor="rm")

    vals = [0.46, 0.40, 0.52, 0.44, 0.60, 0.50, 0.57, 0.48, 0.68, 0.52,
            0.46, 0.62, 0.42, 0.55, 0.40, 0.50, 0.58, 0.44, 0.62, 0.54,
            0.47, 0.44, 0.52, 0.46]
    values = [1 - v for v in vals]
    sparkline(cv, [166, 1010, 1040, 1160], values, accent, fill_alpha=55)

    # 告警条
    ab = [120, 1232, 1080, 1380]
    cv.rrect(ab, 18, fill=(251, 191, 36, 22), outline=(251, 191, 36, 90), width=1)
    cv.round_gradient([120, 1232, 125, 1380], 2, AMBER, AMBER, vertical=False)
    warning(cv, 162, 1306, AMBER, 15)
    cv.text((196, 1262), "异常告警 · 温度超过阈值", font("cn", 20, True),
            fill=(255, 214, 130))
    cv.text((196, 1294), "工控机 CPU 温度 81°C（阈值 80°C）", font("cn", 17),
            fill=(197, 179, 146))
    cv.text((196, 1330), "2026-10-29 14:32:08", font("mono", 14),
            fill=(150, 137, 116))
    # 右侧「查看」
    cv.rrect([956, 1288, 1056, 1332], 22, fill=(251, 191, 36, 34),
             outline=(251, 191, 36, 100), width=1)
    cv.text((986, 1312), "查看", font("cn", 17, True), fill=AMBER, anchor="lm")
    arrow(cv, 1030, 1310, AMBER + (230,), 5, 2)

    # 卡片底部小字
    cv.text((120, 1400), "MQTT · broker www.baidong.xyz:1883", font("mono", 15),
            fill=(96, 112, 144))
    cv.ellipse([1038, 1402, 1048, 1412], fill=GREEN)
    cv.text((1030, 1408), "实时同步", font("cn", 15), fill=(110, 128, 160), anchor="rm")

    return cv


# ---------------------------------------------------------------- 页面 2：外设
def draw_robot_scene(cv, box):
    """摄像头画面里的机械臂 / 小车场景。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    # 背景：暗室渐变
    scene = vertical_gradient(int(w * S), int(h * S), [
        (0.0, (13, 30, 44)),
        (0.45, (10, 23, 35)),
        (0.72, (8, 17, 27)),
        (1.0, (5, 10, 17)),
    ])
    mask = Image.new("L", (int(w * S), int(h * S)), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, int(w * S) - 1, int(h * S) - 1),
                                          radius=int(22 * S), fill=255)
    cv.img.paste(scene, (int(x0 * S), int(y0 * S)), mask)
    # 遮罩后重设 draw
    cv.d = ImageDraw.Draw(cv.img, "RGBA")

    # 光晕
    cv.glow([x0 + 40, y0 + 20, x0 + 470, y0 + 330], (45, 212, 191), alpha=55, blur=70)
    cv.glow([x0 + 480, y0 + 60, x1 + 40, y1 + 120], (251, 191, 36), alpha=26, blur=90)

    # 远处墙面网格（透视感）
    for i in range(9):
        gx = x0 + 90 + i * 105
        cv.line([(gx, y0 + 80), (gx - 40, y0 + 300)], (120, 200, 220, 18), 1)
    for i in range(5):
        gy = y0 + 90 + i * 48
        cv.line([(x0 + 30, gy), (x1 - 20, gy + 10)], (120, 200, 220, 14), 1)

    # 桌面
    cv.polygon([(x0 + 20, y0 + 360), (x1 - 20, y0 + 340),
                (x1 - 20, y0 + 430), (x0 + 20, y0 + 430)],
               fill=(16, 34, 45, 220))
    cv.line([(x0 + 20, y0 + 360), (x1 - 20, y0 + 340)], (90, 200, 215, 40), 1.2)

    # 机械臂底座
    bx, by = x0 + 250, y0 + 360
    cv.polygon([(bx - 62, by), (bx + 62, by), (bx + 42, by - 34), (bx - 42, by - 34)],
               fill=(30, 55, 74), outline=(90, 220, 230, 70))
    cv.rrect([bx - 46, by - 56, bx + 46, by - 30], 8,
             fill=(24, 46, 62), outline=(100, 230, 240, 80), width=1)
    cv.ellipse([bx - 20, by - 70, bx + 20, by - 30], fill=(18, 40, 56),
               outline=(120, 240, 245, 150), width=2)
    cv.ellipse([bx - 7, by - 57, bx + 7, by - 43], fill=(50, 240, 225, 235))

    # 大臂
    cv.line([(bx, by - 60), (bx - 64, by - 188)], (38, 78, 98), 22)
    cv.line([(bx, by - 60), (bx - 64, by - 188)], (100, 225, 235, 80), 2)
    # 小臂
    cv.line([(bx - 64, by - 188), (bx + 76, by - 252)], (38, 78, 98), 16)
    cv.line([(bx - 64, by - 188), (bx + 76, by - 252)], (100, 225, 235, 80), 2)
    # 关节
    for jx, jy, r in ((bx - 64, by - 188, 17), (bx + 76, by - 252, 12)):
        cv.ellipse([jx - r, jy - r, jx + r, jy + r], fill=(16, 34, 48),
                   outline=(120, 240, 245, 170), width=2)
        cv.ellipse([jx - r * 0.4, jy - r * 0.4, jx + r * 0.4, jy + r * 0.4],
                   fill=(90, 240, 235, 200))
    # 夹爪
    cv.line([(bx + 76, by - 252), (bx + 102, by - 286)], (38, 78, 98), 10)
    cv.line([(bx + 102, by - 286), (bx + 86, by - 312)], (90, 220, 230, 200), 5)
    cv.line([(bx + 102, by - 286), (bx + 122, by - 306)], (90, 220, 230, 200), 5)
    cv.ellipse([bx + 96, by - 292, bx + 108, by - 280], fill=(251, 191, 36, 230))

    # 前景小车
    cx, cy = x0 + 700, y0 + 358
    cv.rrect([cx - 82, cy - 44, cx + 82, cy - 4], 12, fill=(20, 42, 58),
             outline=(120, 235, 240, 90), width=1.2)
    for wx in (cx - 54, cx + 42):
        cv.ellipse([wx, cy - 12, wx + 30, cy + 18], fill=(9, 17, 26),
                   outline=(120, 235, 240, 110), width=2)
        cv.ellipse([wx + 9, cy - 3, wx + 21, cy + 9], fill=(90, 210, 220, 200))
    # 云台相机
    cv.rrect([cx - 24, cy - 86, cx + 24, cy - 48], 8, fill=(24, 48, 64),
             outline=(120, 240, 245, 110), width=1)
    cv.ellipse([cx - 15, cy - 78, cx + 15, cy - 56], fill=(8, 14, 22),
               outline=(120, 240, 245, 180), width=2)
    cv.ellipse([cx - 7, cy - 72, cx + 5, cy - 60], fill=(70, 230, 235, 220))
    cv.line([(cx, cy - 48), (cx, cy - 44)], (100, 220, 230, 150), 5)
    # 小天线
    cv.line([(cx + 56, cy - 44), (cx + 70, cy - 96)], (120, 235, 240, 180), 2)
    cv.ellipse([cx + 66, cy - 102, cx + 74, cy - 94], fill=RED + (240,))

    # 激光扫描线
    cv.line([(x0 + 40, y0 + 320), (x1 - 30, y0 + 300)],
            (45, 212, 191, 40), 1.4)

    # 取景框角标
    m = 22
    for sx, sy, dx, dy in ((x0 + m, y0 + m, 1, 1), (x1 - m, y0 + m, -1, 1),
                           (x0 + m, y1 - m, 1, -1), (x1 - m, y1 - m, -1, -1)):
        cv.line([(sx, sy), (sx + 26 * dx, sy)], (255, 255, 255, 140), 2)
        cv.line([(sx, sy), (sx, sy + 26 * dy)], (255, 255, 255, 140), 2)
    # 中心对焦框
    fx, fy = (x0 + x1) / 2, (y0 + y1) / 2
    cv.rrect([fx - 30, fy - 30, fx + 30, fy + 30], 6,
             outline=(255, 255, 255, 55), width=1)

    # 角标文字
    cv.ellipse([x0 + 42, y0 + 40, x0 + 52, y0 + 50], fill=RED + (245,))
    cv.text((x0 + 60, y0 + 38), "REC  00:12", font("mono", 16, True),
            fill=(255, 255, 255, 225))
    cv.text((x1 - 42, y0 + 38), "1080P · 30fps", font("mono", 16, True),
            fill=(255, 255, 255, 200), anchor="ra")
    cv.text((x0 + 42, y1 - 46), "/dev/video0 · MJPG", font("mono", 15),
            fill=(190, 235, 240, 190))
    cv.text((x1 - 42, y1 - 46), "≈ 180 ms", font("mono", 15, True),
            fill=(90, 240, 220, 220), anchor="ra")


def page2():
    accent, accent2 = (45, 212, 191), (251, 191, 36)
    cv = background(accent, accent2, 2)

    logo_tile(cv, [120, 118, 186, 184], accent, (16, 148, 158))
    cv.text((202, 128), "WALLE", font("mono", 26, True), fill=TEXT)
    cv.text((202, 166), "摄像头 · 电机 · 舵机", font("cn", 17), fill=(150, 165, 195))

    box = [80, 300, 1120, 1490]
    card(cv, box)

    cv.text((120, 342), "实时画面", font("cn", 31, True), fill=TEXT)
    cv.text((120, 386), "CAMERA  /dev/video0", font("mono", 15), fill=(126, 146, 182))
    # 设备选择
    cv.rrect([798, 344, 1080, 392], 24, fill=(255, 255, 255, 16),
             outline=(255, 255, 255, 38), width=1)
    cv.text((822, 368), "USB 2.0 Camera", font("cn", 17, True), fill=(205, 218, 238),
            anchor="lm")
    arrow(cv, 1048, 368, (170, 190, 220, 220), 5, 2, "down")

    # 摄像头画面
    preview = [120, 424, 1080, 964]
    draw_robot_scene(cv, preview)
    cv.rrect(preview, 22, outline=(255, 255, 255, 52), width=1.2)

    # 画面下方参数
    cv.text((120, 992), "分辨率 ", font("cn", 16), fill=(126, 148, 180))
    cv.text((120 + cv.tw("分辨率 ", font("cn", 16)), 992), "1920×1080",
            font("mono", 16), fill=(162, 184, 214))
    cv.text((430, 992), "码率 ", font("cn", 16), fill=(126, 148, 180))
    cv.text((430 + cv.tw("码率 ", font("cn", 16)), 992), "1.0 Mbps",
            font("mono", 16), fill=(162, 184, 214))
    cv.text((720, 992), "GOP 60", font("mono", 16), fill=(126, 148, 180))
    chip(cv, 952, 980, "低延迟", (110, 240, 220), h=34, f=font("cn", 16, True), padx=14)

    cv.line([(120, 1032), (1080, 1032)], (255, 255, 255, 26), 1)

    # 控制区标题
    cv.text((120, 1052), "外设控制", font("cn", 22, True), fill=TEXT)
    cv.text((408, 1060), "实时下发 · 电机 / 舵机", font("cn", 16), fill=(118, 138, 170))
    chip(cv, 946, 1042, "已连接", GREEN, h=34, f=font("cn", 16, True), padx=14)

    # 摇杆
    jx, jy, jr = 250, 1230, 108
    cv.ellipse([jx - jr, jy - jr, jx + jr, jy + jr], fill=(8, 16, 30, 120),
               outline=(255, 255, 255, 32), width=1.2)
    cv.ellipse([jx - jr + 20, jy - jr + 20, jx + jr - 20, jy + jr - 20],
               outline=(255, 255, 255, 20), width=1)
    # 方向箭头
    for ang in (0, 90, 180, 270):
        a = math.radians(ang)
        px, py = jx + math.cos(a) * (jr - 12), jy + math.sin(a) * (jr - 12)
        cv.polygon([(px + math.cos(a) * 8, py + math.sin(a) * 8),
                    (px + math.cos(a + 2.4) * 8, py + math.sin(a + 2.4) * 8),
                    (px + math.cos(a - 2.4) * 8, py + math.sin(a - 2.4) * 8)],
                   fill=(255, 255, 255, 70))
    # 摇杆帽
    kx, ky = jx + 30, jy - 36
    cv.ellipse([kx - 42, ky - 42, kx + 42, ky + 42], fill=(20, 44, 56, 240),
               outline=(90, 240, 220, 160), width=2)
    cv.ellipse([kx - 30, ky - 30, kx + 30, ky + 30], fill=(16, 36, 48),
               outline=(150, 250, 235, 110), width=1)
    cv.ellipse([kx - 10, ky - 10, kx + 10, ky + 10], fill=(70, 240, 225, 220))
    cv.text((250, 1368), "云台 / 移动", font("cn", 19, True), fill=(196, 214, 238),
            anchor="ma")
    cv.text((250, 1398), "X +30   Y −36", font("mono", 15), fill=(110, 136, 168),
            anchor="ma")

    # 滑杆
    sx, sw = 430, 610
    slider(cv, sx, 1088, sw, 0.62, accent, "电机 PWM", "62%")
    slider(cv, sx, 1186, sw, 0.38, (56, 189, 248), "舵机 1 · 水平", "45°")
    slider(cv, sx, 1284, sw, 0.78, (167, 139, 250), "舵机 2 · 俯仰", "120°")

    # 快捷动作
    quick = [("归位", (255, 255, 255, 40)), ("录像", (251, 191, 36, 50)),
             ("截图", (56, 189, 248, 50))]
    qx = 430
    for label, col in quick:
        f = font("cn", 17, True)
        w = cv.tw(label, f) + 40
        cv.rrect([qx, 1386, qx + w, 1430], 22, fill=col,
                 outline=(255, 255, 255, 55), width=1)
        cv.text((qx + w / 2, 1409), label, f, fill=(235, 242, 255), anchor="mm")
        qx += w + 14

    # 卡片底注
    cv.text((120, 1452), "MQTT video/request · 推流中", font("cn", 15),
            fill=(96, 112, 144))
    cv.ellipse([1046, 1448, 1056, 1458], fill=accent)
    cv.text((1036, 1454), "稳定", font("cn", 15), fill=(110, 128, 160), anchor="rm")
    return cv


# ---------------------------------------------------------------- 页面 3：Agent
def draw_agent(cv, cx, cy):
    # 光环
    cv.glow([cx - 200, cy - 190, cx + 200, cy + 170], VIOLET, alpha=110, blur=80)
    for r, a in ((132, 50), (110, 80), (86, 120)):
        cv.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(185, 165, 255, a), width=1.2)
    cv.arc([cx - 158, cy - 158, cx + 158, cy + 158], 200, 340, (200, 180, 255, 170), 2.2)
    cv.arc([cx - 158, cy - 158, cx + 158, cy + 158], 20, 160, (80, 230, 230, 130), 2.2)

    # 天线
    cv.line([(cx, cy - 156), (cx, cy - 112)], (185, 165, 255, 200), 2.5)
    cv.ellipse([cx - 8, cy - 170, cx + 8, cy - 154], fill=(220, 200, 255, 245))
    cv.glow([cx - 24, cy - 186, cx + 24, cy - 138], (220, 200, 255), alpha=170, blur=24)

    # 头部
    head = [cx - 96, cy - 104, cx + 96, cy + 76]
    cv.round_gradient(head, 46, (52, 42, 102), (27, 24, 62), vertical=True,
                      outline=(190, 170, 255, 130), outline_width=1.6)
    # 面罩
    cv.rrect([cx - 76, cy - 68, cx + 76, cy + 34], 30, fill=(9, 10, 26, 235),
             outline=(160, 140, 240, 90), width=1.2)
    # 眼睛
    for ex in (cx - 34, cx + 34):
        cv.rrect([ex - 16, cy - 36, ex + 16, cy - 8], 13, fill=(120, 245, 240, 250))
        cv.glow([ex - 30, cy - 50, ex + 30, cy + 6], (120, 245, 240), alpha=120, blur=22)
    # 嘴巴
    cv.rrect([cx - 16, cy - 2, cx + 16, cy + 4], 3, fill=(150, 130, 240, 200))
    # 侧耳
    cv.rrect([cx - 112, cy - 52, cx - 96, cy + 10], 8, fill=(40, 34, 84),
             outline=(170, 150, 255, 90), width=1)
    cv.rrect([cx + 96, cy - 52, cx + 112, cy + 10], 8, fill=(40, 34, 84),
             outline=(170, 150, 255, 90), width=1)



def page3():
    accent, accent2 = VIOLET, MAGENTA
    cv = background(accent, accent2, 3)

    logo_tile(cv, [120, 118, 186, 184], (139, 92, 246), (219, 39, 119))
    cv.text((202, 128), "WALLE", font("mono", 26, True), fill=TEXT)
    cv.text((202, 166), "专属智能机器人", font("cn", 17), fill=(150, 165, 195))

    box = [80, 300, 1120, 1480]
    card(cv, box)

    # 机器人
    draw_agent(cv, 600, 490)
    cv.text((600, 640), "WALLE Agent", font("cn", 30, True), fill=TEXT, anchor="ma")
    cv.ellipse([600 - 106, 678, 600 - 96, 688], fill=GREEN)
    cv.text((600 - 88, 683), "在线 · 已连接 3 台设备", font("cn", 17),
            fill=(150, 172, 200), anchor="lm")


    # 对话区
    # 用户消息
    cv.round_gradient([508, 728, 1080, 812], 24, (99, 102, 241), (139, 92, 246),
                      vertical=False, outline=(190, 175, 255, 110), outline_width=1)
    cv.text((532, 748), "帮我巡检所有设备，异常时自动降温", font("cn", 18, True),
            fill=WHITE)
    cv.text((1056, 784), "你 · 14:58", font("cn", 13), fill=(221, 214, 255), anchor="ra")

    # Agent 回复
    ab = [120, 832, 930, 1132]
    cv.rrect(ab, 26, fill=(255, 255, 255, 13), outline=(190, 170, 255, 70), width=1)
    cv.text((148, 854), "AGENT", font("mono", 14, True), fill=(190, 170, 255))
    cv.ellipse([222, 856, 231, 865], fill=GREEN)
    cv.text((240, 855), "任务已完成", font("cn", 14), fill=(130, 200, 175))
    # 结果行
    rows = [
        (888, GREEN, "✓", "树莓派 5 · CPU 42% · 温度 48°C · 正常", None),
        (942, AMBER, "!", "工控机 · 温度 81°C → 已启动风扇", "降至 56°C"),
        (996, GREEN, "✓", "巡检报告已生成并推送", None),
        (1050, VIOLET, "→", "已下发任务：每日 08:00 自动巡检", None),
    ]
    for ry, col, mark, txt, tail in rows:
        cv.rrect([148, ry, 186, ry + 38], 12, fill=col + (34,),
                 outline=col + (90,), width=1)
        cv.text((167, ry + 19), mark, font("mono", 18, True), fill=col, anchor="mm")
        cv.text((202, ry + 19), txt, font("cn", 17), fill=(203, 215, 236), anchor="lm")
        if tail:
            f = font("cn", 14, True)
            tw = cv.tw(tail, f) + 20
            cv.rrect([930 - 22 - tw, ry + 4, 908, ry + 32], 14, fill=GREEN + (30,),
                     outline=GREEN + (80,), width=1)
            cv.text((930 - 22 - tw / 2, ry + 18), tail, f, fill=GREEN, anchor="mm")

    # 小度气泡尾巴
    cv.polygon([(148, 832), (148, 812), (176, 832)], fill=(24, 22, 44, 255))

    # 任务卡片（下一条消息预览）
    tb = [120, 1156, 1080, 1360]
    cv.rrect(tb, 24, fill=(255, 255, 255, 10), outline=(255, 255, 255, 26), width=1)
    cv.text((148, 1174), "可下发的任务", font("cn", 16, True), fill=(170, 184, 210))
    tasks = [
        ("自动巡检", "每 30 分钟", (56, 189, 248)),
        ("异常降温", "温度 > 80°C", (251, 191, 36)),
        ("生成报告", "每天 08:00", (167, 139, 250)),
    ]
    tx = 148
    for i, (name, sub, col) in enumerate(tasks):
        f = font("cn", 16, True)
        w = 280
        cv.rrect([tx, 1206, tx + w, 1310], 18, fill=col + (18,),
                 outline=col + (60,), width=1)
        cv.rrect([tx + 14, 1220, tx + 54, 1260], 10, fill=col + (36,),
                 outline=col + (90,), width=1)
        if i == 0:
            cv.ellipse([tx + 27, 1233, tx + 41, 1247], outline=col + (240,), width=2)
            cv.line([(tx + 34, 1226), (tx + 34, 1240)], col + (240,), 2)
        elif i == 1:
            cv.line([(tx + 34, 1228), (tx + 34, 1240)], col + (240,), 2)
            cv.line([(tx + 34, 1240), (tx + 42, 1246)], col + (240,), 2)
            cv.ellipse([tx + 28, 1228, tx + 40, 1240], outline=col + (240,), width=1.6)
        else:
            cv.rrect([tx + 28, 1226, tx + 40, 1252], 2, outline=col + (240,), width=1.6)
            cv.line([(tx + 31, 1232), (tx + 37, 1232)], col + (240,), 1.4)
            cv.line([(tx + 31, 1238), (tx + 37, 1238)], col + (240,), 1.4)
        cv.text((tx + 68, 1222), name, font("cn", 17, True), fill=(225, 234, 250))
        cv.text((tx + 68, 1252), sub, font("cn", 13), fill=(130, 148, 178))
        cv.text((tx + w - 16, 1306), "已启用", font("cn", 12, True),
                fill=GREEN + (220,), anchor="rs")
        cv.ellipse([tx + w - 42, 1298, tx + w - 34, 1306], fill=GREEN)
        tx += w + 16

    cv.text((120, 1398), "自然语言下发 · Agent 自主执行", font("cn", 16),
            fill=(150, 164, 192))
    cv.text((1080, 1398), "↗ 17 个任务", font("cn", 16, True),
            fill=(190, 170, 255), anchor="ra")
    return cv


# ---------------------------------------------------------------- 输出
def save(cv, page):
    img = cv.img.resize((W, H), Image.LANCZOS)
    # 上下各补一条背景（最终在 20:9 手机上会被 centerCrop 裁掉）：
    # 这样 1200x2400 的插图会整体等比缩放到 1080x2160，左右留出 26dp 边距，
    # 而不是被 centerCrop 从左右各裁掉 5%，把卡片顶到屏幕边缘。
    pad_top, pad_bottom = 134, 133
    out = Image.new("RGB", (W, H + pad_top + pad_bottom), INK)
    top_row = img.crop((0, 0, W, 1)).resize((W, pad_top), Image.BILINEAR)
    bottom_row = img.crop((0, H - 1, W, H)).resize((W, pad_bottom), Image.BILINEAR)
    out.paste(top_row, (0, 0))
    out.paste(img, (0, pad_top))
    out.paste(bottom_row, (0, pad_top + H))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"onboarding_{page}.webp")
    out.save(path, "WEBP", quality=92, method=6)
    print("saved", path, os.path.getsize(path) // 1024, "KB", out.size)
    # 同时输出未补边的预览图便于人工检查
    preview_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")
    os.makedirs(preview_dir, exist_ok=True)
    img.save(os.path.join(preview_dir, f"onboarding_{page}.png"))


def main():
    for i, fn in enumerate((page1, page2, page3), start=1):
        save(fn(), i)


if __name__ == "__main__":
    main()
