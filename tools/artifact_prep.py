#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude.ai 아티팩트로 미리보기를 게시할 때만 쓰는 도구. (GitHub 배포에는 필요 없음)

  python3 tools/artifact_prep.py
    → build.py 실행
    → _artifact/index.html   : 아티팩트 본문용 조각(<head>/<body> 태그 없이)
    → _artifact/_src/bundle.json : 사이트 소스 전체 (다음 세션이 이걸로 복원)
    → _artifact/files.json   : Artifact 도구 files 인자에 그대로 넣을 {게시경로: 로컬경로}

복원:  python3 -c "import json,os;b=json.load(open('bundle.json'));[ (os.makedirs(os.path.dirname(os.path.join('web',p)) or '.',exist_ok=True), open(os.path.join('web',p),'w',encoding='utf-8').write(t)) for p,t in b['files'].items()]"
"""
import io, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(HERE, "public")
OUT = os.path.join(HERE, "_artifact")
SKIP_DIRS = {"public", "_artifact", "__pycache__", ".git", "cache"}

subprocess.check_call([sys.executable, os.path.join(HERE, "build.py")])
os.makedirs(os.path.join(OUT, "_src"), exist_ok=True)

h = io.open(os.path.join(PUB, "index.html"), encoding="utf-8").read()
head = h[h.index("<title>"):h.index("</head>")]
body = h[h.index(">", h.index("<body")) + 1:h.index("</body>")]
head = re.sub(r"<title>[^<]*</title>", "<title>부동산 시장 온도계</title>", head, 1)
io.open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(head + "\n" + body)

files = {}
for d, dirs, fs in os.walk(HERE):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    for f in fs:
        if f.endswith((".pyc", ".tmp")):
            continue
        p = os.path.join(d, f)
        files[os.path.relpath(p, HERE)] = io.open(p, encoding="utf-8").read()
json.dump({"note": "부동산 시장 온도계 사이트 소스 전체. tools/artifact_prep.py 가 만든다.", "files": files},
          io.open(os.path.join(OUT, "_src", "bundle.json"), "w", encoding="utf-8"), ensure_ascii=False)

fmap = {}
for d, _, fs in os.walk(PUB):
    for f in fs:
        rel = os.path.relpath(os.path.join(d, f), PUB)
        if rel in ("index.html", ".nojekyll", "CNAME", "sitemap.xml", "ads.txt") or re.fullmatch(r"[0-9a-f]{32}\.txt", rel):
            continue
        fmap[rel] = os.path.join(d, f)
fmap["source/bundle.json"] = os.path.join(OUT, "_src", "bundle.json")
json.dump(fmap, io.open(os.path.join(OUT, "files.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("아티팩트 준비 완료: %s (파일 %d개, 소스 %d개)" % (OUT, len(fmap), len(files)))
