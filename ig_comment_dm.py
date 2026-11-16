"""영어 인스타 이벤트(11-16~22): 이벤트 릴스(캡션에 Comment "REF")들의 댓글에 "REF"가 달리면 그 사람에게 IGEN 링크를 DM(비공개 답장)으로 보내고 댓글에 짧게 답한다.
맥 launchd가 30초마다 돌린다(11-15: 팔로우 확인형은 프로필 조회가 "사용자 동의 필요"로 막혀 댓글만으로 되돌림)(scripts/install_ig_dm_job.sh). 같은 사람에게는 한 번만. END 뒤에는 아무것도 안 한다.
기록: ~/.local/share/whistle/ig-dm-sent.json, 토큰: ~/.config/whistle/meta.json(영어 계정 ig_user_id·ig_token)
사용: python3 ig_comment_dm.py run | list"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

B = "https://graph.facebook.com/v21.0"
START = "2026-11-15T14:10:00+0000"           # 이 뒤에 처음 올라온 릴스 하나(이벤트 릴스)만 본다 — 찾으면 기록에 고정
END = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)   # 11-23 00:00 KST(= 미국 11-22 낮)까지
# 클라우드(whistle-sns-bot, 공개 저장소)에서는 기록이 공개되므로 HASH_USERS=1로 아이디를 해시로 남긴다(11-16)
STATE = Path(os.environ.get("WHISTLE_STATE_DIR", Path.home() / ".local/share/whistle")) / "ig-dm-sent.json"
LINK = "https://apps.apple.com/redeem?ctx=offercodes&id=6823503970&code=IGEN"
DM = ("Thanks for commenting! Here's your 1 month of Whistle PRO, free "
      f"(iPhone, no auto-renew, it just ends): {LINK}\n\n"
      "If you like it, a follow means a lot to a solo dev 🙏")
REPLY = "Sent! Check your DMs 📩 (message requests too)"
WORD = re.compile(r"\bref\b", re.I)
# DM으로 "ref" 한 단어만 보낸 사람에게는 링크 대신 릴스 댓글을 안내한다(11-16 사용자 결정: 신청은 모두 댓글로)
DM_ONLY = re.compile(r"^\s*ref\s*[!.]*\s*$", re.I)
REEL_URL = "https://www.instagram.com/reel/Dfw-HSzAOLw/"
DM_TO_COMMENT = 'Thanks! To get your free month, just comment "REF" on our latest reel and we\'ll DM you the link right away 🎁\n'
EVENT_CAPTION = re.compile(r'comment\s*["“]?ref', re.I)


def uk(user):
    """기록에 남기는 사람 이름 — 클라우드에선 해시"""
    if os.environ.get("HASH_USERS"):
        return "h:" + hashlib.sha256(user.lower().encode()).hexdigest()[:16]
    return user


def call(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "WhistleDM/1.0"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()[:300]) from None


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {"comments": {}, "users": {}}


def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def run():
    if datetime.now(timezone.utc) >= END:
        print("이벤트 끝남")
        return
    c = json.loads((Path.home() / ".config/whistle/meta.json").read_text())
    uid, tok = c["ig_user_id"], c["ig_token"]
    me = call(f"{B}/{uid}?fields=username&access_token={tok}")["username"]
    s = load()
    # 이벤트 릴스: START 뒤 릴스 중 캡션에 Comment "REF"가 있는 것 전부(11-16: 7초판 추가)
    media = call(f"{B}/{uid}/media?fields=id,timestamp,media_product_type,caption,permalink&limit=15&access_token={tok}")["data"]
    reels = sorted((m for m in media if m["timestamp"] >= START and m.get("media_product_type") == "REELS"
                    and EVENT_CAPTION.search(m.get("caption") or "")), key=lambda m: m["timestamp"])
    ids = s.setdefault("media_ids", [s.pop("media_id")] if s.get("media_id") else [])
    for m in reels:
        if m["id"] not in ids:
            ids.append(m["id"])
            print("이벤트 릴스 추가", m["id"], m["timestamp"])
    if reels:
        s["latest_url"] = reels[-1].get("permalink", REEL_URL)
    if not ids:
        print("이벤트 릴스 아직 없음")
        return
    save(s)
    for m in [{"id": i} for i in ids]:
        try:
            call(f"{B}/{m['id']}?fields=id&access_token={tok}")
        except RuntimeError as e:
            if "does not exist" in str(e):   # 지운 릴스는 목록에서 뺀다
                ids.remove(m["id"])
                print("지워진 릴스 뺌", m["id"])
                continue
            raise
        url = f"{B}/{m['id']}/comments?fields=id,text,username,timestamp&limit=50&access_token={tok}"
        while url:
            page = call(url)
            for x in page["data"]:
                cid, user, text = x["id"], x.get("username", ""), x.get("text", "")
                if cid in s["comments"] or user == me or not WORD.search(text):
                    continue
                if uk(user) in s["users"]:
                    s["comments"][cid] = {"user": uk(user), "status": "중복(이미 받음)"}
                    continue
                try:
                    call(f"{B}/{uid}/messages?access_token={tok}", {"recipient": {"comment_id": cid}, "message": {"text": DM}})
                    st = "보냄"
                    s["users"][uk(user)] = time.strftime("%Y-%m-%d %H:%M")
                    try:
                        call(f"{B}/{cid}/replies?access_token={tok}", {"message": REPLY})
                    except RuntimeError as e:
                        st += f" (댓글 답 실패: {e})"
                except RuntimeError as e:
                    st = f"실패: {e}"
                s["comments"][cid] = {"user": uk(user), "status": st, "at": time.strftime("%Y-%m-%d %H:%M")}
                print(uk(user), st)
                save(s)
            url = page.get("paging", {}).get("next")
    save(s)
    dms(uid, tok, me, s)


def dms(uid, tok, me, s):
    """DM으로 "ref"만 보낸 사람 → 릴스 댓글 안내(한 사람 한 번, 이미 링크 받은 사람은 건너뜀)."""
    handled = s.setdefault("dm_handled", {})
    convs = call(f"{B}/{uid}/conversations?platform=instagram&fields=participants,updated_time,messages.limit(1){{message,from,created_time}}&limit=25&access_token={tok}")["data"]
    for cv in convs:
        if cv.get("updated_time", "") < START:
            continue
        msgs = cv.get("messages", {}).get("data", [])
        if not msgs:
            continue
        m = msgs[0]
        user = m["from"].get("username", "")
        if user == me or m["id"] in handled or not DM_ONLY.match(m.get("message") or ""):
            continue
        if uk(user) in s["users"] or uk(user) in handled.values():
            handled[m["id"]] = uk(user)
            continue
        # 실패하면 처리함으로 적지 않고 다음 확인 때 다시 보낸다(3번까지, 11-16 스토리 답장 안내 실패)
        fails = s.setdefault("dm_fail", {})
        if fails.get(m["id"], 0) >= 3:
            continue
        try:
            call(f"{B}/{uid}/messages?access_token={tok}", {"recipient": {"id": m["from"]["id"]}, "message": {"text": DM_TO_COMMENT + s.get("latest_url", REEL_URL)}})
            print(uk(user), "DM ref → 댓글 안내")
            handled[m["id"]] = uk(user)
            fails.pop(m["id"], None)
        except RuntimeError as e:
            fails[m["id"]] = fails.get(m["id"], 0) + 1
            print(uk(user), "DM 안내 실패", fails[m["id"]], e)
        save(s)


def show():
    s = load()
    print("받은 사람", len(s["users"]))
    for cid, v in s["comments"].items():
        print(v.get("at", ""), v["user"], v["status"])


if __name__ == "__main__":
    {"run": run, "list": show}[sys.argv[1] if len(sys.argv) > 1 else "run"]()
