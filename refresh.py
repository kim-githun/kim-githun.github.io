#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
부동산 시장 온도계 — 데이터 갱신 스크립트
================================================================
data/market.json 을 갱신합니다. 페이지는 build.py 가 이 파일로 다시 만듭니다.
표준 라이브러리만 씁니다 (pip install 불필요).

사용법
  export ECOS_KEY=...        # https://ecos.bok.or.kr/api/  (무료, 즉시 발급)
  export KOSIS_KEY=...       # https://kosis.kr/openapi/    (무료, 즉시 발급)
  export RONE_KEY=...        # https://www.reb.or.kr/r-one/  로그인 후 발급 (선택)

  python3 refresh.py update              # 전부 갱신 후 data/market.json 저장
  python3 refresh.py update --dry-run    # 가져온 값만 출력, 파일은 안 건드림
  python3 refresh.py update --accept base,mort   # 급변 검사를 이번만 통과시킴(값 확인 후)
  python3 refresh.py discover ecos 121Y006        # 통계표의 항목코드 목록
  python3 refresh.py discover kosis 미분양         # KOSIS 통계표 검색
  python3 refresh.py doctor              # 키/설정/엔드포인트 점검

  네트워크가 막힌 곳에서 (응답을 브라우저로 받아 파일로 넣는 방식)
  python3 refresh.py update --dry-run --cache cache/   # 필요한 URL 이 cache/missing.txt 에 적힘
  python3 refresh.py cache-name "<URL>"                # 그 URL 응답을 저장할 파일 이름
  → 응답 JSON 을 cache/<파일이름> 으로 저장하고 missing.txt 가 빌 때까지 반복한 뒤
  python3 refresh.py update --cache cache/             # 안전장치를 똑같이 거쳐 저장

원칙
  - API 호출이 실패하면 기존 값을 그대로 둡니다. 절대 0이나 빈 값으로 덮지 않습니다.
  - 설정이 비어 있는(null) 지표는 건너뛰고 보고서에 '미설정'으로 찍습니다.
  - 성공/실패는 마지막에 한 표로 출력됩니다. 조용히 실패하지 않습니다.

안전장치 (값이 들어오기 전에 전부 통과해야 함)
  1. 계열 혼입   — 같은 시점 값이 둘 이상 오면 조회 조건이 모자란 것. 거부.
  2. 지문 대조   — 처음 성공한 통계표명·항목명·단위를 기억해 두고, 다음부터 다르면 거부.
                   (통계표 ID를 잘못 바꾸면 에러 없이 엉뚱한 숫자가 오는 사고를 막는다)
  3. 값 범위     — sources.json 의 bounds 를 벗어나면 거부.
  4. 급변        — 직전 값 대비 max_jump 를 넘으면 보류. 사람이 확인 후 --accept 로 통과.
  5. 시점 역행   — 새 기준시점이 기존보다 과거면 거부.
  6. 교차 검증   — 지표끼리의 관계(checks)가 깨지면 이번 갱신 전체를 게시하지 않음(종료코드 2).
"""

import json, os, sys, io, re, ssl, time, datetime, hashlib
import urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "market.json")
HISTORY = os.path.join(HERE, "data", "history")
LOGFILE = os.path.join(HERE, "data", "log.jsonl")
CONF = os.path.join(HERE, "sources.json")

ECOS_KEY = os.environ.get("ECOS_KEY", "").strip()
KOSIS_KEY = os.environ.get("KOSIS_KEY", "").strip()
RONE_KEY = os.environ.get("RONE_KEY", "").strip()

CTX = ssl.create_default_context()
REPORT = []


def log(key, status, detail=""):
    REPORT.append((key, status, detail))


CACHE = None      # --cache DIR : 네트워크 대신 미리 받아 둔 응답 파일을 쓴다 (브라우저로 받은 경우)


def cache_name(url):
    return hashlib.md5(url.encode("utf-8")).hexdigest() + ".json"


def get_json(url, tries=3):
    """GET → JSON. 실패하면 예외."""
    if CACHE is not None:
        p = os.path.join(CACHE, cache_name(url))
        if os.path.exists(p):
            return json.load(io.open(p, encoding="utf-8"))
        with io.open(os.path.join(CACHE, "missing.txt"), "a", encoding="utf-8") as f:
            f.write(url + "\n")
        raise RuntimeError("캐시에 없음 → missing.txt 에 적었음")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "market-thermometer/1.0"})
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                raw = r.read().decode("utf-8", "replace")
            return json.loads(raw)
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("요청 실패: %s (%s)" % (url.split("?")[0], last))


# ─────────────────────────────────────────────────────────────
# ECOS — 한국은행 경제통계시스템
#   항목코드를 외우지 않습니다. 항목 목록을 받아 '이름'으로 찾습니다.
#   (코드 체계가 바뀌어도 이름 매칭이 살아남습니다)
# ─────────────────────────────────────────────────────────────
ECOS = "https://ecos.bok.or.kr/api"


def ecos_items(stat_code):
    url = "%s/StatisticItemList/%s/json/kr/1/500/%s" % (ECOS, ECOS_KEY, stat_code)
    js = get_json(url)
    if "StatisticItemList" not in js:
        raise RuntimeError("ECOS 응답 이상: %s" % json.dumps(js, ensure_ascii=False)[:200])
    return js["StatisticItemList"].get("row", [])


def ecos_find_item(stat_code, name_contains):
    for row in ecos_items(stat_code):
        nm = row.get("ITEM_NAME", "") or ""
        if all(tok in nm for tok in name_contains):
            return row.get("ITEM_CODE"), nm
    raise RuntimeError("항목 못 찾음: %s 안에 %s" % (stat_code, "+".join(name_contains)))


def ecos_series(stat_code, cycle, start, end, item_code, meta=None):
    url = "%s/StatisticSearch/%s/json/kr/1/700/%s/%s/%s/%s/%s" % (
        ECOS, ECOS_KEY, stat_code, cycle, start, end, item_code)
    js = get_json(url)
    if "StatisticSearch" not in js:
        raise RuntimeError("ECOS 조회 실패: %s" % json.dumps(js, ensure_ascii=False)[:200])
    out = []
    for r in js["StatisticSearch"].get("row", []):
        try:
            out.append((r["TIME"], float(r["DATA_VALUE"])))
        except (KeyError, ValueError, TypeError):
            continue
        if meta is not None and not meta:
            meta.update({"tbl": r.get("STAT_NAME", ""), "itm": r.get("ITEM_NAME1", ""),
                         "unit": r.get("UNIT_NAME", ""), "cls": ""})
    return out


def ecos_resolve_item(cfg):
    """item_code 가 있으면 그대로, 없으면 item_name 으로 찾는다.
       코드를 아는 항목은 코드로 고정하는 게 안전하다 — 이름 매칭은
       '주택담보대출' 과 '고정형 주택담보대출' 처럼 부분일치가 겹칠 때 흔들린다."""
    if cfg.get("item_code"):
        return cfg["item_code"], cfg.get("item_label", cfg["item_code"])
    return ecos_find_item(cfg["stat"], cfg["item_name"])


def fetch_ecos_latest(cfg, meta=None):
    """{stat, item_code 또는 item_name:[토큰...], cycle} → (값, 기준시점문자열)"""
    code, nm = ecos_resolve_item(cfg)
    today = datetime.date.today()
    if cfg.get("cycle", "M") == "M":
        start = "%04d%02d" % (today.year - 2, today.month)
        end = "%04d%02d" % (today.year, today.month)
    else:
        start = "%04d%02d%02d" % (today.year - 2, today.month, 1)
        end = today.strftime("%Y%m%d")
    rows = ecos_series(cfg["stat"], cfg.get("cycle", "M"), start, end, code, meta=meta)
    if not rows:
        raise RuntimeError("데이터 0건 (%s / %s)" % (cfg["stat"], nm))
    t, v = rows[-1]
    return v, fmt_period(t), nm


# ─────────────────────────────────────────────────────────────
# KOSIS — 국가통계포털 (국토부 미분양·거래량, 부동산원 통계 미러)
# ─────────────────────────────────────────────────────────────
KOSIS_DATA = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
KOSIS_SEARCH = "https://kosis.kr/openapi/statisticsSearch.do"


def kosis_rows(cfg, periods=14, meta=None):
    q = {
        "method": "getList", "apiKey": KOSIS_KEY, "format": "json", "jsonVD": "Y",
        "orgId": cfg["orgId"], "tblId": cfg["tblId"], "itmId": cfg["itmId"],
        "objL1": cfg["objL1"], "prdSe": cfg.get("prdSe", "M"), "newEstPrdCnt": str(periods),
    }
    for k in ("objL2", "objL3", "objL4", "objL5", "objL6", "objL7", "objL8"):
        if cfg.get(k):
            q[k] = cfg[k]
    js = get_json(KOSIS_DATA + "?" + urllib.parse.urlencode(q))
    if isinstance(js, dict):                       # 에러는 dict로 옴 (HTTP 200 이어도)
        raise RuntimeError("KOSIS 오류: %s" % json.dumps(js, ensure_ascii=False)[:200])
    rows, seen = [], {}
    for r in js:
        try:
            t = r["PRD_DE"]
            v = float(str(r["DT"]).replace(",", ""))
        except (KeyError, ValueError, TypeError):
            continue
        if t in seen and seen[t] != v:
            raise RuntimeError("계열 혼입: %s 시점 값이 둘 이상 (%s, %s) — 조회 조건(objL)이 모자랍니다"
                               % (t, seen[t], v))
        seen[t] = v
        rows.append((t, v))
        if meta is not None and not meta:
            meta.update({"tbl": r.get("TBL_NM", ""), "itm": r.get("ITM_NM", ""),
                         "unit": r.get("UNIT_NM", ""),
                         "cls": " / ".join(str(r.get("C%d_NM" % i, "")) for i in range(1, 9)
                                           if r.get("C%d_NM" % i))})
    rows = sorted(set(rows))
    return rows


def fetch_kosis_latest(cfg, meta=None):
    rows = kosis_rows(cfg, meta=meta)
    if not rows:
        raise RuntimeError("데이터 0건 (%s/%s)" % (cfg["orgId"], cfg["tblId"]))
    t, v = rows[-1]
    delta = None
    if len(rows) >= 2 and rows[-2][1]:
        pct = (v - rows[-2][1]) / rows[-2][1] * 100.0
        delta = "전월 대비 %+.1f%%" % pct if cfg.get("prdSe", "M") == "M" else "전분기 대비 %+.1f%%" % pct
    return v, fmt_period(t), delta


# ─────────────────────────────────────────────────────────────
# R-ONE — 한국부동산원 (주간 아파트 가격동향 등)
# ─────────────────────────────────────────────────────────────
RONE = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"


def fetch_rone_latest(cfg):
    q = {"KEY": RONE_KEY, "Type": "json", "STATBL_ID": cfg["statbl"],
         "DTACYCLE_CD": cfg.get("cycle", "WK"), "pIndex": "1", "pSize": "60"}
    for k, p in (("cls", "CLS_ID"), ("itm", "ITM_ID")):
        if cfg.get(k):
            q[p] = cfg[k]
    js = get_json(RONE + "?" + urllib.parse.urlencode(q))
    rows = _dig_rows(js)
    if not rows:
        raise RuntimeError("R-ONE 응답에 row 없음: %s" % json.dumps(js, ensure_ascii=False)[:200])
    vals = []
    for r in rows:
        t = r.get("WRTTIME_IDTFR_ID") or r.get("WRTTIME_DESC") or ""
        try:
            vals.append((str(t), float(str(r.get("DTA_VAL")).replace(",", ""))))
        except (ValueError, TypeError):
            continue
    vals.sort()
    if not vals:
        raise RuntimeError("R-ONE 값 파싱 실패")
    t, v = vals[-1]
    return v, fmt_period(t), None


def _dig_rows(js):
    """응답 형태가 바뀌어도 row 리스트를 찾아냄."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "row" in o and isinstance(o["row"], list):
                found.extend(o["row"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(js)
    return found


# ─────────────────────────────────────────────────────────────
# 파생: 전세가율 = 평균전세가격 / 평균매매가격 × 100
#   부동산원은 '전세가율'을 단일 통계표로 주지 않습니다.
#   평균매매가격·평균전세가격 두 표를 받아서 직접 계산합니다.
# ─────────────────────────────────────────────────────────────
def fetch_derived_jeonse(cfg):
    sale = kosis_rows(cfg["sale"])
    rent = kosis_rows(cfg["rent"])
    if not sale or not rent:
        raise RuntimeError("평균가격 데이터 부족")
    smap, rmap = dict(sale), dict(rent)
    common = sorted(set(smap) & set(rmap))
    if not common:
        raise RuntimeError("매매/전세 기준시점이 겹치지 않음")
    t = common[-1]
    if not smap[t]:
        raise RuntimeError("평균매매가격이 0")
    ratio = rmap[t] / smap[t] * 100.0
    return ratio, fmt_period(t), "평균전세 %s / 평균매매 %s 기준" % (_kmoney(rmap[t]), _kmoney(smap[t]))


def _kmoney(v):
    return "%.1f억" % (v / 10000.0) if v > 10000 else "%.0f만" % v


# ─────────────────────────────────────────────────────────────
# 계산식 지표 (source: "formula")
#   API로 받은 값들을 식에 넣어 새 지표를 만든다.
#   예) 전세가율  = rent / sale * 100
#       부담지수  = pmt(...) * 12 / income / 0.25 * 100
#   식은 제한된 문법만 허용한다(eval 안 씀). 이름은 inputs 의 키.
# ─────────────────────────────────────────────────────────────
import ast
import math


def _pmt(monthly_rate, months, principal):
    """원리금균등 월 상환액."""
    if months <= 0:
        raise ValueError("months must be > 0")
    if abs(monthly_rate) < 1e-12:
        return principal / months
    f = (1 + monthly_rate) ** months
    return principal * monthly_rate * f / (f - 1)


SAFE_FUNCS = {"min": min, "max": max, "abs": abs, "round": round,
              "pmt": _pmt, "sqrt": math.sqrt, "log": math.log}
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)


def safe_eval(expr, env):
    """산술식만 계산한다. 함수는 SAFE_FUNCS 만, 이름은 env 안의 것만."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (int, float)):
                return n.value
            raise ValueError("숫자만 쓸 수 있습니다: %r" % (n.value,))
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            raise ValueError("알 수 없는 이름: %s (inputs 에 없습니다)" % n.id)
        if isinstance(n, ast.BinOp) and isinstance(n.op, _ALLOWED_BINOPS):
            return _apply(n.op, ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id not in SAFE_FUNCS:
                raise ValueError("허용되지 않은 함수: %s" % n.func.id)
            if n.keywords:
                raise ValueError("키워드 인자는 쓸 수 없습니다")
            return SAFE_FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("허용되지 않은 식: %s" % type(n).__name__)

    def _apply(op, a, b):
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            if b == 0:
                raise ValueError("0으로 나눌 수 없습니다")
            return a / b
        if isinstance(op, ast.Pow):
            return a ** b
        return a % b

    return ev(ast.parse(expr, mode="eval"))


def fetch_one(cfg, meta=None):
    """source 별 단일 지표 조회 → (값, 기준시점, 증감문구)"""
    src = cfg.get("source")
    if src == "ecos":
        v, asof, _ = fetch_ecos_latest(cfg, meta=meta)
        return v, asof, None
    if src == "kosis":
        return fetch_kosis_latest(cfg, meta=meta)
    if src == "rone":
        return fetch_rone_latest(cfg)
    raise RuntimeError("계산식 입력에 쓸 수 없는 source: %r" % src)


def fetch_formula(cfg, meta=None, env_out=None):
    """inputs 를 각각 받아 formula 에 넣는다. 상수는 consts 로 넘긴다."""
    env = dict(cfg.get("consts") or {})
    asofs = {}
    for name, sub in (cfg.get("inputs") or {}).items():
        m = {}
        v, asof, _ = fetch_one(sub, meta=m)
        env[name] = v
        asofs[name] = asof
        if meta is not None:
            meta[name] = m
    if not env:
        raise RuntimeError("inputs 가 비어 있습니다")
    if len(set(asofs.values())) > 1:
        raise RuntimeError("계산식 입력의 기준시점이 서로 다름: %s" % asofs)
    value = safe_eval(cfg["formula"], env)
    if env_out is not None:
        env_out.update(env)
    key = cfg.get("asof_from")
    asof = asofs.get(key) or (sorted(asofs.values())[0] if asofs else "")
    parts = ", ".join("%s=%s" % (k, _fmtnum(env[k])) for k in sorted(env))
    return value, asof, "자체 산출 · " + parts


def _fmtnum(v):
    return ("%.4g" % v) if isinstance(v, float) else str(v)


# ─────────────────────────────────────────────────────────────
# 기준금리 시계열 (차트용) — 변동 시점만 압축
# ─────────────────────────────────────────────────────────────
def fetch_rate_history(cfg):
    code, nm = ecos_resolve_item(cfg)
    rows = ecos_series(cfg["stat"], "M", "200801", datetime.date.today().strftime("%Y%m"), code)
    if not rows:
        raise RuntimeError("기준금리 시계열 0건")
    out, prev = [], None
    for t, v in rows:
        y, m = int(t[:4]), int(t[4:6])
        dec = round(y + (m - 1) / 12.0, 2)
        if prev is None or abs(v - prev) > 1e-9:
            out.append([dec, round(v, 2)])
            prev = v
    last = rows[-1]
    dec_last = round(int(last[0][:4]) + (int(last[0][4:6]) - 1) / 12.0, 2)
    if out[-1][0] != dec_last:
        out.append([dec_last, round(last[1], 2)])
    return out


# ─────────────────────────────────────────────────────────────
def fmt_period(t):
    t = str(t).strip()
    if re.fullmatch(r"\d{6}", t):
        return "%s년 %d월" % (t[:4], int(t[4:6]))
    if re.fullmatch(r"\d{4}Q?[1-4]", t.upper().replace("Q", "")) and len(t) == 5:
        return "%s년 %s분기" % (t[:4], t[-1])
    if re.fullmatch(r"\d{8}", t):
        return "%s년 %d월 %d일" % (t[:4], int(t[4:6]), int(t[6:8]))
    if re.fullmatch(r"\d{4}", t):
        return "%s년" % t
    return t


def load_data():
    return json.load(io.open(DATA, encoding="utf-8"))


def save_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)                          # 반쯤 쓴 파일이 남지 않게


# ─────────────────────────────────────────────────────────────
# 안전장치
# ─────────────────────────────────────────────────────────────
def parse_asof(t):
    """'2026년 8월 31일' / '2026년 7월' / '2026년 1분기' → date (그 기간의 마지막 날)"""
    if not t:
        return None
    y = re.search(r"(\d{4})\s*년", t)
    if not y:
        return None
    y = int(y.group(1))
    q = re.search(r"([1-4])\s*분기", t)
    if q:
        m = int(q.group(1)) * 3
        return _month_end(y, m)
    mo = re.search(r"(\d{1,2})\s*월", t)
    if mo:
        d = re.search(r"(\d{1,2})\s*일", t)
        if d:
            return datetime.date(y, int(mo.group(1)), int(d.group(1)))
        return _month_end(y, int(mo.group(1)))
    return datetime.date(y, 12, 31)


def _month_end(y, m):
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    return nxt - datetime.timedelta(days=1)


def check_value(key, cfg, new, old, new_asof, old_asof, fp_old, fp_new, accept=()):
    """(판정, 사유). 판정: 'ok' | 'reject' | 'hold'"""
    # 지문
    if fp_old and fp_new:
        for f in ("tbl", "itm", "unit", "cls"):
            a, b = fp_old.get(f, ""), fp_new.get(f, "")
            if a and b and a != b:
                return "reject", "지문 불일치(%s): 이전 '%s' → 이번 '%s'" % (f, a, b)
    # 범위
    b = cfg.get("bounds")
    if b and not (b[0] <= new <= b[1]):
        return "reject", "값 %s 이 허용 범위 %s~%s 밖" % (new, b[0], b[1])
    # 시점 역행
    na, oa = parse_asof(new_asof), parse_asof(old_asof)
    if na and oa and na < oa:
        return "reject", "기준시점 역행: %s → %s" % (old_asof, new_asof)
    # 급변
    j = cfg.get("max_jump")
    if j and old is not None and key not in accept:
        if "abs" in j and abs(new - old) > j["abs"]:
            return "hold", "직전 %s → %s, 변화 %.3g 가 한도 %s 초과" % (old, new, abs(new - old), j["abs"])
        if "pct" in j and old and abs(new - old) / abs(old) * 100 > j["pct"]:
            return "hold", "직전 %s → %s, 변화 %.1f%% 가 한도 %s%% 초과" % (
                old, new, abs(new - old) / abs(old) * 100, j["pct"])
    return "ok", ""


def run_checks(checks, env):
    """교차 검증. 실패 목록을 돌려준다."""
    bad = []
    for c in checks or []:
        try:
            v = safe_eval(c["expr"], env)
        except Exception as e:                      # noqa: BLE001
            bad.append((c["expr"], "계산 불가: %s" % e, c.get("why", "")))
            continue
        lo, hi = c.get("min"), c.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            bad.append((c["expr"], "%.4g (허용 %s~%s)" % (v, lo, hi), c.get("why", "")))
    return bad


# ─────────────────────────────────────────────────────────────
def cmd_update(dry=False, accept=()):
    conf = json.load(io.open(CONF, encoding="utf-8"))
    data = load_data()
    before = json.loads(json.dumps(data))           # 원본 사본 (previous 용)
    by_key = {d["key"]: d for d in data["indicators"]}
    fps = data.setdefault("fingerprints", {})
    env = {}                                          # 교차 검증용 이름공간
    changed = False

    for key, cfg in conf["indicators"].items():
        ind = by_key.get(key)
        if ind is None:
            log(key, "건너뜀", "데이터에 해당 지표 없음")
            continue
        src = cfg.get("source")
        if src == "manual":
            log(key, "수동", "API 없음 — 기존 값 유지 (신선도 경고는 페이지가 표시)")
            continue
        if not src or cfg.get("todo"):
            log(key, "미설정", cfg.get("todo") or "source 없음")
            continue
        keys = {"ecos": ECOS_KEY, "kosis": KOSIS_KEY, "rone": RONE_KEY, "derived": KOSIS_KEY}
        if src == "formula":
            subs = {c.get("source") for c in (cfg.get("inputs") or {}).values()}
            missing = [t.upper() for t in subs if not keys.get(t, "")]
            if missing:
                log(key, "키없음", "계산식 입력에 %s_KEY 필요" % "/".join(missing))
                continue
        elif not keys.get(src, ""):
            log(key, "키없음", "%s_KEY 환경변수 미설정" % src.upper())
            continue
        meta, fenv = {}, {}
        try:
            if src == "ecos":
                v, asof, _ = fetch_ecos_latest(cfg, meta=meta)
                delta = None
            elif src == "kosis":
                v, asof, delta = fetch_kosis_latest(cfg, meta=meta)
            elif src == "rone":
                v, asof, delta = fetch_rone_latest(cfg)
            elif src == "formula":
                v, asof, delta = fetch_formula(cfg, meta=meta, env_out=fenv)
            elif src == "derived":
                v, asof, delta = fetch_derived_jeonse(cfg)
            else:
                log(key, "미지원", src)
                continue
        except Exception as e:                      # noqa: BLE001
            log(key, "실패", str(e)[:110])
            continue

        v = round(v, ind.get("dec", 1))
        # 계산식은 입력별 지문을 따로 대조한다
        if src == "formula":
            verdict, why = "ok", ""
            for name, m in meta.items():
                verdict, why = check_value(key, {}, v, None, asof, None,
                                           fps.get("%s.%s" % (key, name)), m)
                if verdict != "ok":
                    why = "[%s] %s" % (name, why)
                    break
            if verdict == "ok":
                verdict, why = check_value(key, cfg, v, ind["value"], asof, ind.get("asof"),
                                           None, None, accept)
        else:
            verdict, why = check_value(key, cfg, v, ind["value"], asof, ind.get("asof"),
                                       fps.get(key), meta, accept)
        if verdict == "reject":
            log(key, "거부", why)
            continue
        if verdict == "hold":
            log(key, "보류", why + " — 확인 후 --accept %s" % key)
            continue

        old = ind["value"]
        if old != v or ind.get("asof") != asof:
            changed = True
        ind["value"] = v
        ind["asof"] = asof
        if delta and src != "formula":
            ind["delta"] = delta
        # 지문은 처음 성공했을 때만 기록 (이후에는 대조만)
        if src == "formula":
            for name, m in meta.items():
                fps.setdefault("%s.%s" % (key, name), m)
            for name, val in fenv.items():
                env["%s__%s" % (key, name)] = val
        elif meta:
            fps.setdefault(key, meta)
        log(key, "OK", "%s → %s (%s)" % (old, v, asof))

    # 교차 검증은 이번에 갱신 안 된 지표도 포함한 '게시될 상태' 전체로 한다
    for d in data["indicators"]:
        env[d["key"]] = d["value"]
    # 계산식 입력이 이번에 안 들어왔으면 기존 값으로 역산 가능한 것만 채운다
    for name, expr in (conf.get("check_env") or {}).items():
        if name not in env:
            try:
                env[name] = safe_eval(expr, env)
            except Exception:                        # noqa: BLE001
                pass
    bad = run_checks(conf.get("checks"), env)
    for expr, res, why in bad:
        log("교차검증", "실패", "%s = %s · %s" % (expr, res, why))

    # 같은 기준시점이어야 하는 짝
    for pair in conf.get("same_asof") or []:
        asofs = {k: by_key[k].get("asof") for k in pair if k in by_key}
        if len(set(asofs.values())) > 1:
            log("시점짝", "경고", " / ".join("%s=%s" % kv for kv in asofs.items()))

    # 기준금리 차트 시계열
    rc = conf.get("rate_history")
    if rc and ECOS_KEY:
        try:
            rates = fetch_rate_history(rc)
            if len(rates) < 10 or not (0 <= rates[-1][1] <= 10):
                raise RuntimeError("시계열이 비정상적으로 짧거나 값 이상 (%d개)" % len(rates))
            if rates != data.get("rates"):
                changed = True
            data["rates"] = rates
            log("rates", "OK", "%d개 변동점" % len(rates))
        except Exception as e:                      # noqa: BLE001
            log("rates", "실패", str(e)[:110])
    else:
        log("rates", "미설정" if not rc else "키없음", "")

    ok = sum(1 for _, s, _ in REPORT if s == "OK")
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    publish = ok > 0 and not bad
    verdict = "게시" if publish else ("보류: 교차 검증 실패" if bad else "보류: 성공 항목 없음")

    print_report()
    print("\n판정: %s" % verdict)
    if dry:
        print("[dry-run] 파일은 수정하지 않았습니다.")
        return 0

    run = {"at": now.strftime("%Y-%m-%d %H:%M KST"), "verdict": verdict,
           "rows": [{"key": k, "status": s, "detail": d} for k, s, d in REPORT]}
    # 로그는 게시 여부와 관계없이 남긴다
    with io.open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")

    if not publish:
        print("게시하지 않았습니다. data/market.json 은 그대로입니다.")
        return 2 if bad else 1

    if changed:
        data["previous"] = {"updated": before.get("updated"),
                            "values": {d["key"]: d["value"] for d in before["indicators"]},
                            "asof": {d["key"]: d.get("asof") for d in before["indicators"]}}
    data["updated"] = now.date().isoformat()
    data["lastrun"] = run
    save_json(DATA, data)
    save_json(os.path.join(HISTORY, "%s.json" % data["updated"]), data)
    print("저장 완료 → %s" % DATA)
    return 0


def print_report():
    print("\n%-8s %-6s %s" % ("지표", "상태", "내용"))
    print("-" * 72)
    for k, s, d in REPORT:
        print("%-8s %-6s %s" % (k, s, d))


def cmd_discover(kind, term):
    if kind == "ecos":
        if not ECOS_KEY:
            sys.exit("ECOS_KEY 가 필요합니다.")
        print("통계표 %s 의 항목 목록:" % term)
        for r in ecos_items(term):
            print("  %-14s %-40s %s" % (r.get("ITEM_CODE"), r.get("ITEM_NAME"), r.get("CYCLE", "")))
    elif kind == "kosis":
        if not KOSIS_KEY:
            sys.exit("KOSIS_KEY 가 필요합니다.")
        q = {"method": "getList", "apiKey": KOSIS_KEY, "format": "json", "jsonVD": "Y",
             "searchNm": term}
        js = get_json(KOSIS_SEARCH + "?" + urllib.parse.urlencode(q))
        items = js if isinstance(js, list) else [js]
        for r in items[:40]:
            print("  orgId=%-5s tblId=%-18s %s" % (r.get("ORG_ID"), r.get("TBL_ID"), r.get("TBL_NM")))
        print("\n※ tblId 를 sources.json 에 넣고, itmId/objL1 은 KOSIS 통계표 화면의")
        print("   '오픈API' 버튼에서 생성되는 URL에서 그대로 복사하세요.")
    else:
        sys.exit("discover ecos|kosis <검색어>")
    return 0


def cmd_doctor():
    print("키 설정")
    for n, v in (("ECOS_KEY", ECOS_KEY), ("KOSIS_KEY", KOSIS_KEY), ("RONE_KEY", RONE_KEY)):
        print("  %-10s %s" % (n, "설정됨" if v else "없음"))
    conf = json.load(io.open(CONF, encoding="utf-8"))
    print("\n지표 설정")
    for k, c in conf["indicators"].items():
        state = c.get("todo") or ("%s" % c.get("source"))
        print("  %-8s %s" % (k, state))
    print("\n엔드포인트 연결 확인")
    for name, url in (("ECOS", "https://ecos.bok.or.kr/"), ("KOSIS", "https://kosis.kr/"),
                      ("R-ONE", "https://www.reb.or.kr/r-one/")):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mt/1.0"})
            urllib.request.urlopen(req, timeout=10, context=CTX)
            print("  %-6s 연결 OK" % name)
        except Exception as e:                      # noqa: BLE001
            print("  %-6s 연결 실패 — %s" % (name, str(e)[:60]))
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    global CACHE
    if "--cache" in args:
        i = args.index("--cache")
        CACHE = args[i + 1]
        if not os.path.isdir(CACHE):
            os.makedirs(CACHE)
        mp = os.path.join(CACHE, "missing.txt")
        if os.path.exists(mp):
            os.remove(mp)
    if cmd == "cache-name" and len(args) >= 2:
        print(cache_name(args[1]))
        return 0
    if cmd == "update":
        acc = ()
        if "--accept" in args:
            i = args.index("--accept")
            if i + 1 < len(args):
                acc = tuple(x.strip() for x in args[i + 1].split(",") if x.strip())
        return cmd_update(dry="--dry-run" in args, accept=acc)
    if cmd == "discover" and len(args) >= 3:
        return cmd_discover(args[1], args[2])
    if cmd == "doctor":
        return cmd_doctor()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
