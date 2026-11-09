"""에이전트 업무 요청함 배치 (10분마다).

1) 볼트 요청함.md의 `- [ ] @워크스페이스: 요청` 줄을 그 워크스페이스의 열린 Claude 세션에 전달하고
   '전달 기록'으로 옮긴다.
2) 오늘 대화 기록으로 `업무일지/YYYY-MM-DD 업무 요청 회의록.md`를 새로 쓴다.
사용: python inbox.py [--dry-run]
"""
import datetime as dt
import json
import re
import sys
import time

import run as base

VAULT = base.HOME / "Documents" / "whisky" / "AI 활용" / "에이전트 업무 요청"
INBOX = VAULT / "요청함.md"
LOGDIR = VAULT / "업무일지"
TAGS = "#개인 #개인/AI도구 #에이전트업무요청"
NOW = dt.datetime.now()
DRY = "--dry-run" in sys.argv
ITEM = re.compile(r"^- \[ \] @(?P<ws>[^:：]+)[:：]\s*(?P<body>.+?)(?:\s+⚠.*)?$")
LIST_MARK = "## 워크스페이스 목록"
DONE_MARK = "## 전달 기록"


def log(msg):
    (base.ROOT / "logs").mkdir(exist_ok=True)
    with open(base.ROOT / "logs" / "inbox.log", "a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def key(s):
    return re.sub(r"[\s\-_·/]", "", s or "").lower()


# ---------- 워크스페이스 ↔ 터미널 ----------
def workspaces():
    """[{name, repo, path, kind, terms:[{handle,title,agent,working}]}]"""
    ps, tl = base.orca("worktree", "ps"), base.orca("terminal", "list")
    if ps is None or tl is None:
        return None
    norm = lambda p: (p or "").replace("\\", "/").rstrip("/")
    terms = tl.get("terminals", [])
    out = []
    for w in ps.get("worktrees", []):
        if w.get("isArchived"):
            continue
        path = norm(w.get("path"))
        if any(s in path for s in base.CFG["stray_patterns"]) and path not in base.CFG["keep_repos"]:
            continue
        panes = {a.get("paneKey") for a in w.get("agents", [])}
        folder = w.get("workspaceKind") == "folder-workspace"
        mine = [t for t in terms if f"{t.get('tabId')}:{t.get('leafId')}" in panes or
                (not folder and norm(t.get("worktreePath")) == path)]
        out.append({"name": w.get("displayName") or path.rsplit("/", 1)[-1], "repo": w.get("repo") or "",
                    "path": path, "kind": "폴더" if folder else "git",
                    "terms": [{"handle": t["handle"], "agent": t.get("agentIdentity") or "shell",
                               "title": t.get("title", "").lstrip("✳◐◑◒◓ ").strip(),
                               "working": not t.get("title", "").startswith("✳")} for t in mine]})
    return out


def resolve(wss, name):
    k = key(name)
    hits = [w for w in wss if key(w["name"]) == k or key(f"{w['repo']}/{w['name']}") == k]
    if not hits:
        hits = [w for w in wss if any(key(t["title"]) == k for t in w["terms"])]
    return hits


# ---------- 1) 요청 전달 ----------
def dispatch(wss):
    if not INBOX.exists():
        return 0
    before = INBOX.stat().st_mtime
    lines = INBOX.read_text(encoding="utf-8").splitlines()
    stamp = NOW.strftime("%m-%d %H:%M")
    keep, done, sent = [], [], 0
    i = 0
    stop = next((j for j, l in enumerate(lines) if l.startswith((LIST_MARK, DONE_MARK))), len(lines))
    while i < len(lines):
        line = lines[i]
        m = ITEM.match(line) if i < stop else None
        if not m:
            keep.append(line)
            i += 1
            continue
        cont = []
        while i + 1 < stop and re.match(r"^(\t| {2,})\S", lines[i + 1]):
            cont.append(lines[i + 1].strip())
            i += 1
        ws, body = m["ws"].strip(), " / ".join([m["body"].strip(), *cont])
        hits = resolve(wss, ws) if wss is not None else []
        target = None
        if wss is None:
            note = "Orca가 꺼져 있음"
        elif not hits:
            note = "워크스페이스 이름을 못 찾음"
        elif len(hits) > 1:
            note = "여러 워크스페이스와 일치: " + ", ".join(f"{h['repo']}/{h['name']}" for h in hits)
        else:
            claude = [t for t in hits[0]["terms"] if t["agent"] == "claude"]
            target = claude[0] if claude else None
            note = None if target else "열린 Claude 세션 없음"
        if target:
            msg = f"[요청함 {stamp}] {body}"
            ok = True
            if not DRY:
                code, out, _ = base.sh([base.shutil.which("orca"), "terminal", "send", "--terminal",
                                        target["handle"], "--text", msg, "--enter", "--json"])
                ok = code == 0 and '"ok": true' in out.replace('"ok":true', '"ok": true')
            if ok:
                done.append(f"- [x] {stamp} @{ws} → {target['title'] or '세션'}: {body}")
                sent += 1
                log(f"전달 @{ws}: {body[:80]}")
                i += 1
                continue
            note = "전달 실패, 다음 실행에서 다시 시도"
        keep.append(f"- [ ] @{ws}: {body} ⚠ {note} ({stamp})")
        i += 1
    if not sent and keep == lines:
        return 0
    # 전달 기록 섹션 맨 위에 새 항목을 넣는다
    try:
        d = keep.index(DONE_MARK)
        keep[d + 1:d + 1] = [""] + done if done else []
    except ValueError:
        keep += ["", DONE_MARK, ""] + done
    if INBOX.stat().st_mtime != before:  # 그사이 사용자가 고쳤으면 이번엔 쓰지 않는다
        log("요청함이 편집 중이라 이번 실행은 건너뜀")
        return 0
    if not DRY:
        INBOX.write_text("\n".join(keep).rstrip() + "\n", encoding="utf-8")
    return sent


def refresh_list(wss):
    if wss is None or not INBOX.exists():
        return
    text = INBOX.read_text(encoding="utf-8")
    head, _, rest = text.partition(LIST_MARK)
    tail = rest[rest.find(DONE_MARK):] if DONE_MARK in rest else ""
    rows = ["", f"{NOW:%m-%d %H:%M} 기준. 요청에는 왼쪽 이름을 그대로 쓰세요. 이름이 겹치면 `저장소/이름`으로 씁니다.", "",
            "| 이름 | 저장소 | 열린 Claude 세션 |", "|---|---|---|"]
    for w in sorted(wss, key=lambda w: (not w["terms"], w["repo"], w["name"])):
        ses = ", ".join(("작업 중: " if t["working"] else "") + t["title"] for t in w["terms"] if t["agent"] == "claude")
        rows.append(f"| {w['name']} | {w['repo']} | {ses or '없음'} |")
    new = head + LIST_MARK + "\n" + "\n".join(rows) + "\n\n" + tail
    if new != text and not DRY:
        INBOX.write_text(new.rstrip() + "\n", encoding="utf-8")


# ---------- 2) 회의록 ----------
def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def tidy(t, limit):
    t = re.sub(r"<command-name>(.*?)</command-name>.*?<command-args>(.*?)</command-args>", r"\1 \2", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    lines = [l.rstrip() for l in t.strip().splitlines() if l.strip()]
    out = "\n".join(lines)
    return out if len(out) <= limit else out[:limit].rstrip() + " …"


def sessions_today():
    start = dt.datetime.combine(NOW.date(), dt.time())
    out = []
    for f in (base.HOME / ".claude" / "projects").glob("*/*.jsonl"):
        if dt.datetime.fromtimestamp(f.stat().st_mtime) < start:
            continue
        s = {"id": f.stem, "title": "", "cwd": "", "turns": []}
        try:
            for line in f.open(encoding="utf-8"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                kind = d.get("type")
                if kind == "ai-title":
                    s["title"] = d.get("aiTitle", "")
                if kind not in ("user", "assistant") or d.get("isSidechain"):
                    continue
                s["cwd"] = s["cwd"] or (d.get("cwd") or "").replace("\\", "/").rstrip("/")
                ts = d.get("timestamp", "")
                try:
                    when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                except ValueError:
                    continue
                text = text_of(d.get("message", {}).get("content"))
                if kind == "user" and not d.get("isMeta"):
                    if text and not text.lstrip().startswith(base.NOISE) and "system-reminder" not in text[:40]:
                        s["turns"].append({"at": when, "ask": tidy(text, 1200), "reply": "", "replyAt": None})
                elif kind == "assistant" and s["turns"] and len(text.strip()) > 20:
                    s["turns"][-1].update(reply=text, replyAt=when)
        except OSError:
            continue
        s["turns"] = [t for t in s["turns"] if t["at"] >= start]
        if s["turns"]:
            out.append(s)
    return out


def write_minutes(wss):
    sess = sessions_today()
    by_cwd, by_title = {}, {}
    for w in wss or []:
        if w["kind"] == "git":
            by_cwd[w["path"]] = w["name"]
        for t in w["terms"]:
            by_title[t["title"]] = w["name"]
    for s in sess:
        s["ws"] = by_title.get(s["title"]) or by_cwd.get(s["cwd"]) or (s["cwd"].rsplit("/", 1)[-1] + " (워크스페이스 미확인)")
    sess.sort(key=lambda s: (s["ws"] != "lead agent", s["ws"], s["turns"][0]["at"]))
    n_req = sum(len(s["turns"]) for s in sess)
    L = [TAGS, "", f"# {NOW:%Y-%m-%d} 에이전트 업무 요청 회의록", "",
         f"- 자동 생성: {NOW:%H:%M} (10분마다 다시 씀. 이 파일을 직접 고치면 다음 실행 때 덮어써집니다. 메모는 [[요청함]]에 남기세요)",
         f"- 세션 {len(sess)}개 · 요청 {n_req}건", "", "## 요약", "",
         "| 워크스페이스 | 세션 | 요청 | 첫 요청 | 마지막 응답 |", "|---|---|---|---|---|"]
    for s in sess:
        last = max((t["replyAt"] for t in s["turns"] if t["replyAt"]), default=None)
        L.append(f"| {s['ws']} | {s['title'] or s['id'][:8]} | {len(s['turns'])} | {s['turns'][0]['at']:%H:%M} | "
                 f"{last.strftime('%H:%M') if last else '응답 없음'} |")
    for s in sess:
        L += ["", f"## {s['ws']} · {s['title'] or s['id'][:8]}", "",
              f"이어서 하기: `claude --resume {s['id']}`"]
        for t in s["turns"]:
            src = "요청함" if t["ask"].startswith("[요청함") else "요청"
            L += ["", f"### {t['at']:%H:%M} {src}", ""]
            L += ["> " + l for l in t["ask"].splitlines()]
            L += [""]
            if t["replyAt"]:
                L += [f"**응답 {t['replyAt']:%H:%M}**", "", tidy(t["reply"], 1500)]
            else:
                L += ["**응답 대기**"]
    LOGDIR.mkdir(parents=True, exist_ok=True)
    path = LOGDIR / f"{NOW:%Y-%m-%d} 업무 요청 회의록.md"
    if not DRY:
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path, len(sess), n_req


def main():
    try:
        wss = workspaces()
        sent = dispatch(wss)
        refresh_list(wss)
        path, ns, nr = write_minutes(wss)
        log(f"완료: 전달 {sent}건, 회의록 세션 {ns}개·요청 {nr}건{' (dry-run)' if DRY else ''}")
        if DRY:
            print(sent, ns, nr, path)
    except Exception as e:
        log(f"실패: {e!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
