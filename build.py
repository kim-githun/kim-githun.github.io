#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
부동산 시장 온도계 — 사이트 생성기
================================================================
data/market.json + config.json + content/ → public/ (그대로 올리면 되는 정적 사이트)

  python3 build.py            # public/ 다시 만들기
  BUILD_DATE=2026-09-23 python3 build.py   # 신선도 판정 기준일 고정(검증용)

점수 계산은 여기 한 곳에서만 합니다. 페이지의 숫자는 전부 이 파일이 쓴 것이라
브라우저 스크립트 없이도 모든 내용이 문서 안에 있습니다(검색 노출·접근성).
"""
import datetime
import html
import io
import itertools
import json
import math
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from content.articles import ARTICLES as _A1    # noqa: E402
from content.articles2 import ARTICLES2 as _A2  # noqa: E402
ARTICLES = _A1 + _A2
from content.timeline import TIMELINE          # noqa: E402
from content.guides import GUIDES              # noqa: E402
from content import pages as PAGES             # noqa: E402
from content.guidebook import GUIDEBOOK        # noqa: E402
from content.changelog import CHANGES          # noqa: E402
from content.keywords import KEYWORDS          # noqa: E402
import glob as _glob                            # noqa: E402
POLICIES = sorted((json.load(io.open(f, encoding="utf-8")) for f in _glob.glob(os.path.join(HERE, "content", "policy", "*.json"))),
                  key=lambda p: (p["checked"], p["slug"]), reverse=True)
GBY = {g["slug"]: g for g in GUIDEBOOK}

PUB = os.path.join(HERE, "public")
CFG = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))
DATA = json.load(io.open(os.path.join(HERE, "data", "market.json"), encoding="utf-8"))
SRC = json.load(io.open(os.path.join(HERE, "sources.json"), encoding="utf-8"))

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = (datetime.date.fromisoformat(os.environ["BUILD_DATE"]) if os.environ.get("BUILD_DATE")
         else datetime.datetime.now(KST).date())
WARN = []

# ─────────────────────────────────────────────────────────────
# 판정 규칙 — 방법론 페이지도 이 값을 그대로 읽어 씁니다
# ─────────────────────────────────────────────────────────────
ZONES = [  # (상한, 색, 글자색, 이름)
    (30, "#b5d9fd", "#1d2d3d", "침체"),
    (55, "#749dc4", "#1d2d3d", "안정"),   # 50(눈금 한가운데)은 반드시 안정 안에
    (75, "#416180", "#f2f2f3", "주의"),
    (100, "#1d2d3d", "#f2f2f3", "과열"),
]
MAXAGE = {"주간": 24, "월간": 75, "분기": 160, "수시": 75}
REGION = {"capital": "수도권", "local": "지방", "national": "전국 공통"}
REGION_SUB = {"capital": "서울 · 인천 · 경기", "local": "수도권 외 전 지역"}
KIND = {"price": "주간 가격", "jeonse": "전세가율", "unsold": "미분양"}


def esc(s):
    return html.escape(str(s), quote=True)


def todo(label):
    WARN.append(label)
    return '<span class="todo">[입력 필요: %s]</span>' % esc(label)


def fmt(v, dec=1):
    s = ("{:,.%df}" % dec).format(v)
    return s


def zone(s):
    for z in ZONES:
        if s <= z[0]:
            return z
    return ZONES[-1]


def heat_of(d, value=None):
    v = d["value"] if value is None else value
    t = (v - d["lo"]) / float(d["hi"] - d["lo"])
    if d.get("invert"):
        t = 1 - t
    return max(0.0, min(100.0, t * 100))


def parse_asof(t):
    if not t:
        return None
    y = re.search(r"(\d{4})\s*년", t)
    if not y:
        return None
    y = int(y.group(1))
    q = re.search(r"([1-4])\s*분기", t)
    if q:
        return month_end(y, int(q.group(1)) * 3)
    m = re.search(r"(\d{1,2})\s*월", t)
    if m:
        d = re.search(r"(\d{1,2})\s*일", t)
        if d:
            return datetime.date(y, int(m.group(1)), int(d.group(1)))
        return month_end(y, int(m.group(1)))
    return datetime.date(y, 12, 31)


def month_end(y, m):
    return datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)


def freshness(d):
    w = parse_asof(d.get("asof"))
    if not w:
        return {"stale": True, "days": None, "why": "기준시점을 읽을 수 없음"}
    days = (TODAY - w).days
    lim = MAXAGE.get(d.get("cycle"), 75)
    return {"stale": days > lim, "days": days, "limit": lim,
            "why": ("%s 지표인데 기준시점 후 %d일 지남(한도 %d일)" % (d.get("cycle"), days, lim)) if days > lim else ""}


# ─────────────────────────────────────────────────────────────
# 점수 계산
# ─────────────────────────────────────────────────────────────
IND = DATA["indicators"]
BY = {d["key"]: d for d in IND}
for d in IND:
    d["heat"] = heat_of(d)
    d["zone"] = zone(d["heat"])
    d["fresh"] = freshness(d)


def region_score(region, values=None):
    """values 를 주면 그 값으로 다시 계산 (직전 갱신 비교용)"""
    mem = [d for d in IND if d["region"] == region]
    live = [d for d in mem if not d["fresh"]["stale"] and d.get("weight", 0) > 0]
    wsum = sum(d["weight"] for d in live)
    rows = []
    for d in mem:
        v = (values or {}).get(d["key"], d["value"])
        h = heat_of(d, v)
        on = d in live
        aw = d["weight"] / wsum if (on and wsum) else 0.0
        rows.append({"d": d, "heat": h, "w": aw, "contrib": h * aw, "on": on, "value": v})
    score = sum(r["contrib"] for r in rows)
    return {"score": score, "wsum": wsum, "rows": rows}


SCORE = {r: region_score(r) for r in ("capital", "local")}
GAP = SCORE["capital"]["score"] - SCORE["local"]["score"]

PREV = DATA.get("previous")
PSCORE = None
if PREV and PREV.get("values"):
    PSCORE = {r: region_score(r, PREV["values"]) for r in ("capital", "local")}


def kind_of(key):
    return key.split("_")[0]


def gap_split():
    """격차를 지표 종류별 기여 차이로 나눈다."""
    out = []
    for k in ("price", "jeonse", "unsold"):
        c = sum(r["contrib"] for r in SCORE["capital"]["rows"] if kind_of(r["d"]["key"]) == k)
        l = sum(r["contrib"] for r in SCORE["local"]["rows"] if kind_of(r["d"]["key"]) == k)
        out.append((k, c, l, c - l))
    return out


# ─────────────────────────────────────────────────────────────
# SVG — 계기
# ─────────────────────────────────────────────────────────────
SWEEP, START = 240.0, 210.0


def pt(cx, cy, r, t):
    a = math.radians(START - SWEEP * t)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def arc(cx, cy, r, t0, t1):
    x0, y0 = pt(cx, cy, r, t0)
    x1, y1 = pt(cx, cy, r, t1)
    big = 1 if SWEEP * (t1 - t0) > 180 else 0
    return "M%.1f,%.1f A%.1f,%.1f 0 %d 1 %.1f,%.1f" % (x0, y0, r, r, big, x1, y1)


def dial_svg(score, label):
    cx, cy, r = 160, 150, 118
    parts = ['<svg viewBox="0 0 320 236" role="img" aria-label="%s">' % esc(label)]
    s0 = 0
    for to, col, _, _ in ZONES:
        g = 0.006
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="16"/>' % (
            arc(cx, cy, r, s0 / 100.0 + (g if s0 else 0), to / 100.0 - (g if to < 100 else 0)), col))
        s0 = to
    for t in [0, .30, .55, .75, 1]:
        a, b = pt(cx, cy, r + 10, t), pt(cx, cy, r + 17, t)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1d1f20" stroke-width="1" opacity=".45"/>'
                     % (a[0], a[1], b[0], b[1]))
    lx0, ly0 = pt(cx, cy, r, 0)
    lx1, ly1 = pt(cx, cy, r, 1)
    parts.append('<text x="%.0f" y="%.0f" font-size="11" fill="#7a7a7d" text-anchor="middle" '
                 'font-family="ui-monospace,Menlo,monospace">0</text>' % (lx0, ly0 + 24))
    parts.append('<text x="%.0f" y="%.0f" font-size="11" fill="#7a7a7d" text-anchor="middle" '
                 'font-family="ui-monospace,Menlo,monospace">100</text>' % (lx1, ly1 + 24))
    nx, ny = pt(cx, cy, r - 26, score / 100.0)
    parts.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="#1d1f20" stroke-width="3"/>' % (cx, cy, nx, ny))
    parts.append('<circle cx="%d" cy="%d" r="7" fill="#f2f2f3" stroke="#1d1f20" stroke-width="2"/>' % (cx, cy))
    parts.append("</svg>")
    return "".join(parts)


def mini_svg(heat, color, label):
    cx, cy, r = 80, 78, 58
    t = heat / 100.0
    parts = ['<svg viewBox="0 0 160 116" role="img" aria-label="%s">' % esc(label),
             '<path d="%s" fill="none" stroke="var(--color-neutral-300)" stroke-width="9"/>' % arc(cx, cy, r, 0, 1)]
    if t > 0.004:
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="9"/>' % (arc(cx, cy, r, 0, t), color))
    for tt in [0, .30, .55, .75, 1]:
        a, b = pt(cx, cy, r + 6, tt), pt(cx, cy, r + 11, tt)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1d1f20" stroke-width="1" opacity=".35"/>'
                     % (a[0], a[1], b[0], b[1]))
    nx, ny = pt(cx, cy, r - 14, t)
    parts.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="#1d1f20" stroke-width="2"/>' % (cx, cy, nx, ny))
    parts.append('<circle cx="%d" cy="%d" r="4.5" fill="#f2f2f3" stroke="#1d1f20" stroke-width="1.5"/>' % (cx, cy))
    parts.append("</svg>")
    return "".join(parts)


def rate_svg():
    R = DATA["rates"]
    W, H, ml, mr, mt, mb = 820, 300, 44, 16, 22, 36
    x0, x1, ymax = 2008.0, max(2026.9, R[-1][0] + 0.2), 5.6

    def X(v):
        return ml + (v - x0) / (x1 - x0) * (W - ml - mr)

    def Y(v):
        return H - mb - v / ymax * (H - mt - mb)
    p = ['<svg viewBox="0 0 %d %d" role="img" aria-labelledby="rateT rateD">' % (W, H),
         '<title id="rateT">한국은행 기준금리 추이 2008년~%d년</title>' % int(R[-1][0]),
         '<desc id="rateD">2008년 5.25%%에서 2020년 0.50%%까지 내려간 뒤 2023년 3.50%%로 올랐고, '
         '2025년 2.50%%까지 내렸다가 2026년 %.2f%%로 다시 올랐다.</desc>' % R[-1][1]]
    for v in range(0, 6):
        p.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#1d1f20" stroke-opacity=".12"/>' % (ml, Y(v), W - mr, Y(v)))
        p.append('<text x="%d" y="%.1f" font-size="11" fill="#7a7a7d" text-anchor="end" '
                 'font-family="ui-monospace,Menlo,monospace">%d.0</text>' % (ml - 8, Y(v) + 4, v))
    for yr in (2008, 2012, 2016, 2020, 2024):
        p.append('<text x="%.1f" y="%d" font-size="11" fill="#7a7a7d" text-anchor="middle" '
                 'font-family="ui-monospace,Menlo,monospace">%d</text>' % (X(yr), H - 12, yr))
    d = area = ""
    for i, (x, v) in enumerate(R):
        if i == 0:
            d = "M%.1f,%.1f" % (X(x), Y(v))
            area = "M%.1f,%.1f L%.1f,%.1f" % (X(x), Y(0), X(x), Y(v))
        else:
            pv = R[i - 1][1]
            seg = " L%.1f,%.1f L%.1f,%.1f" % (X(x), Y(pv), X(x), Y(v))
            d += seg
            area += seg
    end = x1 - 0.05
    d += " L%.1f,%.1f" % (X(end), Y(R[-1][1]))
    area += " L%.1f,%.1f L%.1f,%.1f Z" % (X(end), Y(R[-1][1]), X(end), Y(0))
    p.append('<path d="%s" fill="var(--color-accent-200)" stroke="none"/>' % area)
    p.append('<path d="%s" fill="none" stroke="var(--color-accent-700)" stroke-width="2.4" stroke-linejoin="round"/>' % d)
    last_x = R[-1][0]
    # (x, y값, 문구, 정렬, 글자 기준선 y) — 선과 겹치지 않는 자리에 고정
    labels = [(2008.79, 5.0, "리먼 파산 → 급격한 인하", "start", Y(5.0) - 12, X(2008.79) + 8),
              (2020.38, 0.5, "코로나 · 사상 최저 0.50%", "middle", Y(0.5) + 20, X(2020.38) + 30),
              (2023.04, 3.5, "긴축 정점 3.50%", "end", Y(3.5) - 12, X(2023.04) - 6),
              (last_x, R[-1][1], "2026년 인상 전환 → %.2f%%" % R[-1][1], "end", Y(2.5) + 22, X(last_x) + 2)]
    for x, v, t, anc, ty, tx in labels:
        p.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="var(--color-accent-900)"><title>%s</title></circle>'
                 % (X(x), Y(v), esc(t)))
        p.append('<text x="%.1f" y="%.1f" font-size="12.5" fill="#2b2b2d" text-anchor="%s">%s</text>'
                 % (tx, ty, anc, esc(t)))
    p.append("</svg>")
    return "".join(p)


def corners():
    return '<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>'


# ─────────────────────────────────────────────────────────────
# 마크다운 (해설·안내 문서용 최소 변환기)
# ─────────────────────────────────────────────────────────────
def inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md(text):
    out, i = [], 0
    lines = text.strip().split("\n")
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.startswith("### "):
            out.append("<h3>%s</h3>" % inline(ln[4:].strip()))
            i += 1
        elif ln.startswith("## "):
            h = ln[3:].strip()
            out.append('<h2 id="%s">%s</h2>' % (slugify(h), inline(h)))
            i += 1
        elif ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith("> "):
                buf.append(inline(lines[i][2:].strip()))
                i += 1
            out.append('<blockquote class="pull">%s</blockquote>' % "<br>".join(buf))
        elif ln.startswith("| "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not set("".join(r)) <= set("-: ")]
            # 숫자 열만 오른쪽 정렬·줄바꿈 금지. 글 열은 왼쪽 정렬로 줄바꿈 허용
            numeric = [k > 0 and all(is_num(r[k] if k < len(r) else "") for r in body) for k in range(len(head))]
            th = "".join("<th%s>%s</th>" % (' class="n"' if numeric[k] else "", inline(c)) for k, c in enumerate(head))
            tb = "".join("<tr>%s</tr>" % "".join('<td%s>%s</td>' % (' class="n"' if k < len(numeric) and numeric[k] else "", inline(c))
                                                  for k, c in enumerate(r)) for r in body)
            out.append('<div class="tablewrap"><table class="table"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                       % (th, tb))
        elif ln.startswith("- [ ] "):
            buf = []
            while i < len(lines) and lines[i].startswith("- [ ] "):
                buf.append('<li><span class="box" aria-hidden="true"></span>%s</li>' % inline(lines[i][6:].strip()))
                i += 1
            out.append('<ul class="check">%s</ul>' % "".join(buf))
        elif ln.startswith("- "):
            buf = []
            while i < len(lines) and lines[i].startswith("- ") and not lines[i].startswith("- [ ] "):
                buf.append("<li>%s</li>" % inline(lines[i][2:].strip()))
                i += 1
            out.append("<ul>%s</ul>" % "".join(buf))
        elif re.match(r"\d+\. ", ln):
            buf = []
            while i < len(lines) and re.match(r"\d+\. ", lines[i]):
                buf.append("<li>%s</li>" % inline(re.sub(r"^\d+\. ", "", lines[i]).strip()))
                i += 1
            out.append("<ol>%s</ol>" % "".join(buf))
        else:
            buf = []
            while (i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "> ", "| ", "- "))
                   and not re.match(r"\d+\. ", lines[i])):
                buf.append(lines[i].strip())
                i += 1
            out.append("<p>%s</p>" % inline(" ".join(buf)))
    return "\n".join(out)


def is_num(cell):
    t = re.sub(r"\*\*|이하|이상|초과|미만|약", "", cell)
    return bool(re.search(r"\d", t)) and not re.sub(r"[\s\d.,%+\-−~()·억만천원호건가구배p년월일분기×=]", "", t)


def slugify(h):
    return "s-" + re.sub(r"[^0-9a-z가-힣]+", "-", h.lower()).strip("-")[:40]


def plain_len(text):
    return len(re.sub(r"\s", "", re.sub(r"[#*>|\-\[\]()]", "", text)))


def text_len(h):
    """보이는 글자 수(공백 포함, 줄바꿈 제외)"""
    return len(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h))).strip())


def place_ads(body_html, name="본문"):
    """본문 광고 규칙 (공백 포함 글자 수 기준)
       - 2,500자 이하 2개, 그보다 길면 3개 — 자리가 안 나오면 줄인다
       - 광고 사이 1,000자 이상, 첫 광고는 600자 이후, 광고 뒤에 300자 이상 남을 것
       - 문단(p) 바로 뒤, 다음이 문단·소제목일 때만: 표·목록·점검표·인용·버튼 옆에는 두지 않음
       - 글을 n+1 등분한 지점에 가장 가까운 조합을 고르되, 소제목 앞 자리를 우선"""
    blocks = body_html.split("\n")
    lens = [text_len(b) for b in blocks]
    total = sum(lens)
    cum, cands = 0, []
    for i, b in enumerate(blocks[:-1]):
        cum += lens[i]
        nxt = blocks[i + 1]
        if b.startswith("<p>") and nxt.startswith(("<h2", "<h3", "<p>")):
            if 600 <= cum <= total - 300:
                cands.append((i, cum, nxt.startswith(("<h2", "<h3"))))
    chosen = []
    for want in range(2 if total <= 2500 else 3, 0, -1):
        best, best_cost = None, None
        for combo in itertools.combinations(cands, want):
            if any(combo[k + 1][1] - combo[k][1] < 1000 for k in range(want - 1)):
                continue
            cost = sum(abs(c[1] - total * (j + 1) / (want + 1.0)) - (250 if c[2] else 0)
                       for j, c in enumerate(combo))
            if best_cost is None or cost < best_cost:
                best, best_cost = combo, cost
        if best:
            chosen = list(best)
            break
    at = {c[0] for c in chosen}
    out = []
    for i, b in enumerate(blocks):
        out.append(b)
        if i in at:
            out.append(ad(name))
    return "\n".join(out), len(chosen), total


# ─────────────────────────────────────────────────────────────
# 공통 틀
# ─────────────────────────────────────────────────────────────
NAV = [("gauge.html", "계기판"), ("policy/index.html", "정책 해설"), ("guides/index.html", "가이드"), ("articles/index.html", "통계 해설"),
       ("changelog.html", "변경 기록"), ("methodology.html", "산출 방법"), ("about.html", "소개")]


def url_of(path):
    base = CFG.get("site_url", "").rstrip("/")
    if not base:
        return ""
    p = path[:-len("index.html")] if path.endswith("index.html") else path
    return base + "/" + p


def ad(name):
    if CFG.get("adsense_client"):
        slots = CFG.get("adsense_slots") or {}
        if name in slots:
            return ('<div class="ad live"><ins class="adsbygoogle" style="display:block" data-ad-client="%s" '
                    'data-ad-slot="%s" data-ad-format="auto" data-full-width-responsive="true"></ins>'
                    '<script>(adsbygoogle=window.adsbygoogle||[]).push({});</script></div>'
                    % (esc(CFG["adsense_client"]), esc(slots[name])))
        return ""                 # 자동 광고에 맡긴다
    return '<div class="ad" aria-hidden="true">광고 · %s</div>' % esc(name)


def pen():
    return esc(CFG["pen_name"]) if CFG.get("pen_name") else todo("필명")


def email_link(pre=""):
    e = CFG.get("contact_email")
    if not e:
        return todo("문의 이메일")
    return '<a href="mailto:%s">%s</a>' % (esc(e), esc(e))


PAGE_META = {}   # 경로 → 최종 title·desc·본문 (검색어 점검용)
READY = bool(CFG.get("pen_name")) and bool(CFG.get("contact_email"))   # 공개 준비 완료 여부


def norm_kw(s):
    return re.sub(r"[\s·\-—,.()]", "", s or "").lower()


def keyword_check():
    """검색어 지도 점검: 주 검색어 중복(서로 순위 깎기), 제목·설명에 주 검색어가 있는지, 설명 길이."""
    out, seen = [], {}
    for path, kw in KEYWORDS.items():
        p = norm_kw(kw["primary"])
        if p in seen:
            out.append("주 검색어 중복: '%s' — %s, %s" % (kw["primary"], seen[p], path))
        seen[p] = path
        for a in kw.get("also", []):
            if norm_kw(a) in seen and seen[norm_kw(a)] != path:
                out.append("보조 검색어가 다른 페이지의 주 검색어: '%s' (%s ↔ %s)" % (a, path, seen[norm_kw(a)]))
        m = PAGE_META.get(path)
        if not m:
            out.append("검색어 지도에 있는데 생성되지 않은 페이지: " + path)
            continue
        toks = [norm_kw(t) for t in kw["primary"].split()]
        miss_t = [t for t in toks if t not in norm_kw(m["title"])]
        if miss_t:
            out.append("제목에 주 검색어 낱말 없음: %s — %s" % (path, ", ".join(miss_t)))
        miss_d = [t for t in toks if t not in norm_kw(m["desc"])]
        if miss_d:
            out.append("설명(meta)에 주 검색어 낱말 없음: %s — %s" % (path, ", ".join(miss_d)))
        if not (60 <= len(m["desc"]) <= 160):
            out.append("설명 길이 %d자 (60~160 권장): %s" % (len(m["desc"]), path))
    return out


def page(path, title, desc, body, current=None, jsonld=None, body_attr=""):
    depth = path.count("/")
    pre = "../" * depth
    kw = KEYWORDS.get(path)
    if kw:                                   # 검색어 지도에 있는 페이지는 <title> 을 검색어 앞쪽으로
        full_title = kw["title"]
        if CFG["site_name"] not in full_title and len(full_title) + len(CFG["site_name"]) + 3 <= 44:
            full_title += " | " + CFG["site_name"]
    else:
        full_title = title if path == "index.html" else "%s — %s" % (title, CFG["site_name"])
    PAGE_META[path] = {"title": full_title, "desc": desc, "body": body}
    canon = url_of(path)
    head = [
        "<!doctype html>", '<html lang="ko">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        "<title>%s</title>" % esc(full_title),
        '<meta name="description" content="%s">' % esc(desc),
        '<meta property="og:type" content="%s">' % ("article" if path.startswith(("articles/", "guides/")) and not path.endswith("index.html") else "website"),
        '<meta property="og:title" content="%s">' % esc(kw["title"] if kw else title),
        '<meta property="og:description" content="%s">' % esc(desc),
        '<meta property="og:site_name" content="%s">' % esc(CFG["site_name"]),
        '<meta property="og:locale" content="ko_KR">',
    ]
    if canon:
        head += ['<link rel="canonical" href="%s">' % esc(canon), '<meta property="og:url" content="%s">' % esc(canon)]
    if not READY:   # 필명·문의 이메일을 채우기 전에는 검색엔진에 색인하지 말라고 표시
        head.append('<meta name="robots" content="noindex">')
    head += [
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&'
        'family=Barlow+Condensed:wght@500;600;700&family=Noto+Sans+KR:wght@400;500;700&display=swap">',
        '<link rel="stylesheet" href="%sassets/industry.css">' % pre,
        '<link rel="stylesheet" href="%sassets/site.css">' % pre,
        '<link rel="icon" href="%sassets/favicon.svg" type="image/svg+xml">' % pre,
    ]
    if CFG.get("adsense_client"):
        head.append('<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=%s" '
                    'crossorigin="anonymous"></script>' % esc(CFG["adsense_client"]))
    if jsonld:
        head.append('<script type="application/ld+json">%s</script>' % json.dumps(jsonld, ensure_ascii=False))
    head.append("</head>")

    nav = "".join('<a href="%s%s"%s>%s</a>' % (pre, href, ' aria-current="page"' if current == href else "", esc(lbl))
                  for href, lbl in NAV)
    header = ('<a class="skip" href="#main">본문 바로가기</a>'
              '<header class="site-head"><div class="wrap">'
              '<a class="brand" href="%sindex.html"><b>%s</b><span class="mono">데이터 반영 %s</span></a>'
              '<nav class="gnb" aria-label="주요 메뉴">%s</nav></div></header>'
              % (pre, esc(CFG["site_name"]), esc(DATA["updated"]), nav))
    footer = ('<footer class="site-foot"><div class="wrap"><div>'
              '<p><b>알려 드려요</b> · 이 사이트의 숫자는 공개 통계를 옮기거나 계산한 것이고, 과열도는 이 사이트가 정한 방식으로 매긴 점수예요. '
              '투자 권유나 자문이 아니니, 사고팔기를 정하는 근거로 쓰지 마세요.</p>'
              '<p>개인이 운영하는 정보 사이트예요. 한국은행·통계청·한국부동산원·국토교통부 등 어떤 기관과도 관계가 없어요.</p>'
              '<p class="mono stamp">데이터 최종 반영 %s · 페이지 생성 %s</p></div>'
              '<nav aria-label="사이트 정보"><a href="%smethodology.html">산출 방법과 검증</a>'
              '<a href="%sabout.html">운영자 소개 · 문의</a><a href="%sprivacy.html">개인정보처리방침</a>'
              '<a href="%sdisclaimer.html">면책 고지</a><a href="%schangelog.html">변경 기록 · 정정</a>'
              '<a href="%sguides/index.html">가이드 전체</a><a href="%sarticles/index.html">통계 해설 전체</a></nav>'
              '</div></footer>'
              % (esc(DATA["updated"]), TODAY.isoformat(), pre, pre, pre, pre, pre, pre, pre))
    return "\n".join(head) + "\n<body%s>\n%s\n<main id=\"main\">\n%s\n</main>\n%s\n</body>\n</html>\n" % (
        body_attr, header, body, footer)


# ─────────────────────────────────────────────────────────────
# 계기판
# ─────────────────────────────────────────────────────────────
COPY = {
    "capital": "집값이 한 주에 {p}% 올랐어요. 1년 내내 이 속도면 {ann}% 안팎 오르는 셈이에요. 전세가율(집값에서 전세금이 차지하는 비율)은 {j}%라서, "
               "집값의 {exp}% 정도는 '앞으로 오를 거라는 기대'가 얹힌 값이에요. 그런데 안 팔린 집(미분양)도 {u}호로 함께 늘고 있어요. 값은 오르는데 재고도 쌓이는, 엇갈린 모습이에요.",
    "local": "집값은 한 주에 {p}% 움직여 사실상 제자리예요. 전세가율이 {j}%로 높아서, 집값에 '오를 거라는 기대'가 거의 실려 있지 않아요. "
             "전국에서 안 팔린 집(미분양)의 {share}%가 이 권역에 있어요. 뜨거운 게 아니라, 사려는 사람이 적은 시장이에요.",
}


def region_copy(r):
    p = BY["price_" + r]["value"]
    j = BY["jeonse_" + r]["value"]
    u = BY["unsold_" + r]["value"]
    nat = BY["unsold_capital"]["value"] + BY["unsold_local"]["value"]
    ann = ((1 + p / 100.0) ** 52 - 1) * 100
    return COPY[r].format(p=fmt(p, 3), ann=fmt(ann, 0), j=fmt(j, 1), exp=fmt(100 - j, 0),
                          u=fmt(u, 0), share=fmt(BY["unsold_local"]["value"] / nat * 100, 0))


def short_asof(t):
    m = re.match(r"(\d{4})년 (\d{1,2})월", t or "")
    return "%s.%s" % (m.group(1)[2:], m.group(2)) if m else (t or "")


def hero(r):
    S = SCORE[r]
    z = zone(S["score"])
    other = "local" if r == "capital" else "capital"
    oz = zone(SCORE[other]["score"])
    g = GAP if r == "capital" else -GAP
    chips = [
        (oz[1], "%s 과열도" % REGION[other], "%s · %s" % (fmt(SCORE[other]["score"]), oz[3])),
        ("#416180", "%s 대비 격차" % REGION[other], "%+.1fp %s" % (g, "더 뜨거움" if g >= 0 else "더 차가움")),
        ("#98989b", "기준금리 · 공통 · %s" % short_asof(BY["base"]["asof"]), "%s%%" % fmt(BY["base"]["value"], 2)),
        ("#98989b", "주담대 금리 · 공통 · %s" % short_asof(BY["mort"]["asof"]), "%s%%" % fmt(BY["mort"]["value"], 2)),
    ]
    chip_html = "".join('<div class="chip"><i style="background:%s"></i><div><div class="k">%s</div>'
                        '<div class="v">%s</div></div></div>' % (c, esc(k), esc(v)) for c, k, v in chips)
    legend = "".join('<span><i style="background:%s"></i>%s %s</span>' % (c, lbl, rng)
                     for (to, c, _, lbl), rng in zip(ZONES, ["0–30", "30–55", "55–75", "75–100"]))
    live = [x for x in S["rows"] if x["on"]]
    basis = "%s · %d개 지표 가중평균. 금리 등 공통 조건은 제외." % (
        " · ".join(KIND[kind_of(x["d"]["key"])] for x in live), len(live))
    return """
<div class="wrap hero r-%(r)s">
  <div>
    <div class="kicker">종합 판독 · %(rn)s (%(rs)s)</div>
    <h1>지금 %(rn)s 시장은 <em>%(zl)s</em> 구간이에요.<br>%(on)s과는 전혀 다른 모습이에요.</h1>
    <p class="lead">%(copy)s</p>
    <div class="chips">%(chips)s</div>
  </div>
  <div class="blueprint dial">%(c)s
    %(svg)s
    <div class="big" style="color:%(zc)s">%(score)s</div>
    <div class="cap">%(rn)s 과열도 <span class="mono">(0 = 빙점 · 100 = 과열)</span></div>
    <div class="legend">%(legend)s</div>
    <div class="basis">%(basis)s<br><a href="methodology.html">산식 전체 보기</a></div>
  </div>
</div>""" % {"r": r, "rn": REGION[r], "rs": REGION_SUB[r], "zl": z[3], "on": REGION[other],
             "copy": esc(region_copy(r)), "chips": chip_html, "c": corners(),
             "svg": dial_svg(S["score"], "%s 과열도 %.1f" % (REGION[r], S["score"])),
             "zc": "var(--color-accent-900)", "score": fmt(S["score"]), "legend": legend, "basis": esc(basis)}


def gauge_card(d, scope_cls=""):
    z = d["zone"]
    off = d["region"] == "national" or d["fresh"]["stale"]
    why = ""
    if d["fresh"]["stale"]:
        why = "갱신 지연으로 점수 제외 — " + d["fresh"]["why"]
    elif d["region"] == "national":
        why = "권역 점수 제외 — 두 권역에 똑같이 걸리는 조건이거나 권역 분해를 아직 확보하지 못한 지표"
    note = re.sub(r"</?b>", "", d.get("note", ""))
    return """
<div class="blueprint gauge%(off)s %(sc)s">%(c)s
  <div class="top"><h3>%(name)s</h3><span class="scope mono">%(scope)s</span></div>
  %(svg)s
  <div class="row"><div><span class="val mono">%(val)s</span> <span class="unit">%(unit)s</span></div>
    <span class="heat mono" style="background:%(zc)s;color:%(zi)s">과열도 %(h)d · %(zl)s</span></div>
  <div class="range mono"><span>%(lo)s%(inv)s</span><span>%(hi)s</span></div>
  <div class="note">%(note)s</div>
  <div class="meta mono">%(asof)s · %(cycle)s · %(delta)s</div>
  %(why)s
</div>""" % {"off": " off" if off else "", "sc": scope_cls, "c": corners(), "name": esc(d["name"]),
             "scope": esc(REGION[d["region"]]), "svg": mini_svg(d["heat"], z[1], "%s 과열도 %d" % (d["name"], d["heat"])),
             "val": fmt(d["value"], d["dec"]), "unit": esc(d.get("unit", "")), "zc": z[1], "zi": z[2],
             "h": round(d["heat"]), "zl": z[3], "lo": fmt(d["lo"], d["dec"]), "hi": fmt(d["hi"], d["dec"]),
             "inv": " (역방향)" if d.get("invert") else "", "note": esc(note), "asof": esc(d["asof"]),
             "cycle": esc(d["cycle"]), "delta": esc(d.get("delta", "")),
             "why": ('<div class="off-why">%s</div>' % esc(why)) if why else ""}


def anatomy(r):
    S = SCORE[r]
    P = PSCORE[r] if PSCORE else None
    rows = []
    for i, x in enumerate(S["rows"]):
        d = x["d"]
        dc = ""
        if P:
            pc = P["rows"][i]["contrib"]
            diff = x["contrib"] - pc
            dc = '<span class="%s">%+.1f</span>' % ("delta-up" if diff > 0.05 else "delta-down", diff)
        else:
            dc = '<span class="muted">—</span>'
        rows.append("""<tr%s><td>%s%s</td><td class="n mono">%s %s</td><td class="n mono">%d</td>
<td><div class="bar"><i style="width:%.1f%%;background:%s"></i></div></td>
<td class="n mono%s">%s</td><td class="n mono%s">%s</td><td class="n mono">%s</td></tr>""" % (
            "" if x["on"] else ' class="off"', esc(d["name"]),
            "" if x["on"] else ' <span class="tag tag-outline" style="font-size:10px">제외</span>',
            fmt(d["value"], d["dec"]), esc(d.get("unit", "")), round(x["heat"]), x["heat"], d["zone"][1],
            "" if x["on"] else " strike", "%.1f%%" % (x["w"] * 100) if x["on"] else "%.1f%%" % (d["weight"] * 100),
            "" if x["on"] else " strike", fmt(x["contrib"]) if x["on"] else "—", dc))
    prev_note = ""
    if P:
        diff = S["score"] - P["score"]
        prev_note = "직전 갱신(%s) %s → 이번 %s, <b>%+.1fp</b>." % (
            esc(PREV.get("updated", "")), fmt(P["score"]), fmt(S["score"]), diff)
    else:
        prev_note = "아직 지난번 기록이 없어 비교할 수 없어요. 다음 자동 갱신부터 이 칸에 얼마나 바뀌었는지 보여 드려요."
    return """
<div class="r-%(r)s">
  <div class="tablewrap"><table class="table" style="min-width:720px">
    <caption class="muted" style="text-align:left;font-size:12px;padding-bottom:8px">%(rn)s 과열도 산출 내역 · 기여 = 과열도 × 가중치</caption>
    <thead><tr><th>지표</th><th class="n">값</th><th class="n">과열도</th><th style="width:26%%">눈금 위치</th>
      <th class="n">가중치</th><th class="n">기여</th><th class="n">직전 대비</th></tr></thead>
    <tbody>%(rows)s
      <tr class="sum"><td colspan="5">%(rn)s 과열도 (기여의 합)</td><td class="n mono">%(score)s</td><td></td></tr>
    </tbody></table></div>
  <p class="muted" style="font-size:12.5px;margin-top:12px">%(prev)s</p>
</div>""" % {"r": r, "rn": REGION[r], "rows": "".join(rows), "score": fmt(S["score"]), "prev": prev_note}


def gap_block():
    parts = []
    for k, c, l, dlt in gap_split():
        parts.append('<div><div class="k">%s 몫</div><div class="v mono">%+.1fp</div>'
                     '<div class="s">수도권 %s · 지방 %s</div></div>' % (KIND[k], dlt, fmt(c), fmt(l)))
    biggest = max(gap_split(), key=lambda t: abs(t[3]))
    return """
<div class="gapsplit">
  <div><div class="k">권역 격차 (수도권 − 지방)</div><div class="v mono">%+.1fp</div>
    <div class="s">수도권 %s · 지방 %s</div></div>
  %s
</div>
<p class="muted" style="font-size:12.5px;margin-top:12px">두 권역의 점수 차이를 지표별로 나눠 본 거예요. 지금 차이를 가장 크게 만드는 건 <b>%s</b>예요.</p>
""" % (GAP, fmt(SCORE["capital"]["score"]), fmt(SCORE["local"]["score"]), "".join(parts), KIND[biggest[0]])


def dashboard():
    gauges = []
    for r in ("capital", "local"):
        for d in IND:
            if d["region"] == r:
                gauges.append(gauge_card(d, "r-" + r))
    for d in IND:
        if d["region"] == "national":
            gauges.append(gauge_card(d))
    tl = "".join("""<div class="cell"><div class="when mono">%s</div><h3>%s</h3><p>%s</p>
<div class="tags">%s</div>%s</div>""" % (esc(e["when"]), esc(e["h"]), esc(e["p"]),
                  "".join('<span class="tag tag-outline" style="font-size:10.5px">%s</span>' % esc(t) for t in e["tags"]),
                  ('<a class="go" href="%s">자세히 →</a>' % esc(e["href"])) if e.get("href") else "")
                 for e in TIMELINE)
    guide = "".join("""<article class="cell"><div class="when mono">%02d / %s</div><h3>%s</h3>%s
<a class="go" href="%s">긴 해설 읽기 →</a></article>""" % (
        i + 1, esc(g["sub"]), esc(g["h"]), "".join("<p>%s</p>" % esc(p) for p in g["ps"]), esc(g["href"]))
        for i, g in enumerate(GUIDES))
    latest = sorted(ARTICLES, key=lambda a: a.get("date", ""), reverse=True)[:6]
    cards = "".join(article_card(a, "articles/") for a in latest)
    status = run_status()
    ledger = "".join("""<tr><td>%s</td><td>%s</td><td class="n mono">%s %s</td><td>%s</td><td>%s</td><td>%s</td>
<td class="mono" style="font-size:11.5px">%s</td></tr>""" % (
        esc(REGION[d["region"]]), esc(d["name"]), fmt(d["value"], d["dec"]), esc(d.get("unit", "")),
        esc(d["asof"]), esc(d["src"]), esc(d["cycle"]), esc(status.get(d["key"], "")))
        for d in IND)

    body = """
<div class="region-bar"><div class="wrap">
  <div class="region-tabs" role="group" aria-label="권역 선택">
    <button type="button" data-set="capital" aria-pressed="true"><b>수도권</b><span>%(rtab_c)s</span></button>
    <button type="button" data-set="local" aria-pressed="false"><b>지방</b><span>%(rtab_l)s</span></button>
  </div>
  <p class="region-hint">권역을 누르면 아래 계기와 표가 모두 그 권역 기준으로 바뀌어요.</p>
</div></div>
%(hero_c)s
%(hero_l)s
<div class="wrap">%(ad1)s</div>

<section class="wrap sec" id="gauges">
  <div class="sec-head"><div><div class="kicker">계기판 / 권역 3개 + 공통 %(nn)d개</div><h2>지표별 바늘</h2></div>
    <p>계기마다 그 지표가 과거에 움직였던 범위를 눈금으로 삼았어요. 숫자보다 바늘이 <b>어디쯤</b> 있는지를 보세요. 점선 틀은 점수 계산에 넣지 않은 참고 지표예요.</p></div>
  <div class="gauges">%(gauges)s</div>
</section>

<section class="wrap sec" id="anatomy">
  <div class="sec-head"><div><div class="kicker">해부도</div><h2>점수는 이렇게 나왔어요</h2></div>
    <p>지표마다 0~100점으로 바꾼 뒤, 중요도에 따라 비중을 달리해 평균을 냈어요. 숨긴 계산은 없어요. 어떤 지표가 점수를 얼마나 올렸는지, 지난번보다 무엇이 바뀌었는지 아래에 모두 적었어요.</p></div>
  %(anat_c)s
  %(anat_l)s
  %(gap)s
</section>

<section class="wrap sec" id="rates">
  <div class="sec-head"><div><div class="kicker">시계열 / 2008–%(yr)d</div><h2>모든 사이클의 뒷배경, 기준금리</h2></div>
    <p>집값 그래프보다 먼저 볼 그래프예요. 2020년 0.50%%까지 내려간 금리는 2021년 집값 고점의 배경으로 자주 꼽히고, 2023년 3.50%%까지 오른 금리는 그 고점을 끌어내렸다는 평가가 많아요. 그리고 2026년 7·8월, 한국은행은 다시 금리를 올리기 시작했어요.</p></div>
  <div class="blueprint chart">%(c)s%(rate)s
    <div class="key"><span><i style="width:20px;height:2px;background:var(--color-accent-700)"></i>한국은행 기준금리 (연 %%)</span>
      <span><i style="width:9px;height:9px;border-radius:50%%;background:var(--color-accent-900)"></i>주요 변곡점</span>
      <span>자료: 한국은행 경제통계시스템(ECOS)</span></div>
  </div>
</section>

<section class="wrap sec" id="history">
  <div class="sec-head"><div><div class="kicker">기점 / 2008–현재</div><h2>지표가 꺾인 날들</h2></div>
    <p>지표는 혼자 움직이지 않아요. 금리, 정부 대책, 경제 위기가 먼저 오고 지표는 그 뒤를 따라가요.</p></div>
  <div class="grid5">%(tl)s</div>
</section>

<div class="wrap">%(ad2)s</div>

<section class="wrap sec" id="guide">
  <div class="sec-head"><div><div class="kicker">읽는 법</div><h2>이 숫자가 실제로 뜻하는 것</h2></div>
    <p>"몇이면 위험한가"보다 "이 숫자가 언제 헷갈리게 만드는가"를 아는 게 더 중요해요. 항목마다 마지막 문단에 그 함정을 적었어요.</p></div>
  <div class="grid3">%(guide)s</div>
</section>

<section class="wrap sec" id="articles">
  <div class="sec-head"><div><div class="kicker">해설 / 전 %(na)d편</div><h2>더 깊이 읽기</h2></div>
    <p><a href="articles/index.html">해설 전체 보기 →</a></p></div>
  <div class="cards">%(cards)s</div>
</section>

<section class="wrap sec" id="sources">
  <div class="sec-head"><div><div class="kicker">데이터 원장</div><h2>수치와 출처</h2></div>
    <p>지표마다 발표 주기가 달라서, 언제 기준 숫자인지 반드시 함께 적었어요. 마지막 칸은 최근 자동 갱신 때 이 숫자가 검사를 통과했는지 보여 줘요.</p></div>
  <div class="tablewrap"><table class="table" style="min-width:880px">
    <thead><tr><th>권역</th><th>지표</th><th class="n">값</th><th>기준 시점</th><th>출처</th><th>주기</th><th>최근 검증</th></tr></thead>
    <tbody>%(ledger)s</tbody></table></div>
  <p class="muted" style="font-size:12.5px;margin-top:12px">지방 미분양은 발표된 숫자가 아니라 전국에서 수도권을 빼서 계산한 값이에요. 주택구입부담지수는 서울 기준이고 분기마다 직접 입력해요.
    검사 방법은 <a href="methodology.html#s-자동-갱신과-검증">산출 방법</a>에 있어요.</p>
</section>

<script>
(function(){
  var b=document.body, btns=document.querySelectorAll("[data-set]");
  var head=document.querySelector(".site-head");
  function pad(){ if(head){ b.style.setProperty("--head-h", head.offsetHeight+"px"); } }
  pad(); window.addEventListener("resize", pad);
  function set(r, keepHash){ b.setAttribute("data-region",r);
    for(var i=0;i<btns.length;i++){ btns[i].setAttribute("aria-pressed", btns[i].getAttribute("data-set")===r ? "true":"false"); }
    try{ localStorage.setItem("mt-region", r); }catch(e){}
    if(!keepHash){ try{ history.replaceState(null, "", r==="local" ? "#local" : location.pathname+location.search); }catch(e){} }
  }
  for(var i=0;i<btns.length;i++){ btns[i].addEventListener("click", function(){ set(this.getAttribute("data-set")); }); }
  var saved=null; try{ saved=localStorage.getItem("mt-region"); }catch(e){}
  var h=(location.hash||"").replace("#","");
  set(h==="local"||h==="capital" ? h : (saved==="local" ? "local" : "capital"), true);
  window.addEventListener("hashchange", function(){ var x=location.hash.replace("#",""); if(x==="local"||x==="capital"){ set(x,true); } });
})();
</script>
""" % {"hero_c": hero("capital"), "hero_l": hero("local"), "ad1": ad("판독 아래"), "ad2": ad("연혁 아래"),
       "rtab_c": "%s · %s" % (fmt(SCORE["capital"]["score"]), zone(SCORE["capital"]["score"])[3]),
       "rtab_l": "%s · %s" % (fmt(SCORE["local"]["score"]), zone(SCORE["local"]["score"])[3]),
       "gauges": "".join(gauges), "nn": sum(1 for d in IND if d["region"] == "national"),
       "anat_c": anatomy("capital"), "anat_l": anatomy("local"), "gap": gap_block(),
       "yr": int(DATA["rates"][-1][0]), "c": corners(), "rate": rate_svg(), "tl": tl, "guide": guide,
       "na": len(ARTICLES), "cards": cards, "ledger": ledger}
    desc = ("부동산 과열 지표 계기판. 수도권 과열도 %s(%s), 지방 %s(%s), 격차 %+.1fp. 한국은행·한국부동산원·국토교통부 "
            "공개 통계로 계산한 주택시장 과열도와 산출 근거를 공개합니다.") % (
        fmt(SCORE["capital"]["score"]), zone(SCORE["capital"]["score"])[3],
        fmt(SCORE["local"]["score"]), zone(SCORE["local"]["score"])[3], GAP)
    ld = {"@context": "https://schema.org", "@type": "WebPage", "name": "계기판 — 수도권·지방 주택시장 과열도",
          "inLanguage": "ko-KR", "description": desc, "dateModified": DATA["updated"]}
    if CFG.get("site_url"):
        ld["url"] = url_of("gauge.html")
    return page("gauge.html", "계기판 — 수도권·지방 주택시장 과열도", desc, body,
                current="gauge.html", jsonld=ld, body_attr=' data-region="capital"')


def run_status():
    run = DATA.get("lastrun")
    if not run:
        return {d["key"]: ("수동 입력" if d.get("method") == "manual" else "수동 대조 2026-09-15") for d in IND}
    m = {"OK": "통과 · 갱신", "보류": "보류(급변)", "거부": "거부 · 이전 값", "실패": "조회 실패 · 이전 값",
         "수동": "수동 입력", "미설정": "미설정", "키없음": "키 없음"}
    out = {}
    for row in run.get("rows", []):
        out[row["key"]] = "%s · %s" % (m.get(row["status"], row["status"]), run["at"][:10])
    return out


# ─────────────────────────────────────────────────────────────
# 해설
# ─────────────────────────────────────────────────────────────
def article_card(a, pre=""):
    return ('<a class="blueprint acard" href="%s%s.html">%s<span class="k">%s</span>'
            '<span class="t">%s</span><span class="d">%s</span></a>'
            % (pre, a["slug"], corners(), esc(a["kicker"]), esc(a["title"]), esc(a["desc"])))


def article_page(a, idx):
    body_html = md(a["body"])
    hs = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body_html)
    body_html, a["_ads"], a["_chars"] = place_ads(body_html)
    toc = ""
    if len(hs) >= 4:
        toc = ('<nav class="blueprint toc" aria-label="목차">%s<b>목차</b><ol>%s</ol></nav>'
               % (corners(), "".join('<li><a href="#%s">%s</a></li>' % (i, t) for i, t in hs)))
    series = [x for x in ARTICLES if x.get("series") == a.get("series")]
    k = series.index(a)
    prev_a = series[k - 1] if k > 0 else None
    next_a = series[k + 1] if k + 1 < len(series) else None
    pager = ('<nav class="pager" aria-label="시리즈 이전·다음 글">%s%s</nav>' % (
        ('<a class="prev" href="%s.html"><span>이전 글 · %s</span><b>%s</b></a>' % (prev_a["slug"], esc(prev_a["kicker"]), esc(prev_a["title"])))
        if prev_a else '<span></span>',
        ('<a class="next" href="%s.html"><span>다음 글 · %s</span><b>%s</b></a>' % (next_a["slug"], esc(next_a["kicker"]), esc(next_a["title"])))
        if next_a else '<a class="next" href="index.html"><span>시리즈 끝</span><b>통계 해설 전체 보기</b></a>'))
    more = "".join(guide_card(g, "../guides/") for g in GUIDEBOOK)
    src = a.get("sources") or ("한국은행 경제통계시스템(ECOS), 국가통계포털(KOSIS), 한국부동산원, 국토교통부. "
                               "본문 숫자는 각 기관 공개 자료에서 직접 찾아 확인한 값이에요.")
    body = """
<div class="wrap narrow doc">
  <article>
    <div class="kicker">%(kicker)s</div>
    <h1>%(title)s</h1>
    <p class="sub">%(sub)s</p>
    <div class="meta"><span>글 %(pen)s</span><span>작성 %(date)s</span><span>자료 기준 %(asof)s</span>
      <span><a href="index.html">통계 해설 목록</a></span></div>
    %(toc)s
    <div class="prose">%(body)s</div>
    <div class="blueprint srcbox">%(c)s<b>자료와 한계</b><br>%(src)s
      <div class="disc">통계 읽는 법을 설명하는 글이에요. 투자 판단의 근거로 쓰지 마세요.
        지표는 시장 전체의 평균적인 움직임일 뿐, 특정 아파트나 특정 계약에 대해서는 아무것도 말해 주지 않아요.
        틀린 곳을 발견하시면 <a href="../about.html#s-문의">알려 주세요</a>. 확인한 뒤 고치고, 고친 날짜를 적어 둘게요.</div>
    </div>
    <section class="more"><h2>「%(series)s」 이어서 읽기</h2>%(pager)s</section>
    <section class="more"><h2>생활 가이드</h2><div class="cards">%(more)s</div></section>
  </article>
</div>""" % {"series": esc(a.get("series", "")), "pager": pager, "kicker": esc(a["kicker"]), "title": esc(a["title"]), "sub": esc(a["sub"]), "pen": pen(),
             "date": esc(a.get("date", "2026-09-22")), "asof": esc(a.get("data_asof", "2026년 7~8월")),
             "toc": toc, "body": body_html, "c": corners(), "src": inline(src), "more": more}
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": a["title"],
          "description": a["desc"], "datePublished": a.get("date", "2026-09-22"),
          "dateModified": a.get("updated", a.get("date", "2026-09-22")), "inLanguage": "ko-KR",
          "author": {"@type": "Person", "name": CFG.get("pen_name") or "운영자"},
          "publisher": {"@type": "Organization", "name": CFG["site_name"]}}
    if CFG.get("site_url"):
        ld["mainEntityOfPage"] = url_of("articles/%s.html" % a["slug"])
    return page("articles/%s.html" % a["slug"], a["title"], a["desc"], body,
                current="articles/index.html", jsonld=ld)


def articles_index():
    groups = {}
    for a in ARTICLES:
        groups.setdefault(a.get("series", "지표 읽는 법"), []).append(a)
    sec = ""
    for gname, items in groups.items():
        sec += '<section class="sec"><div class="sec-head"><div><div class="kicker">%d편</div><h2>%s</h2></div></div>' \
               '<div class="cards">%s</div></section>' % (len(items), esc(gname),
                                                         "".join(article_card(a) for a in items))
    body = """
<div class="wrap doc">
  <div class="kicker">통계 해설 / 전 %d편</div>
  <h1>통계 해설</h1>
  <p class="sub" style="max-width:64ch">뉴스에 나오는 부동산 숫자, 그대로 믿어도 될까요? 숫자가 언제 헷갈리게 만드는지 쉽게 풀었어요.
     글마다 마지막 부분에 조심할 점을 모아 두었어요.</p>
  %s
</div>""" % (len(ARTICLES), sec)
    return page("articles/index.html", "통계 해설", "부동산 통계를 읽는 법과 각 지표가 틀리는 순간을 다룬 해설 모음.",
                body, current="articles/index.html")


# ─────────────────────────────────────────────────────────────
# 생활 가이드 (상시 가이드 — 연도 없는 고정 주소, 검토일과 변경 기록으로 관리)
# ─────────────────────────────────────────────────────────────
RELATED = {"jongbu": ["mean-median", "kb-vs-reb"],
           "first-home": ["rate-vs-limit", "rate-types", "khai"],
           "jeonse-tenant": ["jeonse-ratio", "report-lag"]}
ABY = {a["slug"]: a for a in ARTICLES}
KIND_CLS = {"추가": "k-add", "개정": "k-rev", "정정": "k-fix"}


def season_badge(g):
    """종부세처럼 시기가 있는 가이드는 오늘 날짜에 맞는 한 줄을 붙인다."""
    if g["slug"] != "jongbu":
        return ""
    md_ = (TODAY.month, TODAY.day)
    if (9, 16) <= md_ <= (9, 30):
        t = "특례 신청 9월 30일까지"
    elif (10, 1) <= md_ <= (11, 30):
        t = "고지서 통상 11월 하순"
    elif (12, 1) <= md_ <= (12, 15):
        t = "납부 12월 15일까지"
    else:
        return ""
    return '<span class="badge">%s</span>' % esc(t)


def guide_card(g, pre=""):
    return ('<a class="blueprint acard gcard" href="%s%s.html">%s<span class="k">%s</span>%s'
            '<span class="t">%s</span><span class="d">%s</span><span class="rv mono">검토 %s</span></a>'
            % (pre, g["slug"], corners(), esc(g["kind"]), season_badge(g), esc(g["title"]), esc(g["desc"]),
               esc(g["reviewed"])))


def changes_list(items, pre=""):
    rows = []
    for c in items:
        link = c.get("href")
        txt = esc(c["text"])
        if link:
            txt = '<a href="%s%s">%s</a>' % (pre, esc(link), txt)
        rows.append('<li><span class="d mono">%s</span><span class="kind %s">%s</span><span class="x">%s</span></li>'
                    % (esc(c["date"]), KIND_CLS.get(c["kind"], ""), esc(c["kind"]), txt))
    return '<ol class="changes">%s</ol>' % "".join(rows)


def guide_page(g):
    body_html = md(g["body"])
    hs = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body_html)
    body_html, g["_ads"], g["_chars"] = place_ads(body_html)
    toc = ('<nav class="blueprint toc" aria-label="목차">%s<b>목차</b><ol>%s</ol></nav>'
           % (corners(), "".join('<li><a href="#%s">%s</a></li>' % (i, t) for i, t in hs))) if len(hs) >= 4 else ""
    official = "".join('<li><a href="%s" rel="noopener">%s</a></li>' % (esc(u), esc(lbl)) for lbl, u in g["official"])
    mine = [c for c in CHANGES if c.get("href") == "guides/%s.html" % g["slug"]]
    rel = "".join(article_card(ABY[s], "../articles/") for s in RELATED.get(g["slug"], []) if s in ABY)
    others = "".join(guide_card(o) for o in GUIDEBOOK if o is not g)
    body = """
<div class="wrap narrow doc">
  <article>
    <div class="kicker">%(kind)s</div>
    <h1>%(title)s</h1>
    <p class="sub">%(sub)s</p>
    <div class="meta"><span>글 %(pen)s</span><span>검토 %(rv)s</span><span>처음 작성 %(date)s</span>
      <span><a href="index.html">가이드 목록</a></span></div>
    <p class="notice">%(rv)s 기준으로 확인한 내용이에요. 제도가 바뀌면 본문을 고치고, 이 페이지 아래 변경 기록에 남겨요.
      신청·납부·계약 전에는 <a href="#official">공식 확인처</a>에서 마지막으로 확인하세요.</p>
    %(toc)s
    <div class="prose">%(body)s</div>
    <section class="blueprint official" id="official">%(c)s<b>공식 확인처</b>
      <ul>%(official)s</ul></section>
    <div class="blueprint srcbox">%(c)s<b>자료</b><br>%(src)s
      <div class="disc">제도를 이해하도록 돕는 일반 정보예요. 세무·법률·금융 상담을 대신하지는 못해요.
        집을 누구 이름으로 가졌는지, 소득, 지역, 계약 조건에 따라 결과가 달라져요. 틀린 곳을 발견하시면
        <a href="../about.html#s-문의">알려 주세요</a>. 확인한 뒤 고치고 아래 변경 기록에 적을게요.</div>
    </div>
    <section class="more"><h2>이 가이드의 변경 기록</h2>%(changes)s
      <p class="muted" style="font-size:12.5px">사이트 전체 기록은 <a href="../changelog.html">변경 기록</a>에서 볼 수 있어요.</p></section>
    %(rel)s
    <section class="more"><h2>다른 가이드</h2><div class="cards">%(others)s</div></section>
  </article>
</div>""" % {"kind": esc(g["kind"]), "title": esc(g["title"]), "sub": esc(g["sub"]), "pen": pen(),
             "rv": esc(g["reviewed"]), "date": esc(g["date"]), "toc": toc, "body": body_html, "c": corners(),
             "official": official, "src": inline(g["sources"]), "changes": changes_list(mine, "../"),
             "rel": ('<section class="more"><h2>관련 통계 해설</h2><div class="cards">%s</div></section>' % rel) if rel else "",
             "others": others}
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": g["title"],
          "description": g["desc"], "datePublished": g["date"], "dateModified": g["reviewed"],
          "inLanguage": "ko-KR", "author": {"@type": "Person", "name": CFG.get("pen_name") or "운영자"},
          "publisher": {"@type": "Organization", "name": CFG["site_name"]}}
    if CFG.get("site_url"):
        ld["mainEntityOfPage"] = url_of("guides/%s.html" % g["slug"])
    return page("guides/%s.html" % g["slug"], g["title"], g["desc"], body, current="guides/index.html", jsonld=ld)


def guides_index():
    body = """
<div class="wrap doc">
  <div class="kicker">생활 가이드 / 전 %d편</div>
  <h1>생활 가이드</h1>
  <p class="sub" style="max-width:66ch">세금, 대출, 전세처럼 한 번 놓치면 되돌리기 어려운 일을 언제 무엇을 해야 하는지 순서대로 정리했어요.
     글마다 확인한 날짜를 적고, 제도가 바뀌면 같은 주소에서 내용을 고친 뒤 변경 기록을 남겨요.</p>
  <section class="sec"><div class="cards">%s</div></section>
  <section class="sec"><div class="sec-head"><div><div class="kicker">원칙</div><h2>가이드를 쓰는 방식</h2></div></div>
    <ul class="plain">
      <li>숫자는 출처를 확인한 것만 쓰고, 언제 기준인지 함께 적어요.</li>
      <li>아직 국회에서 논의 중이거나 예고만 된 변경은 <b>확정 아님</b>으로 따로 표시해요.</li>
      <li>자주 바뀌는 금리나 보증료는 본문에 적지 않고, 공식 확인처로 연결해요.</li>
      <li>가이드마다 마지막에 <b>이 가이드가 맞지 않는 경우</b>를 꼭 적어요.</li>
    </ul></section>
</div>""" % (len(GUIDEBOOK), "".join(guide_card(g) for g in GUIDEBOOK))
    return page("guides/index.html", "생활 가이드", "종부세, 첫 집 대출, 전세 보증금처럼 시점을 놓치면 되돌리기 어려운 일을 정리한 상시 가이드.",
                body, current="guides/index.html")


def changelog_page():
    fixes = [c for c in CHANGES if c["kind"] == "정정"]
    body = """
<div class="wrap narrow doc">
  <div class="kicker">변경 기록</div>
  <h1>무엇을 언제 고쳤나</h1>
  <p class="sub">새 글을 올리거나, 제도가 바뀌어 내용을 고치거나, 틀린 곳을 바로잡을 때마다 여기에 적어요.</p>
  <div class="meta"><span>최근 기록 %(last)s</span><span>전체 %(n)d건 · 정정 %(nf)d건</span></div>
  <div class="prose">
    <p><span class="kind k-add">추가</span> 새 글이나 기능. <span class="kind k-rev">개정</span> 제도·수치가 바뀌었거나 구성을 바꿔 기존 내용을 고친 것.
       <span class="kind k-fix">정정</span> 이 사이트가 틀렸던 것을 고친 것. 무엇이 틀렸고 어떻게 고쳤는지 둘 다 적어요.</p>
  </div>
  <section class="more"><h2>전체 기록</h2>%(all)s</section>
  <section class="more"><h2>정정만 보기</h2>%(fix)s</section>
  <p class="muted" style="font-size:12.5px;margin-top:22px">자동 갱신되는 지표 값의 변경은 여기에 적지 않고
     <a href="methodology.html#s-최근-갱신-기록">산출 방법 → 최근 갱신 기록</a>에 회차별로 공개해요.
     틀린 곳을 발견하시면 %(email)s 로 알려 주세요.</p>
</div>""" % {"last": esc(CHANGES[0]["date"]), "n": len(CHANGES), "nf": len(fixes),
             "all": changes_list(CHANGES), "fix": changes_list(fixes) if fixes else "<p>아직 없어요.</p>",
             "email": email_link()}
    return page("changelog.html", "변경 기록", "부동산 시장 온도계의 글 추가, 제도 변경에 따른 개정, 정정 기록.",
                body, current="changelog.html")


# ─────────────────────────────────────────────────────────────
# 정책 해설 (정책마다 고정 주소 한 페이지. 발표→국회→시행 단계마다 같은 페이지를 고친다)
# ─────────────────────────────────────────────────────────────
STAGE = {"announced": ("발표됨", "st-ann"), "legislating": ("국회 심의 중", "st-leg"),
         "passed": ("확정·시행 예정", "st-pass"), "in_force": ("시행 중", "st-live")}
POLICY_RELATED = {"jongbu-reform": ["guides/jongbu.html"],
                  "capital-gains-surcharge": ["guides/jongbu.html"],
                  "loan-rules-2026": ["guides/first-home.html", "guides/jeonse-tenant.html"]}


def stage_badge(p):
    lbl, cls = STAGE.get(p["stage"], (p["stage"], "st-ann"))
    return '<span class="stage %s">%s</span>' % (cls, esc(lbl))


def policy_card(p, pre=""):
    return ('<a class="blueprint acard pcard" href="%s%s.html">%s%s<span class="t">%s</span>'
            '<span class="d">%s</span><span class="eff">%s</span><span class="rv mono">확인 %s</span></a>'
            % (pre, p["slug"], corners(), stage_badge(p), esc(p["title"]), esc(p["status"]),
               esc(p["effective"]), esc(p["checked"])))


def policy_timeline(p):
    rows = []
    for t in p.get("timeline", []):
        rows.append('<li class="%s"><span class="d mono">%s</span><span class="x">%s</span></li>'
                    % ("done" if t.get("done") else "todo", esc(t["date"]), esc(t["label"])))
    return '<ol class="ptl">%s</ol>' % "".join(rows)


def policy_page(p):
    body_html = md(p["body"])
    hs = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body_html)
    body_html, p["_ads"], p["_chars"] = place_ads(body_html)
    toc = ('<nav class="blueprint toc" aria-label="목차">%s<b>목차</b><ol>%s</ol></nav>'
           % (corners(), "".join('<li><a href="#%s">%s</a></li>' % (i, t) for i, t in hs))) if len(hs) >= 4 else ""
    official = "".join('<li><a href="%s" rel="noopener">%s</a></li>' % (esc(u), esc(lbl)) for lbl, u in p["official"])
    mine = [c for c in CHANGES if c.get("href") == "policy/%s.html" % p["slug"]]
    rel = "".join(guide_card(GBY[s[len("guides/"):-5]], "../guides/") for s in POLICY_RELATED.get(p["slug"], [])
                  if s[len("guides/"):-5] in GBY)
    others = "".join(policy_card(o) for o in POLICIES if o is not p)
    body = """
<div class="wrap narrow doc">
  <article>
    <div class="kicker">정책 해설</div>
    <h1>%(title)s</h1>
    <p class="sub">%(sub)s</p>
    <div class="meta"><span>글 %(pen)s</span><span>마지막 확인 %(checked)s</span><span>처음 작성 %(date)s</span>
      <span><a href="index.html">정책 해설 목록</a></span></div>
    <section class="blueprint pstat">%(c)s
      <div class="row">%(badge)s<b>%(status)s</b></div>
      <p class="eff"><span>적용 시점</span> %(eff)s</p>
      <details open><summary>진행 단계</summary>%(tl)s</details>
    </section>
    <p class="notice">%(checked)s 기준으로 확인한 내용이에요. 국회를 통과하기 전의 정부안은 바뀔 수 있어서 <b>확정 아님</b>으로 따로 적었어요.
      단계가 바뀌면 이 페이지를 고치고 아래 변경 기록에 남겨요. 신고·계약·대출 전에는 <a href="#official">공식 확인처</a>에서 마지막으로 확인하세요.</p>
    %(toc)s
    <div class="prose">%(body)s</div>
    <section class="blueprint official" id="official">%(c)s<b>공식 확인처</b>
      <ul>%(official)s</ul></section>
    <div class="blueprint srcbox">%(c)s<b>자료</b><br>%(src)s
      <div class="disc">정책 내용을 이해하도록 돕는 일반 정보예요. 세무·법률·금융 상담을 대신하지는 못해요.
        보유 형태, 소득, 지역, 계약 시점에 따라 결과가 달라져요. 틀린 곳을 발견하시면
        <a href="../about.html#s-문의">알려 주세요</a>. 확인한 뒤 고치고 아래 변경 기록에 적을게요.</div>
    </div>
    <section class="more"><h2>이 페이지의 변경 기록</h2>%(changes)s</section>
    %(rel)s
    <section class="more"><h2>다른 정책 해설</h2><div class="cards">%(others)s</div></section>
  </article>
</div>""" % {"title": esc(p["title"]), "sub": esc(p["sub"]), "pen": pen(), "checked": esc(p["checked"]),
             "date": esc(p.get("date", p["checked"])), "c": corners(), "badge": stage_badge(p),
             "status": esc(p["status"]), "eff": esc(p["effective"]), "tl": policy_timeline(p), "toc": toc,
             "body": body_html, "official": official, "src": inline(p["sources"]),
             "changes": changes_list(mine, "../"),
             "rel": ('<section class="more"><h2>함께 보면 좋은 가이드</h2><div class="cards">%s</div></section>' % rel) if rel else "",
             "others": others}
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": p["title"], "description": p["desc"],
          "datePublished": p.get("date", p["checked"]), "dateModified": p["checked"], "inLanguage": "ko-KR",
          "author": {"@type": "Person", "name": CFG.get("pen_name") or "운영자"},
          "publisher": {"@type": "Organization", "name": CFG["site_name"]}}
    if CFG.get("site_url"):
        ld["mainEntityOfPage"] = url_of("policy/%s.html" % p["slug"])
    return page("policy/%s.html" % p["slug"], p["title"], p["desc"], body, current="policy/index.html", jsonld=ld)


def policy_index():
    body = """
<div class="wrap doc">
  <div class="kicker">정책 해설 / 전 %d편</div>
  <h1>정책 해설</h1>
  <p class="sub" style="max-width:66ch">새로 나온 부동산 세금·대출 정책이 나한테 해당되는지, 언제부터인지, 확정됐는지를 정리했어요.
     정책마다 페이지 하나를 두고, 국회 통과나 시행처럼 단계가 바뀔 때마다 같은 페이지를 고쳐요.</p>
  <section class="sec"><div class="cards">%s</div></section>
  <section class="sec"><div class="sec-head"><div><div class="kicker">원칙</div><h2>이렇게 정리해요</h2></div></div>
    <ul class="plain">
      <li>정부 발표와 공식 자료를 먼저 보고, 언론 보도는 두 곳 이상 맞춰 본 것만 써요.</li>
      <li>국회를 통과하기 전의 내용은 <b>정부안(확정 아님)</b>으로 따로 표시해요.</li>
      <li>페이지마다 마지막 확인 날짜와 진행 단계, 아직 모르는 것을 함께 적어요.</li>
      <li>정치적 평가는 양쪽 주장을 함께 적고, 좋다 나쁘다를 판단하지 않아요.</li>
    </ul></section>
</div>""" % (len(POLICIES), "".join(policy_card(p) for p in POLICIES))
    return page("policy/index.html", "정책 해설",
                "종부세 개편안, 다주택자 양도세 중과, 전세대출 규제처럼 새로 바뀌는 부동산 정책이 나한테 해당되는지, 언제부터인지, 확정됐는지 정리했어요.",
                body, current="policy/index.html")


# ─────────────────────────────────────────────────────────────
# 첫 화면
# ─────────────────────────────────────────────────────────────
def home():
    sc, sl = SCORE["capital"]["score"], SCORE["local"]["score"]
    zc, zl = zone(sc)[3], zone(sl)[3]
    duo = "".join("""<a class="blueprint hdial" href="gauge.html#%s">%s%s<span class="n">%s</span>
<span class="r">%s <small>%s</small></span><span class="z">%s</span></a>""" % (
        r, corners(), dial_svg(S, "%s 과열도 %.1f" % (REGION[r], S)), fmt(S), REGION[r], REGION_SUB[r], zone(S)[3])
        for r, S in (("capital", sc), ("local", sl)))
    by_g = {g["slug"]: g for g in GUIDEBOOK}
    sit = [
        ("guides/first-home.html", "첫 집을 알아보는 중", by_g["first-home"]["title"],
         "정책대출 세 가지의 조건과 한도, LTV로 거꾸로 계산하는 순서, 취득세 감면을 잃는 조건.", ""),
        ("guides/jeonse-tenant.html", "전세로 사는 중", by_g["jeonse-tenant"]["title"],
         "계약 전 세금 열람부터 반환보증 가입 시점, 만기에 보증금을 못 받을 때의 순서까지.", ""),
        ("guides/jongbu.html", "종부세 대상일 수 있다", by_g["jongbu"]["title"],
         "9월 특례 신청, 11월 고지서, 12월 납부·분납·납부유예. 개편안은 확정 아님으로 분리.", season_badge(by_g["jongbu"])),
        ("articles/index.html", "기사 속 숫자가 이상하다", "통계가 틀리는 순간",
         "같은 주 서울 집값이 기관마다 다섯 배 차이 나는 이유처럼, 숫자를 읽기 전에 알아야 할 것.", ""),
    ]
    cards = "".join("""<a class="blueprint sit" href="%s">%s<span class="who">%s</span>%s<span class="t">%s</span>
<span class="d">%s</span><span class="go">읽기 →</span></a>""" % (h, corners(), esc(w), b, esc(t), esc(d))
        for h, w, t, d, b in sit)
    series = {}
    for a in ARTICLES:
        series.setdefault(a.get("series", ""), []).append(a)
    ser = "".join("""<div><div class="kicker">%d편</div><h3>%s</h3><ol class="tlist">%s</ol>
<a class="go" href="articles/index.html">전체 보기 →</a></div>""" % (
        len(items), esc(name), "".join('<li><a href="articles/%s.html">%s</a></li>' % (a["slug"], esc(a["title"]))
                                     for a in items[:4]))
        for name, items in series.items())
    body = """
<section class="wrap home-hero">
  <div>
    <div class="kicker">이번 판독 · 데이터 반영 %(upd)s</div>
    <h1><span class="nw">수도권은 <em>%(zc)s</em>,</span> <span class="nw">지방은 <em>%(zl)s</em>.</span></h1>
    <p class="lead">두 권역의 과열도 점수는 <b>%(gap)+.1f점</b> 차이 나요. 뉴스에 자주 나오는 '전국 평균' 하나로는 이 차이가 안 보여요.
      한국은행·한국부동산원·국토교통부가 공개한 통계로 계산하고, 계산 방법과 검사 기록을 모두 공개해요.</p>
    <div class="acts"><a class="btn btn-primary" href="gauge.html">계기판 전체 보기 →</a>
      <a class="btn" href="methodology.html">점수는 어떻게 매기나요?</a></div>
  </div>
  <div class="duo">%(duo)s</div>
</section>
<div class="wrap">%(ad)s</div>

<section class="wrap sec">
  <div class="sec-head"><div><div class="kicker">정책 해설 · 마지막 확인 %(pchk)s</div><h2>요즘 바뀌는 부동산 정책</h2></div>
    <p>새로 나온 세금·대출 정책이 나한테 해당되는지, 언제부터인지, 확정됐는지 정리했어요. 단계가 바뀔 때마다 같은 페이지를 고쳐요.</p></div>
  <div class="cards">%(pcards)s</div>
  <a class="go" href="policy/index.html">정책 해설 전체 →</a>
</section>

<section class="wrap sec">
  <div class="sec-head"><div><div class="kicker">생활 가이드</div><h2>지금 내 상황에서 확인할 것</h2></div>
    <p>세금, 대출, 전세 보증금처럼 때를 놓치면 되돌리기 어려운 일부터 정리했어요. 글마다 확인한 날짜와 변경 기록이 있어요.</p></div>
  <div class="sits">%(cards)s</div>
</section>

<section class="wrap sec home-two">
  <div>
    <div class="sec-head"><div><div class="kicker">변경 기록</div><h2>최근에 고친 것</h2></div></div>
    %(changes)s
    <a class="go" href="changelog.html">변경 기록 전체 →</a>
  </div>
  <div>
    <div class="sec-head"><div><div class="kicker">통계 해설 / 전 %(na)d편</div><h2>숫자를 읽기 전에</h2></div></div>
    <div class="series">%(ser)s</div>
  </div>
</section>
""" % {"upd": esc(DATA["updated"]), "zc": zc, "zl": zl, "gap": GAP, "duo": duo, "ad": ad("홈 계기판 아래"),
       "cards": cards, "changes": changes_list(CHANGES[:5]), "na": len(ARTICLES), "ser": ser,
       "pchk": esc(max(p["checked"] for p in POLICIES)) if POLICIES else "",
       "pcards": "".join(policy_card(p, "policy/") for p in POLICIES[:3])}
    desc = ("공개 통계로 읽는 집값 지표. 수도권 과열도 %s(%s), 지방 %s(%s). 종부세·첫 집 대출·전세 보증금 생활 가이드와 통계 해설."
            % (fmt(sc), zc, fmt(sl), zl))
    ld = {"@context": "https://schema.org", "@type": "WebSite", "name": CFG["site_name"],
          "inLanguage": "ko-KR", "description": desc}
    if CFG.get("site_url"):
        ld["url"] = CFG["site_url"].rstrip("/") + "/"
    return page("index.html", "%s — %s" % (CFG["site_name"], CFG.get("tagline", "")), desc, body,
                current=None, jsonld=ld)


# ─────────────────────────────────────────────────────────────
# 안내 페이지 (산출 방법 · 소개 · 개인정보 · 면책)
# ─────────────────────────────────────────────────────────────
def doc_page(path, kicker, title, sub, desc, body_md, current=None, extra="", updated=None):
    body = """
<div class="wrap narrow doc">
  <div class="kicker">%s</div>
  <h1>%s</h1>
  <p class="sub">%s</p>
  <div class="meta"><span>최종 수정 %s</span></div>
  <div class="prose">%s</div>
  %s
</div>""" % (esc(kicker), esc(title), esc(sub), esc(updated or TODAY.isoformat()), body_md, extra)
    return page(path, title, desc, body, current=current)


def ctx():
    """안내 문서에 끼워 넣을 값 — 비어 있으면 노란 표시."""
    return {
        "site": CFG["site_name"], "pen": pen(), "email": email_link(),
        "updated": DATA["updated"], "launched": CFG.get("launched", ""),
        "n_articles": len(ARTICLES),
        "bio": (esc(CFG["operator_bio"]) if CFG.get("operator_bio")
                else todo("운영자 소개 한두 문장 — 이 사이트를 만든 이유나 관련 경험. 사실인 것만")),
    }


def methodology():
    zones = "".join("<tr><td>%s</td><td class='n mono'>%s</td></tr>" % (lbl, rng)
                    for (to, c, _, lbl), rng in zip(ZONES, ["0 ~ 30", "30 ~ 55", "55 ~ 75", "75 ~ 100"]))
    scales = "".join("""<tr><td>%s</td><td>%s</td><td class="n mono">%s ~ %s %s</td><td>%s</td>
<td class="n mono">%s</td><td>%s</td></tr>""" % (
        esc(REGION[d["region"]]), esc(d["name"]), fmt(d["lo"], d["dec"]), fmt(d["hi"], d["dec"]),
        esc(d.get("unit", "")), "높을수록 차가움" if d.get("invert") else "높을수록 뜨거움",
        ("%.1f%%" % (d["weight"] / SCORE[d["region"]]["wsum"] * 100)) if d.get("weight") and d["region"] in SCORE
        and SCORE[d["region"]]["wsum"] else "점수 제외", esc(d["cycle"])) for d in IND)
    ages = "".join("<tr><td>%s</td><td class='n mono'>%d일</td></tr>" % (k, v) for k, v in MAXAGE.items())
    def human(expr):
        names = {"price_capital": "수도권 주간 변동률", "price_local": "지방 주간 변동률",
                 "jeonse_capital": "수도권 전세가율", "jeonse_local": "지방 전세가율",
                 "unsold_capital": "수도권 미분양", "unsold_local": "지방 미분양",
                 "bad": "준공후 미분양", "base": "기준금리", "mort": "주담대 금리", "vol": "거래량"}
        e = re.sub(r"abs\((.+)\)", r"|\1|", expr)
        for k in sorted(names, key=len, reverse=True):
            e = re.sub(r"\b%s\b" % k, names[k], e)
        return e.replace(" - ", " − ").replace(" / ", " ÷ ")

    def rng(c):
        lo, hi = c.get("min"), c.get("max")
        if lo is None:
            return "%s 이하" % hi
        if hi is None:
            return "%s 이상" % lo
        return "%s ~ %s" % (lo, hi)
    checks = "".join("<li><b>%s</b> 가 %s — %s</li>" % (esc(human(c["expr"])), esc(rng(c)), esc(c.get("why", "")))
                     for c in SRC.get("checks", []))
    bounds = "".join("<tr><td>%s</td><td class='n mono'>%s ~ %s</td><td class='n mono'>%s</td></tr>" % (
        esc(BY[k]["name"] + (" · " + REGION[BY[k]["region"]] if BY[k]["region"] != "national" else "")),
        fmt(v["bounds"][0], 1 if abs(v["bounds"][1]) < 200 else 0), fmt(v["bounds"][1], 1 if abs(v["bounds"][1]) < 200 else 0),
        ("±%s%%p" % v["max_jump"]["abs"]) if "abs" in v.get("max_jump", {}) else ("±%s%%" % v["max_jump"].get("pct")))
        for k, v in SRC["indicators"].items() if v.get("bounds") and k in BY)
    run = DATA.get("lastrun")
    if run:
        log_rows = "".join("<tr><td class='mono'>%s</td><td>%s</td><td style='font-size:12.5px'>%s</td></tr>" % (
            esc(r["key"]), esc(r["status"]), esc(r["detail"])) for r in run["rows"])
        log_html = ("<p>최근 자동 갱신: <b>%s</b> · 판정 <b>%s</b></p><div class='tablewrap'><table class='table'>"
                    "<thead><tr><th>항목</th><th>상태</th><th>내용</th></tr></thead><tbody>%s</tbody></table></div>"
                    % (esc(run["at"]), esc(run["verdict"]), log_rows))
    else:
        log_html = ("<p>아직 자동 갱신 기록이 없어요. 지금 숫자는 2026년 9월 15일에 각 기관 API에서 직접 받아 "
                    "확인한 값이에요. 첫 자동 갱신이 돌면 이 자리에 회차마다 검사 결과를 그대로 공개해요.</p>")
    body_md = md(PAGES.METHODOLOGY).format(**ctx())
    body_md = (body_md.replace("<p>[[ZONES]]</p>", "<div class='tablewrap'><table class='table'><thead><tr><th>구간</th>"
                               "<th class='n'>과열도</th></tr></thead><tbody>%s</tbody></table></div>" % zones)
               .replace("<p>[[SCALES]]</p>", "<div class='tablewrap'><table class='table' style='min-width:640px'><thead><tr>"
                        "<th>권역</th><th>지표</th><th class='n'>눈금 (0 ~ 100)</th><th>방향</th><th class='n'>가중치</th>"
                        "<th>주기</th></tr></thead><tbody>%s</tbody></table></div>" % scales)
               .replace("<p>[[AGES]]</p>", "<div class='tablewrap'><table class='table'><thead><tr><th>주기</th>"
                        "<th class='n'>기준시점 후 허용 기간</th></tr></thead><tbody>%s</tbody></table></div>" % ages)
               .replace("<p>[[BOUNDS]]</p>", "<div class='tablewrap'><table class='table'><thead><tr><th>지표</th>"
                        "<th class='n'>허용 범위</th><th class='n'>1회 변동 한도</th></tr></thead><tbody>%s</tbody></table></div>" % bounds)
               .replace("<p>[[CHECKS]]</p>", "<ul>%s</ul>" % checks)
               .replace("<p>[[LOG]]</p>", log_html))
    return doc_page("methodology.html", "산출 방법과 검증", "점수는 이렇게 계산하고, 숫자는 이렇게 확인해요",
                    "공표 통계가 아닌 이 사이트의 자체 점수가 어떤 산식과 가중치로 나오는지, 자동 갱신된 숫자를 어떻게 검증하는지.",
                    "과열도 계산 방법, 지표별 눈금과 비중, 오래된 숫자를 빼는 규칙, 자동 갱신 안전장치와 최근 검사 기록을 공개해요.",
                    body_md, current="methodology.html", updated=DATA["updated"])


# ─────────────────────────────────────────────────────────────
# 부속 파일
# ─────────────────────────────────────────────────────────────
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#f2f2f3"/>'
           '<path d="M6 22 A10 10 0 0 1 26 22" fill="none" stroke="#5980a6" stroke-width="3"/>'
           '<line x1="16" y1="22" x2="22" y2="13" stroke="#1d1f20" stroke-width="2"/>'
           '<circle cx="16" cy="22" r="2.2" fill="#1d1f20"/></svg>')


def lastmod_of(path):
    """사이트맵 lastmod = 내용이 실제로 바뀐 날. 빌드한 날을 쓰지 않는다(구글은 날짜만 바뀐 갱신을 나쁜 신호로 봄)."""
    last_change = CHANGES[0]["date"] if CHANGES else CFG.get("launched")
    if path.startswith("articles/") and path != "articles/index.html":
        a = ABY[path[len("articles/"):-5]]
        return a.get("updated", a.get("date"))
    if path.startswith("policy/") and path != "policy/index.html":
        return [p for p in POLICIES if "policy/%s.html" % p["slug"] == path][0]["checked"]
    if path == "policy/index.html":
        return max(p["checked"] for p in POLICIES)
    if path.startswith("guides/") and path != "guides/index.html":
        g = [x for x in GUIDEBOOK if "guides/%s.html" % x["slug"] == path][0]
        return g["reviewed"]
    if path == "articles/index.html":
        return max(a.get("updated", a.get("date")) for a in ARTICLES)
    if path == "guides/index.html":
        return max(g["reviewed"] for g in GUIDEBOOK)
    if path in ("index.html", "gauge.html", "methodology.html"):
        return max(DATA["updated"], last_change) if path == "index.html" else DATA["updated"]
    if path == "changelog.html":
        return last_change
    return getattr(PAGES, "UPDATED", {}).get(path, CFG.get("launched"))


def write(rel, text):
    p = os.path.join(PUB, rel)
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    if os.path.isdir(PUB):
        shutil.rmtree(PUB)
    os.makedirs(os.path.join(PUB, "assets"))
    shutil.copy(os.path.join(HERE, "assets", "industry.css"), os.path.join(PUB, "assets", "industry.css"))
    shutil.copy(os.path.join(HERE, "assets", "site.css"), os.path.join(PUB, "assets", "site.css"))
    write("assets/favicon.svg", FAVICON)

    pages = ["index.html", "gauge.html", "guides/index.html", "changelog.html"]
    write("index.html", home())
    write("gauge.html", dashboard())
    write("guides/index.html", guides_index())
    for g in GUIDEBOOK:
        write("guides/%s.html" % g["slug"], guide_page(g))
        pages.append("guides/%s.html" % g["slug"])
    write("changelog.html", changelog_page())
    write("policy/index.html", policy_index())
    pages.append("policy/index.html")
    for p in POLICIES:
        write("policy/%s.html" % p["slug"], policy_page(p))
        pages.append("policy/%s.html" % p["slug"])
    write("articles/index.html", articles_index())
    pages.append("articles/index.html")
    for i, a in enumerate(ARTICLES):
        write("articles/%s.html" % a["slug"], article_page(a, i))
        pages.append("articles/%s.html" % a["slug"])
    write("methodology.html", methodology())
    c = ctx()
    for path, kicker, title, sub, desc, body in PAGES.STATIC:
        write(path, doc_page(path, kicker, title, sub, desc, md(body).format(**c),
                             current="about.html" if path == "about.html" else None,
                             updated=getattr(PAGES, "UPDATED", {}).get(path, CFG.get("launched"))))
        pages.append(path)
    pages.append("methodology.html")
    write("404.html", page("404.html", "페이지를 찾을 수 없어요", "요청한 페이지가 없어요.",
                           '<div class="wrap narrow doc"><h1>페이지를 찾을 수 없어요</h1>'
                           '<p class="sub">주소가 바뀌었거나 없어진 페이지예요. <a href="/">첫 화면으로 가기</a> · <a href="/gauge.html">계기판</a> · <a href="/guides/index.html">생활 가이드</a></p></div>'))

    base = CFG.get("site_url", "").rstrip("/")
    if base:
        urls = "".join("<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (esc(url_of(p)), lastmod_of(p))
                       for p in pages)
        write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n' % urls)
        write("robots.txt", "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % base)
        host = re.sub(r"^https?://", "", base)
        if not host.endswith("github.io"):
            write("CNAME", host + "\n")
    else:
        WARN.append("사이트 주소(site_url) — sitemap.xml·robots.txt·canonical 이 생성되지 않았습니다")
        write("robots.txt", "User-agent: *\nAllow: /\n")
    if CFG.get("adsense_client"):
        pub = CFG["adsense_client"].replace("ca-", "")
        write("ads.txt", "google.com, %s, DIRECT, f08c47fec0942fa0\n" % pub)
    else:
        WARN.append("애드센스 게시자 ID(adsense_client) — 승인 후 입력하면 ads.txt 와 광고 코드가 생깁니다")
    write(".nojekyll", "")
    if CFG.get("indexnow_key"):          # IndexNow 키 확인 파일 (공개돼도 되는 값)
        write("%s.txt" % CFG["indexnow_key"], CFG["indexnow_key"])

    # 보고
    print("생성: %d개 페이지 → %s" % (len(pages) + 1, PUB))
    print("수도권 %.1f (%s) · 지방 %.1f (%s) · 격차 %+.1fp · 판정 기준일 %s" % (
        SCORE["capital"]["score"], zone(SCORE["capital"]["score"])[3],
        SCORE["local"]["score"], zone(SCORE["local"]["score"])[3], GAP, TODAY))
    stale = [d["name"] + "(" + REGION[d["region"]] + ")" for d in IND if d["fresh"]["stale"]]
    if stale:
        print("갱신 지연으로 점수 제외: " + ", ".join(stale))
    print("본문 광고 자리: " + ", ".join("%s %d자→%d개" % (x["slug"], x["_chars"], x["_ads"]) for x in POLICIES + GUIDEBOOK + ARTICLES))
    short = [(a["slug"], plain_len(a["body"])) for a in ARTICLES if plain_len(a["body"]) < 1200]  # 공식 기준은 없음. 지나치게 짧은 글만 경고
    if short:
        print("본문 1,200자 미만 해설: %s" % short)
    if not READY:
        print("\n※ 필명·문의 이메일이 비어 있어 모든 페이지에 noindex(검색 색인 금지)를 붙였습니다. config.json 을 채우면 풀립니다.")
    kwp = keyword_check()
    if kwp:
        print("\n검색어 지도 점검:")
        for w in kwp:
            print("  - " + w)
    else:
        print("검색어 지도 점검: 이상 없음 (%d페이지)" % len(KEYWORDS))
    uniq = sorted(set(WARN))
    if uniq:
        print("\n출시 전 채울 것:")
        for w in uniq:
            print("  - " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
