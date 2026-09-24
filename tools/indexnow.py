#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""바뀐 페이지만 IndexNow 로 검색엔진에 알린다. (build.py 로 public/ 을 만들고 배포한 뒤 실행)

  python3 tools/indexnow.py            # 바뀐 페이지 전송, data/indexnow.json 갱신
  python3 tools/indexnow.py --dry-run  # 무엇이 전송될지만 출력

- '바뀜' 판정은 페이지의 <main> 본문 해시로 한다. 머리·꼬리의 생성 날짜만 바뀐 페이지는 보내지 않는다.
- 보내는 곳: 네이버 서치어드바이저, IndexNow 공용 창구(참여 검색엔진끼리 공유).
- config.json 의 site_url·indexnow_key 가 비어 있으면 아무것도 하지 않는다.
- 전송이 실패해도 종료코드 0 (배포를 막지 않음). 실패한 곳은 다음 실행 때 다시 보낸다."""
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(HERE, "public")
STATE = os.path.join(HERE, "data", "indexnow.json")
ENDPOINTS = ["https://api.searchadvisor.naver.com/indexnow", "https://api.indexnow.org/indexnow"]


def main_hash(path):
    t = io.open(path, encoding="utf-8").read()
    m = re.search(r'<main id="main">(.*)</main>', t, re.S)
    return hashlib.sha256((m.group(1) if m else t).encode("utf-8")).hexdigest()[:16]


def pages():
    out = {}
    for d, _, fs in os.walk(PUB):
        for f in fs:
            if f.endswith(".html") and f != "404.html":
                rel = os.path.relpath(os.path.join(d, f), PUB).replace(os.sep, "/")
                out[rel] = main_hash(os.path.join(d, f))
    return out


def url_of(base, rel):
    return base + "/" + (rel[:-len("index.html")] if rel.endswith("index.html") else rel)


def post(endpoint, body):
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # 네트워크 오류
        return "오류: %s" % e


def main():
    dry = "--dry-run" in sys.argv
    cfg = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    base, key = cfg.get("site_url", "").rstrip("/"), cfg.get("indexnow_key", "")
    if not base or not key:
        print("IndexNow: site_url 또는 indexnow_key 가 비어 있어 건너뜀")
        return 0
    if not (cfg.get("pen_name") and cfg.get("contact_email")):
        print("IndexNow: 필명·문의 이메일이 비어 있어(페이지가 noindex 상태) 건너뜀")
        return 0
    if not os.path.isdir(PUB):
        print("IndexNow: public/ 이 없음 — build.py 먼저")
        return 0
    state = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    now = pages()
    changed = sorted(p for p, h in now.items() if state.get("pages", {}).get(p) != h)
    if not changed:
        print("IndexNow: 바뀐 페이지 없음")
        return 0
    urls = [url_of(base, p) for p in changed]
    print("IndexNow: 바뀐 페이지 %d개" % len(urls))
    for u in urls:
        print("  " + u)
    if dry:
        return 0
    host = re.sub(r"^https?://", "", base).split("/")[0]
    body = {"host": host, "key": key, "keyLocation": "%s/%s.txt" % (base, key), "urlList": urls}
    ok = False
    for ep in ENDPOINTS:
        st = post(ep, body)
        print("  → %s : %s" % (ep, st))
        ok = ok or st in (200, 202)
    if ok:   # 한 곳이라도 받으면 기록 (공용 창구는 참여 엔진끼리 공유)
        state = {"pages": now, "last": {"urls": len(urls)}}
        io.open(STATE, "w", encoding="utf-8").write(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    else:
        print("IndexNow: 모든 창구 실패 — 기록을 남기지 않고 다음 실행 때 다시 보냄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
