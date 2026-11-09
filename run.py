"""로컬 에이전트 관제판 갱신 배치.

수집(Orca·폴더·프로세스) → data.json → index.html 렌더 → git commit·push.
사용: python run.py [--no-push]
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
CFG = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
NOW = dt.datetime.now()
SKIP_WALK = {"node_modules", ".git", ".next", "__pycache__", ".venv", "venv", ".vercel"}
MARKERS = ("CLAUDE.md", ".claude", "agents", ".git")


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(ROOT / "logs" / "run.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(args, cwd=None, timeout=60):
    r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def orca(*args):
    exe = shutil.which("orca")
    if not exe:
        return None
    code, out, _ = sh([exe, *args, "--json"])
    if code != 0:
        return None
    try:
        d = json.loads(out)
        return d["result"] if d.get("ok") else None
    except (ValueError, KeyError):
        return None


def fmt_ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M") if ms else "-"


def last_mod(path, depth=3):
    best = 0.0
    base = len(path.parts)
    for d, dirs, files in os.walk(path):
        dirs[:] = [x for x in dirs if x not in SKIP_WALK]
        if len(Path(d).parts) - base >= depth:
            dirs[:] = []
        for f in files:
            try:
                best = max(best, os.path.getmtime(os.path.join(d, f)))
            except OSError:
                pass
    return dt.datetime.fromtimestamp(best) if best else None


def status_of(when):
    if not when:
        return "idle"
    age = (NOW - when).days
    return "live" if age <= 7 else "recent" if age <= 90 else "idle"


def auto_chain(path):
    chain = [[p.stem, ""] for p in sorted((path / ".claude" / "agents").glob("*.md"))]
    chain += [[p.stem, ""] for p in sorted((path / "agents").glob("*.py"))
              if p.stem not in ("__init__", "orchestrator")]
    return chain


# ---------- 수집 ----------
def collect_orca(prev):
    wts = orca("worktree", "list")
    terms = orca("terminal", "list")
    if wts is None or terms is None:
        old = (prev or {}).get("orca")
        if old:
            old["stale"] = True
            return old
        return {"ok": False, "stale": True, "repos": [], "loose": [], "stray": [], "dupes": []}

    norm = lambda p: (p or "").replace("\\", "/").rstrip("/")
    stray_pat, keep = CFG["stray_patterns"], set(CFG["keep_repos"])
    tlist = terms.get("terminals", [])
    by_path = {}
    for t in tlist:
        title = t.get("title", "")
        working = bool(title) and not title.startswith("✳")
        clean = title.lstrip("✳◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ ").strip() or "(제목 없음)"
        by_path.setdefault(norm(t.get("worktreePath")), []).append({
            "title": clean, "last": fmt_ts(t.get("lastOutputAt")),
            "agent": t.get("agentIdentity") or "shell", "working": working})

    repos, stray, seen_names = {}, [], Counter()
    for w in wts.get("worktrees", []):
        path = norm(w["path"])
        if w.get("isArchived"):
            continue
        if any(s in path for s in stray_pat) and path not in keep:
            stray.append(path)
            continue
        if "/orca/workspaces/" in path:
            repo = path.split("/orca/workspaces/")[1].split("/")[0]
        else:
            repo = path.rsplit("/", 1)[-1]
            if w.get("isMainWorktree"):
                seen_names[repo] += 1
        if path in keep:
            repo = "manid"
        r = repos.setdefault(repo, {"name": repo, "worktrees": []})
        r["worktrees"].append({
            "name": w.get("displayName") or path.rsplit("/", 1)[-1],
            "status": w.get("workspaceStatus") or "in-progress",
            "last": fmt_ts(w.get("lastActivityAt")),
            "lastMs": w.get("lastActivityAt") or 0,
            "terms": by_path.pop(path, []),
        })
    for r in repos.values():
        r["worktrees"].sort(key=lambda x: (x["status"] == "completed", -len(x["terms"]), -x["lastMs"]))
        r["sessions"] = sum(len(x["terms"]) for x in r["worktrees"])
    # 터미널도 없고 90일 넘게 활동 없는 저장소는 접어서 표시
    cutoff = (NOW - dt.timedelta(days=90)).timestamp() * 1000
    active = [r for r in repos.values() if r["sessions"] or any(x["lastMs"] > cutoff for x in r["worktrees"])]
    quiet = sorted(r["name"] for r in repos.values() if r not in active)
    active.sort(key=lambda r: -r["sessions"])
    loose = [t for p, ts in by_path.items() for t in ts]
    dupes = sorted(n for n, c in seen_names.items() if c > 1)
    return {"ok": True, "stale": False, "repos": active, "quiet": quiet, "loose": loose,
            "stray": sorted(stray), "dupes": dupes,
            "completed": [x["name"] for r in active for x in r["worktrees"] if x["status"] == "completed"]}


def collect_projects(orca_data):
    sessions = {r["name"]: r["sessions"] for r in orca_data.get("repos", [])}
    conf = {p["name"]: p for p in CFG["projects"]}
    names = list(conf)
    ignore = set(CFG["ignore_dirs"])
    for d in sorted(HOME.iterdir()):
        if (d.is_dir() and not d.name.startswith(".") and d.name not in ignore and d.name not in conf
                and any((d / m).exists() for m in MARKERS)):
            names.append(d.name)
    out = []
    for n in names:
        path = HOME / n
        if not path.is_dir():
            continue
        c = conf.get(n, {"cmd": "새로 발견된 폴더", "chain": None, "new": True, "org": "미분류"})
        chain = c["chain"] if c.get("chain") is not None else auto_chain(path)
        if sessions.get(n):
            chain = [[f"Orca 세션 ×{sessions[n]}", "shared"], *chain]
        when = last_mod(path)
        out.append({"n": n, "s": status_of(when), "d": when.strftime("%Y-%m-%d") if when else "-",
                    "c": c["cmd"], "ch": chain, "new": bool(c.get("new")), "org": c.get("org", "미분류")})
    order = {o: i for i, o in enumerate(CFG.get("orgs", []))}
    out.sort(key=lambda p: p["d"], reverse=True)
    out.sort(key=lambda p: order.get(p["org"], 99))
    return out


def collect_procs():
    code, out, _ = sh(["tasklist", "/FO", "CSV", "/NH"])
    names = Counter(line.split(",")[0].strip('"').lower() for line in out.splitlines() if line)
    return {"claude": names.get("claude.exe", 0),
            "gemini": sum(v for k, v in names.items() if k.startswith("gemini") and "crash" not in k)}


def collect_pool():
    found = {p.stem for p in (HOME / ".claude" / "agents").glob("*.md")}
    teams, used = [], set()
    for t in CFG["pool"]:
        agents = [a for a in t["agents"] if a in found]
        used |= set(agents)
        teams.append({**t, "agents": agents})
    extra = sorted(found - used)
    if extra:
        teams.append({"team": "미분류", "agents": extra, "used": "projects.json의 pool에 팀을 지정하세요"})
    return teams, len(found)


def build_data():
    prev_file = ROOT / "data.json"
    prev = json.loads(prev_file.read_text(encoding="utf-8")) if prev_file.exists() else None
    o = collect_orca(prev)
    projects = collect_projects(o)
    procs = collect_procs()
    pool, n_global = collect_pool()
    all_terms = [t for r in o["repos"] for w in r["worktrees"] for t in w["terms"]] + o["loose"]
    claude_s = sum(1 for t in all_terms if t["agent"] == "claude")
    gemini_s = sum(1 for t in all_terms if t["agent"] == "gemini")
    limit = CFG["claude_session_limit"]

    alerts = []
    if o.get("stale"):
        alerts.append({"level": "warn", "text": "Orca가 꺼져 있어 세션 정보는 지난 갱신 값입니다."})
    if claude_s > limit:
        alerts.append({"level": "warn", "text": f"Claude 동시 세션 {claude_s}개. 운용 원칙은 동시 {limit}개 이하입니다. claude.exe 프로세스 {procs['claude']}개."})
    if gemini_s == 0:
        alerts.append({"level": "info", "text": "Gemini 세션이 없습니다. 긴 문서 대조·웹 리서치·반복 작업은 Gemini로 넘기면 Claude 사용량이 줄어듭니다."})
    new = [p["n"] for p in projects if p["new"]]
    if new:
        alerts.append({"level": "info", "text": "새 프로젝트 폴더 발견: " + ", ".join(new) + ". projects.json에 체인을 등록하세요."})

    sug = []
    if claude_s > limit:
        sug.append(["Claude 세션", f"{claude_s}개 동시 열림", f"오늘 쓰는 {limit}개만 남기고 나머지는 handoff 메모 후 닫기", "Pro 한도 보호"])
    if o.get("completed"):
        sug.append(["완료된 워크트리", ", ".join(o["completed"]), "세션 종료 후 Orca에서 삭제(브랜치는 유지)", "목록 정리"])
    if o.get("stray"):
        sug.append(["잘못 등록된 저장소", f"{len(o['stray'])}개 (.gemini/history, .vscode, 플러그인)", "Orca 사이드바에서 프로젝트 제거", "목록 노이즈 제거"])
    if o.get("dupes"):
        sug.append(["중복 저장소", ", ".join(o["dupes"]), "실사용 경로 하나로 통일", "작업 위치 혼동 방지"])
    if gemini_s == 0:
        sug.append(["Gemini 활용", "열린 세션 0", "리서치·긴 문서 대조는 Orca Gemini 워커로 배정", "Claude 토큰 절약"])

    project_agents = sum(1 for p in projects for _, k in p["ch"] if k in ("", "opus"))
    return {
        "generated": NOW.strftime("%Y-%m-%d %H:%M"),
        "counts": {"projects": len(projects), "projectAgents": project_agents, "globalAgents": n_global,
                   "claude": claude_s, "gemini": gemini_s, "limit": limit},
        "orgs": CFG.get("orgs", []), "alerts": alerts, "orca": o, "projects": projects, "pool": pool, "suggestions": sug,
    }


# ---------- 렌더·배포 ----------
def render(data):
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
    (ROOT / "docs" / ".nojekyll").write_text("", encoding="utf-8")
    (ROOT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def push():
    sh(["git", "add", "-A"], cwd=ROOT)
    code, _, _ = sh(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if code == 0:
        log("변경 없음, 커밋 생략")
        return
    sh(["git", "commit", "-m", f"chore: daily snapshot {NOW:%Y-%m-%d %H:%M}"], cwd=ROOT)
    code, _, err = sh(["git", "push", "origin", "HEAD"], cwd=ROOT, timeout=120)
    log("push 완료" if code == 0 else f"push 실패: {err.strip()[:300]}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    (ROOT / "logs").mkdir(exist_ok=True)
    try:
        data = build_data()
        render(data)
        c = data["counts"]
        log(f"갱신 완료: 프로젝트 {c['projects']}, Claude {c['claude']}, Gemini {c['gemini']}, Orca {'꺼짐' if data['orca'].get('stale') else '정상'}")
        if "--no-push" not in sys.argv:
            push()
    except Exception as e:  # 배치는 실패를 로그에 남기고 종료 코드로 알린다
        log(f"실패: {e!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
