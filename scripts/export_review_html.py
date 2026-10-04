"""품질 데이터셋 6개 JSONL을 사람이 검토하기 좋은 단일 HTML 파일로 내보낸다.

표준 라이브러리만 쓰고 네트워크를 호출하지 않는다.
    python3 scripts/export_review_html.py
    python3 scripts/export_review_html.py --output /tmp/review.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "evals" / "quality_datasets"
DEFAULT_OUTPUT = ROOT / "evals" / "review" / "quality_dataset_review.html"

DATASETS = [
    ("persona_onboarding_conversation", "온보딩 발화"),
    ("persona_onboarding_tagging", "온보딩 태깅"),
    ("persona_build", "페르소나 build"),
    ("practice_reply", "연습대화"),
    ("simulation_run", "시뮬레이션 대본"),
]


def load() -> list[dict]:
    out = []
    for name, label in DATASETS:
        path = DATA_DIR / f"{name}.jsonl"
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
        out.append({"name": name, "label": label, "rows": rows})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = json.loads((DATA_DIR / "manifest.json").read_text("utf-8"))
    version = manifest.get("version") or manifest.get("datasetVersion") or ""
    payload = json.dumps(load(), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", payload).replace("__VERSION__", str(version))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, "utf-8")
    print(f"wrote {args.output} ({len(html) // 1024} KB)")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>품질 데이터셋 검토</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mute:#66707f;--line:#dfe3ea;--acc:#3457d5;--ok:#1b8a4b;--warn:#b7791f;--bad:#c53030;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12151b;--card:#1a1f28;--fg:#e6e9ef;--mute:#98a2b3;--line:#2b3240;--acc:#7c96f5;--ok:#4cc38a;--warn:#e0b04c;--bad:#f07272;--chip:#242b37}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:17px;margin:0 0 8px}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
button,select,input,textarea{font:inherit;color:inherit}
.tab,.seg{border:1px solid var(--line);background:var(--chip);border-radius:8px;padding:4px 10px;cursor:pointer}
.tab[aria-pressed=true],.seg[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:#fff}
input[type=search]{flex:1;min-width:160px;border:1px solid var(--line);background:var(--bg);border-radius:8px;padding:5px 10px}
main{max-width:980px;margin:0 auto;padding:16px}
.note{color:var(--mute);font-size:13px}
.case{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0}
.case>summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.case>summary::-webkit-details-marker{display:none}
.cid{font-weight:600}
.chip{background:var(--chip);border-radius:6px;padding:1px 8px;font-size:12px;color:var(--mute)}
.chip.v-ok{color:var(--ok)}.chip.v-warn{color:var(--warn)}.chip.v-bad{color:var(--bad)}
.body{padding:0 14px 14px;border-top:1px solid var(--line)}
h3{font-size:13px;color:var(--mute);margin:16px 0 6px;letter-spacing:.02em}
ul{margin:4px 0;padding-left:20px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:2px 12px;font-size:14px}
.kv>dt{color:var(--mute);word-break:break-all}.kv>dd{margin:0;min-width:0}
.bubble{max-width:85%;padding:6px 10px;border-radius:10px;margin:4px 0;background:var(--chip)}
.bubble.user,.bubble.b{margin-left:auto;background:color-mix(in srgb,var(--acc) 18%,var(--card))}
.bubble small{display:block;color:var(--mute);font-size:11px}
.forbid li{color:var(--bad)}
.must li{color:var(--ok)}
details.more{margin-top:8px}details.more>summary{cursor:pointer;color:var(--mute);font-size:13px}
.anchors div{margin:3px 0}
.rev{margin-top:14px;padding-top:10px;border-top:1px dashed var(--line)}
.rev textarea{width:100%;min-height:52px;margin-top:8px;border:1px solid var(--line);background:var(--bg);border-radius:8px;padding:6px 10px}
.vbtn{border:1px solid var(--line);background:var(--chip);border-radius:8px;padding:3px 10px;cursor:pointer;margin-right:4px}
.vbtn[aria-pressed=true].ok{background:var(--ok);color:#fff;border-color:var(--ok)}
.vbtn[aria-pressed=true].warn{background:var(--warn);color:#fff;border-color:var(--warn)}
.vbtn[aria-pressed=true].bad{background:var(--bad);color:#fff;border-color:var(--bad)}
.act{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:4px 10px;cursor:pointer}
pre{white-space:pre-wrap;word-break:break-word;background:var(--chip);border-radius:8px;padding:8px;font-size:12px;margin:4px 0}
@media (max-width:600px){.kv{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>품질 데이터셋 검토 <span class="note">v__VERSION__ · 합성 데이터 · 사람 라벨 미확정</span></h1>
  <div class="row" id="tabs"></div>
  <div class="row" id="splits"></div>
  <div class="row">
    <input type="search" id="q" placeholder="caseId, 카테고리, 본문 검색">
    <span class="note" id="prog"></span>
    <button class="act" id="exp">검토 결과 내보내기(JSON)</button>
    <button class="act" id="expand">모두 펼치기/접기</button>
  </div>
</header>
<main>
  <p class="note">판정과 메모는 이 브라우저(localStorage)에만 저장됩니다. 다른 기기와 공유하려면 “내보내기”를 쓰세요.
  blind_holdout은 프롬프트 수정 시 참고하지 않는 용도입니다.</p>
  <div id="list"></div>
</main>
<script>
const DATA = __DATA__;
const SPLITS = ["calibration","regression","blind_holdout","전체"];
const VERDICTS = [["ok","적절"],["warn","수정 필요"],["bad","부적절"]];
const KEY = "qd-review-v1";
let state = {ds: DATA[0].name, split: "calibration", q: "", open: false};
let rev = {};
try { rev = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(rev)); } catch (e) {} };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function isChat(v){return Array.isArray(v) && v.length && v.every(x=>x && typeof x==="object" && "content" in x && "role" in x)}
function isTurns(v){return Array.isArray(v) && v.length && v.every(x=>x && typeof x==="object" && "speaker" in x && "text" in x)}
function render(v, depth=0){
  if (v === null || v === undefined) return '<span class="note">null</span>';
  if (typeof v !== "object") return esc(v);
  if (isChat(v)) return v.map(m=>`<div class="bubble ${esc(m.role)}"><small>${esc(m.role)}</small>${esc(m.content)}</div>`).join("");
  if (isTurns(v)) return v.map(m=>`<div class="bubble ${esc(m.speaker)}"><small>${esc(m.speaker)} #${esc(m.index??"")}</small>${esc(m.text)}</div>`).join("");
  if (Array.isArray(v)){
    if (!v.length) return '<span class="note">(빈 목록)</span>';
    if (v.every(x=>typeof x!=="object" || x===null)) return "<ul>"+v.map(x=>`<li>${render(x)}</li>`).join("")+"</ul>";
    return v.map(x=>`<div class="kvbox">${render(x,depth+1)}</div>`).join("<hr>");
  }
  const ks = Object.keys(v);
  if (!ks.length) return '<span class="note">(비어 있음)</span>';
  return '<dl class="kv">'+ks.map(k=>`<dt>${esc(k)}</dt><dd>${render(v[k],depth+1)}</dd>`).join("")+"</dl>";
}

function rubricHtml(r){
  if(!r) return "";
  const a = r.anchors ? '<div class="anchors">'+Object.entries(r.anchors).map(([k,t])=>`<div><b>${esc(k)}점</b> — ${esc(t)}</div>`).join("")+"</div>" : "";
  const c = r.criteria ? '<div class="note">배점: '+Object.entries(r.criteria).map(([k,t])=>`${esc(k)} ${esc(t)}`).join(" · ")+"</div>" : "";
  return a + c + `<div class="note">상태: ${esc(r.status||"")}</div>`;
}

function caseHtml(row){
  const m = row.metadata, e = row.expectedOutput, id = m.caseId, r = rev[id] || {};
  const known = new Set(["hardAssertions","forbidden","rubric","referenceLabels","requiredEvidence","acceptableRanges","runtime"]);
  const contracts = Object.fromEntries(Object.entries(e).filter(([k])=>!known.has(k)));
  const vlabel = (VERDICTS.find(x=>x[0]===r.v)||[])[1];
  return `<details class="case" data-id="${esc(id)}" ${state.open?"open":""}>
    <summary><span class="cid">${esc(id)}</span>
      <span class="chip">${esc(m.split)}</span><span class="chip">${esc(m.category)}</span>
      <span class="chip">${esc(m.difficulty)}</span>
      ${vlabel?`<span class="chip v-${r.v}">${vlabel}</span>`:""}</summary>
    <div class="body">
      <h3>입력 (모델에 주는 상황)</h3>${render(row.input)}
      <h3>반드시 지킬 것 (hardAssertions)</h3><ul class="must">${(e.hardAssertions||[]).map(x=>`<li>${esc(x)}</li>`).join("")||'<li class="note">없음</li>'}</ul>
      <h3>하면 안 되는 것 (forbidden)</h3><ul class="forbid">${(e.forbidden||[]).map(x=>`<li>${esc(x)}</li>`).join("")||'<li class="note">없음</li>'}</ul>
      <h3>채점 기준 (rubric)</h3>${rubricHtml(e.rubric)}
      <details class="more"><summary>세부 계약 (${Object.keys(contracts).join(", ")||"없음"})</summary>${render(contracts)}</details>
      <details class="more"><summary>허용 범위·참조 라벨·필수 근거·실행 제한</summary>
        ${["acceptableRanges","referenceLabels","requiredEvidence","runtime"].map(k=>`<h3>${k}</h3>${render(e[k])}`).join("")}</details>
      <details class="more"><summary>메타데이터·근거 코드</summary>${render(m)}</details>
      <div class="rev">
        <b>내 판정</b>
        ${VERDICTS.map(([k,l])=>`<button class="vbtn ${k}" data-v="${k}" aria-pressed="${r.v===k}">${l}</button>`).join("")}
        <textarea placeholder="이유·수정 제안 메모" data-memo>${esc(r.memo||"")}</textarea>
      </div>
    </div></details>`;
}

function rows(){
  const ds = DATA.find(d=>d.name===state.ds);
  const q = state.q.trim().toLowerCase();
  return ds.rows.filter(r=>(state.split==="전체"||r.metadata.split===state.split) &&
    (!q || JSON.stringify(r).toLowerCase().includes(q)));
}

function draw(){
  document.getElementById("tabs").innerHTML = DATA.map(d=>`<button class="tab" data-ds="${d.name}" aria-pressed="${d.name===state.ds}">${d.label} ${d.rows.length}</button>`).join("");
  document.getElementById("splits").innerHTML = SPLITS.map(s=>`<button class="seg" data-sp="${s}" aria-pressed="${s===state.split}">${s}</button>`).join("");
  const list = rows();
  document.getElementById("list").innerHTML = list.map(caseHtml).join("") || '<p class="note">조건에 맞는 케이스가 없습니다.</p>';
  const total = DATA.reduce((n,d)=>n+d.rows.length,0), done = Object.values(rev).filter(x=>x.v).length;
  document.getElementById("prog").textContent = `표시 ${list.length}건 · 판정 ${done}/${total}`;
}

document.addEventListener("click", ev=>{
  const t = ev.target;
  if (t.dataset.ds){ state.ds=t.dataset.ds; draw(); }
  else if (t.dataset.sp){ state.split=t.dataset.sp; draw(); }
  else if (t.dataset.v){
    const id = t.closest(".case").dataset.id; const cur = rev[id] || {};
    cur.v = cur.v===t.dataset.v ? "" : t.dataset.v; rev[id]=cur; save();
    const openIds = [...document.querySelectorAll(".case[open]")].map(x=>x.dataset.id);
    draw(); document.querySelectorAll(".case").forEach(x=>{ if(openIds.includes(x.dataset.id)) x.open=true; });
  }
  else if (t.id==="expand"){ state.open=!state.open; document.querySelectorAll(".case").forEach(x=>x.open=state.open); }
  else if (t.id==="exp"){
    const blob = new Blob([JSON.stringify(rev,null,2)],{type:"application/json"});
    const a = document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="dataset-review.json"; a.click();
  }
});
document.addEventListener("input", ev=>{
  const t = ev.target;
  if (t.id==="q"){ state.q=t.value; draw(); document.getElementById("q").focus(); }
  else if (t.dataset.memo!==undefined){
    const id = t.closest(".case").dataset.id; const cur = rev[id] || {}; cur.memo=t.value; rev[id]=cur; save();
  }
});
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
