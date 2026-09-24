# -*- coding: utf-8 -*-
"""가짜 API 응답으로 refresh.py 를 검증한다. 네트워크 없이 돈다.
   실행: python3 test_refresh.py     (성공하면 'ALL PASS')

   앞부분은 파싱·계산식 단위 검증, 뒷부분은 안전장치 시나리오 검증이다.
   안전장치 시나리오는 실제 sources.json 배선 그대로 돌린다."""
import io, json, os, shutil, sys, tempfile, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refresh  # noqa: E402

refresh.ECOS_KEY = "TESTKEY"
refresh.KOSIS_KEY = "TESTKEY"
refresh.RONE_KEY = "TESTKEY"

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  — " + str(detail)) if detail else ""))
    if not cond:
        fails.append(name)


# ═════════════════════════════════════════════════════════════
# 1) 단위 검증 — 단순한 가짜 응답
# ═════════════════════════════════════════════════════════════
ECOS_ITEMS = {"StatisticItemList": {"row": [
    {"ITEM_CODE": "0101000", "ITEM_NAME": "한국은행 기준금리", "CYCLE": "M"},
    {"ITEM_CODE": "BECBLA03", "ITEM_NAME": "주택담보대출", "CYCLE": "M"},
]}}


def unit_get_json(url, tries=3):
    if "/StatisticItemList/" in url:
        return ECOS_ITEMS
    if "/StatisticSearch/" in url:
        if "/722Y001/" in url:
            rows = [{"TIME": "202601", "DATA_VALUE": "2.5"},
                    {"TIME": "202602", "DATA_VALUE": "2.5"},
                    {"TIME": "202603", "DATA_VALUE": "2.25"}]
        else:
            rows = [{"TIME": "202606", "DATA_VALUE": "4.30"},
                    {"TIME": "202607", "DATA_VALUE": "4.18"}]
        return {"StatisticSearch": {"row": rows}}
    if "statisticsParameterData" in url:
        if "tblId=T_SALE" in url:
            return [{"PRD_DE": "202606", "DT": "120,000"}, {"PRD_DE": "202607", "DT": "125,000"}]
        if "tblId=T_RENT" in url:
            return [{"PRD_DE": "202606", "DT": "62,000"}, {"PRD_DE": "202607", "DT": "65,000"}]
        if "tblId=T_MIXED" in url:
            return [{"PRD_DE": "202607", "DT": "100"}, {"PRD_DE": "202607", "DT": "250"}]
        return [{"PRD_DE": "202606", "DT": "67,500"}, {"PRD_DE": "202607", "DT": "68,217"}]
    if "SttsApiTblData" in url:
        return {"SttsApiTblData": [{"head": []}, {"row": [
            {"WRTTIME_IDTFR_ID": "20260831", "DTA_VAL": "0.15"},
            {"WRTTIME_IDTFR_ID": "20260907", "DTA_VAL": "0.20"}]}]}
    raise AssertionError("예상 못 한 URL: " + url)


refresh.get_json = unit_get_json

print("단위 검증")
v, asof, nm = refresh.fetch_ecos_latest({"stat": "722Y001", "item_name": ["한국은행 기준금리"], "cycle": "M"})
check("ECOS 최신값", v == 2.25 and asof == "2026년 3월", (v, asof))
v, asof, nm = refresh.fetch_ecos_latest({"stat": "121Y006", "item_code": "BECBLA0302",
                                         "item_label": "주택담보대출 통합", "cycle": "M"})
check("ECOS 항목코드 직접지정", v == 4.18 and nm == "주택담보대출 통합", (v, nm))
v, asof, delta = refresh.fetch_kosis_latest(
    {"orgId": "116", "tblId": "T_UNSOLD", "itmId": "T1", "objL1": "ALL", "prdSe": "M"})
check("KOSIS 최신값+증감", v == 68217.0 and delta == "전월 대비 +1.1%", (v, delta))
try:
    refresh.fetch_kosis_latest({"orgId": "1", "tblId": "T_MIXED", "itmId": "T1", "objL1": "A", "prdSe": "M"})
    check("계열 혼입 차단", False, "통과해버림")
except RuntimeError as e:
    check("계열 혼입 차단", "계열 혼입" in str(e), e)
v, asof, delta = refresh.fetch_rone_latest({"statbl": "X", "cycle": "WK"})
check("R-ONE 최신값", v == 0.20 and asof == "2026년 9월 7일", (v, asof))
hist = refresh.fetch_rate_history({"stat": "722Y001", "item_name": ["한국은행 기준금리"]})
check("금리 시계열 압축", hist == [[2026.0, 2.5], [2026.17, 2.25]], hist)

print("\n계산식 엔진")
check("산술", refresh.safe_eval("a / b * 100", {"a": 65000.0, "b": 125000.0}) == 52.0)
check("pmt", abs(refresh.safe_eval("pmt(r/12, n, p)", {"r": 0.0448, "n": 240, "p": 3e8}) - 1876000) < 20000)
for expr, why in [("__import__('os').system('ls')", "임포트"), ("open('x')", "허용 안 된 함수"),
                  ("c + 1", "정의 안 된 이름"), ("1/0", "0 나눗셈"), ("a.real", "속성 접근")]:
    try:
        refresh.safe_eval(expr, {"a": 1.0})
        check("차단: " + why, False, "통과해버림")
    except Exception:                                  # noqa: BLE001
        check("차단: " + why, True)

print("\n기준시점 해석")
import datetime  # noqa: E402
check("주간", refresh.parse_asof("2026년 8월 31일") == datetime.date(2026, 8, 31))
check("월간=월말", refresh.parse_asof("2026년 2월") == datetime.date(2026, 2, 28))
check("분기=분기말", refresh.parse_asof("2026년 1분기") == datetime.date(2026, 3, 31))

# ═════════════════════════════════════════════════════════════
# 2) 안전장치 시나리오 — 실제 sources.json 배선
# ═════════════════════════════════════════════════════════════
BASE_RATES = json.load(io.open(os.path.join(HERE, "data", "market.json"), encoding="utf-8"))["rates"]


def rate_rows(last):
    rows, seen = [], set()
    for dec, v in BASE_RATES:
        y = int(dec)
        m = int(round((dec - y) * 12)) + 1
        t = "%04d%02d" % (y, m)
        if t not in seen:
            rows.append({"TIME": t, "DATA_VALUE": str(v), "STAT_NAME": "한국은행 기준금리 및 여수신금리",
                         "ITEM_NAME1": "한국은행 기준금리", "UNIT_NAME": "연%"})
            seen.add(t)
    rows.append({"TIME": "202609", "DATA_VALUE": str(last), "STAT_NAME": "한국은행 기준금리 및 여수신금리",
                 "ITEM_NAME1": "한국은행 기준금리", "UNIT_NAME": "연%"})
    return rows


# 시나리오마다 바꾸는 값
S = {}


def reset_state():
    S.clear()
    S.update({
        "base": 3.0, "mort": 4.55,
        "price_a1": 0.150, "price_a2": 0.012, "price_prd": "20260907",
        "jeonse_a1": 62.95, "jeonse_a2": 74.91, "jeonse_prd": "202608", "jeonse_tbl": "유형별 매매가격 대비 전세가격 비율",
        "unsold_nat": 69000, "unsold_cap": 19800, "bad": 29000, "vol": 64000, "mprd": "202608",
        "mixed_bad": False, "urls": [],
    })


def kosis_row(prd, v, tbl, itm="", unit="", c1=""):
    return {"PRD_DE": prd, "DT": str(v), "TBL_NM": tbl, "ITM_NM": itm, "UNIT_NM": unit, "C1_NM": c1}


def real_get_json(url, tries=3):
    S["urls"].append(url)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    if "/StatisticSearch/" in url:
        if "/722Y001/" in url:
            return {"StatisticSearch": {"row": rate_rows(S["base"])}}
        if "/121Y006/" in url:
            return {"StatisticSearch": {"row": [
                {"TIME": "202607", "DATA_VALUE": "4.48", "STAT_NAME": "예금은행 대출금리(신규취급액 기준)",
                 "ITEM_NAME1": "주택담보대출", "UNIT_NAME": "연%"},
                {"TIME": "202608", "DATA_VALUE": str(S["mort"]), "STAT_NAME": "예금은행 대출금리(신규취급액 기준)",
                 "ITEM_NAME1": "주택담보대출", "UNIT_NAME": "연%"}]}}
    if "statisticsParameterData" in url:
        t = q.get("tblId")
        if t == "DT_304004_WEEK_001_C":
            v = S["price_" + q["objL1"]]
            return [kosis_row(S["price_prd"], v, "주간 아파트 매매가격지수 변동률", "변동률", "%", q["objL1"])]
        if t == "DT_30404_N0006_R1":
            v = S["jeonse_" + q["objL2"]]
            return [kosis_row(S["jeonse_prd"], v, S["jeonse_tbl"], "비율", "%", "아파트")]
        if t == "DT_MLTM_2080":
            v = S["unsold_nat"] if q["objL1"].endswith("A.0001") else S["unsold_cap"]
            return [kosis_row("202607", 68217 if q["objL1"].endswith("A.0001") else 19459,
                              "규모별 미분양현황", "미분양", "호", q["objL1"]),
                    kosis_row(S["mprd"], v, "규모별 미분양현황", "미분양", "호", q["objL1"])]
        if t == "DT_MLTM_5328":
            rows = [kosis_row(S["mprd"], S["bad"], "공사완료후 미분양현황", "미분양", "호", "계")]
            if S["mixed_bad"]:     # objL4 를 빼먹었을 때처럼 규모별 계열이 섞여 들어옴
                rows.append(kosis_row(S["mprd"], 4100, "공사완료후 미분양현황", "미분양", "호", "60㎡이하"))
            return rows
        if t == "DT_408_2006_S0057":
            return [kosis_row(S["mprd"], S["vol"], "행정구역별 주택매매거래현황", "동(호)수", "호", "전국")]
    raise AssertionError("예상 못 한 URL: " + url)


def setup_tmp():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "data"))
    shutil.copy(os.path.join(HERE, "data", "market.json"), os.path.join(tmp, "data", "market.json"))
    refresh.DATA = os.path.join(tmp, "data", "market.json")
    refresh.HISTORY = os.path.join(tmp, "data", "history")
    refresh.LOGFILE = os.path.join(tmp, "data", "log.jsonl")
    refresh.CONF = os.path.join(HERE, "sources.json")
    return tmp


def run(accept=()):
    refresh.REPORT = []
    out = io.StringIO()
    so = sys.stdout
    sys.stdout = out
    try:
        rc = refresh.cmd_update(dry=False, accept=accept)
    finally:
        sys.stdout = so
    data = json.load(io.open(refresh.DATA, encoding="utf-8"))
    by = {d["key"]: d for d in data["indicators"]}
    st = {k: s for k, s, _ in refresh.REPORT}
    return rc, data, by, st


refresh.get_json = real_get_json

print("\n시나리오 A — 정상 갱신")
reset_state()
tmp = setup_tmp()
orig = json.load(io.open(refresh.DATA, encoding="utf-8"))
rc, data, by, st = run()
check("종료코드 0(게시)", rc == 0, rc)
check("주간 가격 갱신", by["price_capital"]["value"] == 0.15 and by["price_capital"]["asof"] == "2026년 9월 7일",
      (by["price_capital"]["value"], by["price_capital"]["asof"]))
check("지방 미분양 = 전국 − 수도권", by["unsold_local"]["value"] == 69000 - 19800, by["unsold_local"]["value"])
check("준공후 미분양 갱신", by["bad"]["value"] == 29000, by["bad"]["value"])
check("objL4 까지 전달", any("objL4=" in u for u in S["urls"] if "DT_MLTM_5328" in u))
check("수동 지표 보존", by["khai"]["value"] == 179.3)
check("직전값 보관(previous)", data["previous"]["values"]["price_capital"] == 0.171,
      data.get("previous", {}).get("values", {}).get("price_capital"))
check("지문 기록", data["fingerprints"]["jeonse_capital"]["tbl"] == "유형별 매매가격 대비 전세가격 비율")
check("계산식 입력 지문", "unsold_local.national" in data["fingerprints"])
check("스냅샷 저장", os.path.exists(os.path.join(refresh.HISTORY, data["updated"] + ".json")))
check("로그 기록", os.path.exists(refresh.LOGFILE))
check("공개 로그(lastrun)", data["lastrun"]["verdict"] == "게시")
shutil.rmtree(tmp)

print("\n시나리오 B — 통계표가 바뀌어 다른 표 숫자가 옴 (지문 불일치)")
reset_state()
tmp = setup_tmp()
_, d0, b0, _ = run()                                   # 첫 실행으로 지문 기록
first = b0["jeonse_capital"]["value"]
S["jeonse_tbl"] = "오피스텔 매매가격 대비 전세가격 비율"
S["jeonse_a1"] = 84.5
S["jeonse_prd"] = "202609"
rc, data, by, st = run()
check("전세가율 거부", st.get("jeonse_capital") == "거부", st.get("jeonse_capital"))
check("기존 값 유지(84.5 안 들어감)", by["jeonse_capital"]["value"] == first, by["jeonse_capital"]["value"])
check("다른 지표는 게시", rc == 0, rc)
shutil.rmtree(tmp)

print("\n시나리오 C — 조회 조건 누락으로 계열이 섞임")
reset_state()
S["mixed_bad"] = True
tmp = setup_tmp()
rc, data, by, st = run()
check("준공후 미분양 실패 처리", st.get("bad") == "실패", st.get("bad"))
check("기존 값 유지", by["bad"]["value"] == 29152, by["bad"]["value"])
shutil.rmtree(tmp)

print("\n시나리오 D — 말이 안 되는 값 (범위 밖)")
reset_state()
S["price_a1"] = 12.0
tmp = setup_tmp()
rc, data, by, st = run()
check("범위 밖 거부", st.get("price_capital") == "거부", st.get("price_capital"))
check("기존 값 유지", by["price_capital"]["value"] == 0.171)
shutil.rmtree(tmp)

print("\n시나리오 E — 급변은 보류, 확인 후 --accept 로 통과")
reset_state()
S["base"] = 4.0
S["mort"] = 5.4
tmp = setup_tmp()
rc, data, by, st = run()
check("기준금리 보류", st.get("base") == "보류", st.get("base"))
check("보류 시 기존 값", by["base"]["value"] == 3.0)
rc, data, by, st = run(accept=("base",))
check("--accept 후 반영", by["base"]["value"] == 4.0 and st.get("base") == "OK", (by["base"]["value"], st.get("base")))
shutil.rmtree(tmp)

print("\n시나리오 F — 급변을 잘못 승인해도 교차 검증이 전체 게시를 막음")
reset_state()
S["mort"] = 3.1                                       # 기준금리 3.00 과 가산금리 0.1%p → 비정상
tmp = setup_tmp()
before = io.open(refresh.DATA, encoding="utf-8").read()
rc, data, by, st = run(accept=("mort",))
after = io.open(refresh.DATA, encoding="utf-8").read()
check("종료코드 2", rc == 2, rc)
check("파일 그대로", before == after)
check("로그는 남음", "교차 검증 실패" in io.open(refresh.LOGFILE, encoding="utf-8").read())
shutil.rmtree(tmp)

print("\n시나리오 G — 기준시점 역행")
reset_state()
S["jeonse_prd"] = "202605"
tmp = setup_tmp()
rc, data, by, st = run()
check("과거 시점 거부", st.get("jeonse_capital") == "거부", st.get("jeonse_capital"))
shutil.rmtree(tmp)

print("\n" + ("ALL PASS" if not fails else "실패 %d건: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
