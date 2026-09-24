#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검색어 지도(content/keywords.py)를 네이버 키워드도구 실측값으로 판정한다.

  python3 tools/keywords.py batches          # 키워드도구에 넣을 목록 (한 줄 5개)
  python3 tools/keywords.py report 폴더      # 폴더 안의 키워드도구 다운로드 파일(.csv/.xlsx/.tsv)을 모두 읽어 판정
                                             # → reports/keywords-날짜.md, reports/keywords-날짜.csv

판정 기준 (근거 데이터 없이 정한 첫 문턱 — 실측이 쌓이면 여기 숫자만 고친다)
  MIN_KEEP   월간 조회수(PC+모바일) 이 이상이고 경쟁정도가 낮음·중간이면 '유지'
  MIN_ALIVE  이 미만이면 '수요 없음'
  SERIES_MIN 통계 해설 전체 주 검색어 합계가 이 미만이면 해설 확장 중단 신호
"""
import csv
import datetime
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from content.keywords import KEYWORDS, PROBES  # noqa: E402

MIN_KEEP = 1000
MIN_ALIVE = 100
SERIES_MIN = 3000
OK_COMP = ("낮음", "중간")
TOPIC = ("부동산", "아파트", "집값", "전세", "월세", "매매", "종부세", "재산세", "취득세", "양도세", "대출", "디딤돌",
         "보금자리", "청약", "금리", "미분양", "공시가", "LTV", "DSR", "대항력", "확정일자", "보증", "임대차", "주택")


def norm(s):
    return re.sub(r"\s+", "", s or "").upper()


def num(v):
    """'< 10' → 5 (10 미만), '1,234' → 1234"""
    v = str(v or "").strip()
    if not v:
        return 0
    if "<" in v:
        return 5
    try:
        return int(float(v.replace(",", "")))
    except ValueError:
        return 0


# ── 읽기 ────────────────────────────────────────────────
def read_rows(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        return [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)]
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("인코딩을 읽을 수 없음: " + path)
    delim = "\t" if text.count("\t") > text.count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delim))


def find_cols(header):
    h = [re.sub(r"\s", "", x) for x in header]

    def pick(*need, avoid=()):
        for i, x in enumerate(h):
            if all(n in x for n in need) and not any(a in x for a in avoid):
                return i
        return None
    return {"kw": pick("키워드"), "pc": pick("검색", "PC"), "mo": pick("검색", "모바일"), "comp": pick("경쟁")}


def load(folder):
    data, files = {}, []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith((".csv", ".tsv", ".txt", ".xlsx", ".xlsm")):
            continue
        rows = read_rows(os.path.join(folder, name))
        hi = next((i for i, r in enumerate(rows[:10]) if any("키워드" in (c or "") for c in r)), None)
        if hi is None:
            print("  건너뜀(머리글 없음): " + name)
            continue
        col = find_cols(rows[hi])
        if None in (col["kw"], col["pc"], col["mo"]):
            print("  건너뜀(열을 못 찾음 %s): %s" % (col, name))
            continue
        n = 0
        for r in rows[hi + 1:]:
            if len(r) <= max(v for v in col.values() if v is not None) or not r[col["kw"]].strip():
                continue
            k = r[col["kw"]].strip()
            data[norm(k)] = {"kw": k, "pc": num(r[col["pc"]]), "mo": num(r[col["mo"]]),
                             "comp": (r[col["comp"]].strip() if col["comp"] is not None else "")}
            n += 1
        files.append((name, n))
    return data, files


# ── 판정 ────────────────────────────────────────────────
def vol(d):
    return d["pc"] + d["mo"] if d else None


def show(v):
    return "—" if v is None else format(v, ",")


def judge(path, kw, data):
    p = data.get(norm(kw["primary"]))
    alts = [(a, data.get(norm(a))) for a in kw.get("also", [])]
    best = max((x for x in alts if x[1]), key=lambda x: vol(x[1]), default=None)
    pv = vol(p)
    if p is None:
        hint = (" (보조 '%s'는 월 %s)" % (best[0], show(vol(best[1])))) if best else ""
        return "조회 안 됨", "주 검색어를 키워드도구에 넣어 다시 조회%s" % hint, pv, best
    if p is not None and pv >= MIN_KEEP and p["comp"] in OK_COMP:
        return "유지", "", pv, best
    if best and vol(best[1]) >= MIN_KEEP and best[1]["comp"] in OK_COMP and vol(best[1]) > (pv or 0):
        return "주 검색어 교체", "'%s'(%s, 경쟁 %s)로 바꾸고 제목 수정" % (best[0], show(vol(best[1])), best[1]["comp"]), pv, best
    if p is not None and pv >= MIN_KEEP:
        return "경쟁 높음", "광고 경쟁이 높은 검색어(대출·금융 상업 검색일 가능성). 질문형 롱테일로 좁힐 것", pv, best
    if (pv or 0) >= MIN_ALIVE:
        return "약함", "월 %s — 유지하되 이 주제의 새 글 우선순위 낮춤" % show(pv), pv, best
    live = [x for x in alts if x[1] and vol(x[1]) >= MIN_ALIVE]
    if live:
        a, d = max(live, key=lambda x: vol(x[1]))
        return "주 검색어 수요 없음", "보조 '%s'(%s, 경쟁 %s) 쪽으로 제목 조정 검토 — 경쟁 높으면 그대로 둠" % (a, show(vol(d)), d["comp"]), pv, best
    return "수요 없음", "이 주제로 새 글 확장 중단. 기존 글은 계기판 보조로만", pv, best


def report(folder):
    data, files = load(folder)
    if not data:
        raise SystemExit("읽은 키워드가 없습니다. 키워드도구 '다운로드' 파일을 폴더에 넣었는지 확인하세요.")
    mapped = set()
    for kw in KEYWORDS.values():
        mapped.add(norm(kw["primary"]))
        mapped.update(norm(a) for a in kw.get("also", []))
    lines = ["# 검색어 지도 판정 (%s)" % datetime.date.today().isoformat(), "",
             "읽은 파일: " + ", ".join("%s(%d행)" % f for f in files), "",
             "기준: 유지 = 월 %s 이상 + 경쟁 낮음·중간 / 수요 없음 = 월 %s 미만" % (format(MIN_KEEP, ","), MIN_ALIVE), "",
             "주의: '경쟁정도'는 네이버 **광고주** 경쟁이지 검색 순위 경쟁이 아니다. 높음 = 돈이 되는 상업 검색어라는 뜻에 가깝다.", "",
             "| 페이지 | 주 검색어 | 월 조회 | 경쟁 | 판정 | 할 일 |", "|---|---|---|---|---|---|"]
    rows_csv = [["page", "primary", "monthly", "comp", "verdict", "action", "best_also", "best_also_monthly"]]
    series_total = 0
    for path, kw in KEYWORDS.items():
        verdict, action, pv, best = judge(path, kw, data)
        p = data.get(norm(kw["primary"]))
        if path.startswith("articles/"):
            series_total += pv or 0
        lines.append("| %s | %s | %s | %s | **%s** | %s |" % (path, kw["primary"], show(pv), p["comp"] if p else "—", verdict, action))
        rows_csv.append([path, kw["primary"], pv if pv is not None else "", p["comp"] if p else "", verdict, action,
                         best[0] if best else "", vol(best[1]) if best else ""])
    lines += ["", "통계 해설 주 검색어 합계: 월 %s → %s" % (
        show(series_total), "해설 확장 계속" if series_total >= SERIES_MIN else "**해설 확장 중단 신호** (기준 %s)" % format(SERIES_MIN, ","))]

    probes = [k for ks in PROBES.values() for k in ks]
    lines += ["", "## 아직 페이지가 없는 검색어 (새 글 후보)", "", "| 검색어 | 월 조회 | 경쟁 |", "|---|---|---|"]
    for k in probes:
        d = data.get(norm(k))
        lines.append("| %s | %s | %s |" % (k, show(vol(d)), d["comp"] if d else "—"))

    extra = [d for n, d in data.items() if n not in mapped and n not in {norm(k) for k in probes}
             and vol(d) >= MIN_KEEP and d["comp"] in OK_COMP and any(t.upper() in n for t in TOPIC)]
    extra.sort(key=vol, reverse=True)
    lines += ["", "## 연관 검색어 중 월 %s 이상 · 경쟁 낮음/중간 (상위 30)" % format(MIN_KEEP, ","), "",
              "기존 페이지의 보조 검색어로 넣을지, 새 글로 쓸지 판단용. 같은 뜻이면 새 페이지를 만들지 말고 기존 페이지에 붙인다.", "",
              "| 검색어 | 월 조회 | 경쟁 |", "|---|---|---|"]
    lines += ["| %s | %s | %s |" % (d["kw"], show(vol(d)), d["comp"]) for d in extra[:30]] or ["| (없음) | | |"]

    out = os.path.join(HERE, "reports")
    os.makedirs(out, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    md_path = os.path.join(out, "keywords-%s.md" % stamp)
    io.open(md_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    with io.open(os.path.join(out, "keywords-%s.csv" % stamp), "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows_csv)
    print("\n".join(lines))
    print("\n저장: " + md_path)


def batches():
    """주 검색어 + 새 글 후보만. 보조 검색어는 키워드도구의 '연관키워드'로 대부분 함께 나온다."""
    words = [kw["primary"] for kw in KEYWORDS.values()] + [k for ks in PROBES.values() for k in ks]
    seen, uniq = set(), []
    for w in words:
        if norm(w) not in seen:
            seen.add(norm(w))
            uniq.append(w.replace(" ", ""))
    for i in range(0, len(uniq), 5):
        print("[%d] %s" % (i // 5 + 1, ", ".join(uniq[i:i + 5])))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "batches":
        batches()
    elif len(sys.argv) >= 3 and sys.argv[1] == "report":
        report(sys.argv[2])
    else:
        print(__doc__)
