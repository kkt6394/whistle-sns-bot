"""인스타 스토리를 Graph API로 올린다 — Business Suite 예약 스토리가 계속 실패해서(11-08~10) 대신 쓴다.

순서표: docs/release/sns/story-queue.json  [{"at": "2026-11-12T18:00"(KST), "label", "images": [저장소 경로…]}]
  python3 scripts/post_story.py upload      순서표 그림을 Pages 저장소 story-media 가지 story/<MMDD>/에 올림(있으면 건너뜀)
  python3 scripts/post_story.py due         시각이 지났고 아직 안 올린 줄을 올림(6시간 넘게 지난 건 건너뛰고 알림) — launchd가 15분마다
  python3 scripts/post_story.py now <번호>   그 줄을 지금 올림(0부터)
  python3 scripts/post_story.py list        순서표와 올린 상태
"manual": true 줄은 올리지 않고 그 시각에 맥 알림 + 그림을 연다(퀴즈 스티커는 API 미지원 → 폰 앱에서 직접, "sticker"에 보기·정답).
"kind": "carousel"(images 여러 장, JPG) 또는 "reel"(images에 mp4 하나) 줄은 피드 글로 올린다 — "caption" 필수(11-12, 한국 계정 피드).
"account": "kr" 줄은 한국어 계정 @whistlepro_kr로 올린다(없으면 영어 계정 @whistlepro.app).
올린 기록은 ~/.local/share/whistle/story-posted.json. 토큰은 ~/.config/whistle/meta.json
(영어: ig_user_id·ig_token, 다른 계정: "accounts": {"kr": {"ig_user_id", "ig_token"}}).
설치: bash scripts/install_story_job.sh
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = next(p for p in [HERE / "story-queue.json", ROOT / "docs/release/sns/story-queue.json"] if p.exists())
# 클라우드(GitHub Actions, whistle-sns-bot)에서는 WHISTLE_STATE_DIR로 저장소 안 state/를 쓴다(11-16)
STATE = Path(os.environ.get("WHISTLE_STATE_DIR", Path.home() / ".local/share/whistle")) / "story-posted.json"
ALERTS = []
REPO, BRANCH = "kkt6394/kkt6394.github.io", "story-media"
STALE = timedelta(hours=6)


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def when(item):
    return datetime.fromisoformat(item["at"]).replace(tzinfo=KST)


def remote_path(item, img):
    return f"story/{when(item):%m%d}/{Path(img).name}"


def raw_url(item, img):
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{remote_path(item, img)}"


def key(item):
    return f'{item["at"]} {item["label"]}'


def notify(msg, fail=True):
    """맥: 알림 센터. 클라우드: 경고로 찍고, fail이면 실행을 실패로 끝내 GitHub 메일이 가게 한다."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "인스타 스토리"'], check=False)
    else:
        print(f"::warning::{msg}", file=sys.stderr)
    if fail:
        ALERTS.append(msg)


def upload():
    for item in load(QUEUE, []):
        for img in item["images"]:
            rp = remote_path(item, img)
            ok = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{rp}?ref={BRANCH}", "--silent"], capture_output=True).returncode == 0
            if ok:
                continue
            body = json.dumps({"message": f"story: {rp}", "branch": BRANCH,
                               "content": base64.b64encode((ROOT / img).read_bytes()).decode()})
            subprocess.run(["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/{rp}", "--input", "-", "--silent"],
                           input=body.encode(), check=True)
            print("올림", rp)
    print("그림 준비 끝")


def _call(url, data=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"User-Agent": "WhistleStory/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()[:300]) from None


def creds(item):
    """그 줄을 올릴 계정의 (ig_user_id, 토큰). account가 없거나 "en"이면 영어 계정"""
    c = json.loads((Path.home() / ".config/whistle/meta.json").read_text())
    acct = item.get("account", "en")
    if acct != "en":
        c = c.get("accounts", {}).get(acct)
        if not c:
            raise RuntimeError(f"meta.json에 {acct} 계정 토큰이 없음")
        # 한국 계정은 '김기태' 사용자 토큰(60일, 포트폴리오에 못 넣어서) — 만료 10일 전부터 알림
        left = (c.get("expires_at") or 0) - time.time()
        if c.get("expires_at") and left < 10 * 86400:
            notify(f"{acct} 계정 토큰 {max(0, int(left // 86400))}일 남음 — 액세스 토큰 디버거에서 연장", fail=False)
    return c["ig_user_id"], c["ig_token"]


def _wait(base, cid, tok, label, tries=60, gap=5):
    for _ in range(tries):
        st = _call(f"{base}/{cid}?fields=status_code&access_token={tok}")["status_code"]
        if st == "FINISHED":
            return
        if st == "ERROR":
            raise RuntimeError(f"{label} 처리 실패")
        time.sleep(gap)
    raise RuntimeError(f"{label} 처리 시간 초과")


def publish_feed(item, base, uid, tok):
    """피드 캐러셀·릴스 (11-12)"""
    if item["kind"] == "reel":
        cid = _call(f"{base}/{uid}/media", {"media_type": "REELS", "video_url": raw_url(item, item["images"][0]),
                                            "caption": item["caption"], "share_to_feed": "true", "access_token": tok})["id"]
        _wait(base, cid, tok, item["label"], tries=90)
    else:
        kids = [_call(f"{base}/{uid}/media", {"image_url": raw_url(item, img), "is_carousel_item": "true",
                                              "access_token": tok})["id"] for img in item["images"]]
        cid = _call(f"{base}/{uid}/media", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                            "caption": item["caption"], "access_token": tok})["id"]
        _wait(base, cid, tok, item["label"])
    return [_call(f"{base}/{uid}/media_publish", {"creation_id": cid, "access_token": tok})["id"]]


def publish(item):
    base = "https://graph.facebook.com/v21.0"
    uid, tok = creds(item)
    if item.get("kind") in ("carousel", "reel"):
        return publish_feed(item, base, uid, tok)
    ids = []
    for img in item["images"]:  # 장마다 따로 올린다(스토리는 한 장씩)
        cid = _call(f"{base}/{uid}/media", {"image_url": raw_url(item, img), "media_type": "STORIES", "access_token": tok})["id"]
        for _ in range(30):
            st = _call(f"{base}/{cid}?fields=status_code&access_token={tok}")["status_code"]
            if st == "FINISHED":
                break
            if st == "ERROR":
                raise RuntimeError(f"{img} 처리 실패")
            time.sleep(4)
        ids.append(_call(f"{base}/{uid}/media_publish", {"creation_id": cid, "access_token": tok})["id"])
    return ids


def mark(item, ids, note="posted"):
    s = load(STATE, {})
    s[key(item)] = {"status": note, "ids": ids, "at": datetime.now(KST).isoformat(timespec="minutes")}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def already_up(item):
    """Business Suite 예약(지워지지 않음, 11-10)이 그 시각에 성공해 올라갔으면 겹치지 않게 건너뛴다."""
    uid, tok = creds(item)
    ours = {i for v in load(STATE, {}).values() for i in v.get("ids", [])}
    edge = "media" if item.get("kind") in ("carousel", "reel") else "stories"  # 피드는 사용자가 직접 올렸는지 본다
    data = _call(f"https://graph.facebook.com/v21.0/{uid}/{edge}?fields=id,timestamp&limit=10&access_token={tok}")["data"]
    t0 = when(item) - timedelta(minutes=15 if item.get("replace_old") else 5)
    return [s["id"] for s in data if s["id"] not in ours
            and datetime.strptime(s["timestamp"], "%Y-%m-%dT%H:%M:%S%z") >= t0]


def replace_old(item, dup):
    """Business Suite 옛 예약(삭제가 안 됨, 11-17)이 먼저 올라갔으면 지우고 새 판을 올린다.
    지우는 건 그 시각 이후 올라온 우리 것 아닌 게시물 중 글 첫머리가 replace_old와 같은 것만 (Meta IG 미디어 삭제 API, 2025-12)."""
    uid, tok = creds(item)
    gone = []
    for mid in dup:
        cap = _call(f"https://graph.facebook.com/v21.0/{mid}?fields=caption&access_token={tok}").get("caption") or ""
        if cap.startswith(item["replace_old"]):
            req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{mid}?access_token={tok}", method="DELETE",
                                         headers={"User-Agent": "WhistleStory/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    r.read()
            except urllib.error.HTTPError as e:  # 토큰에 instagram_manage_contents가 없으면 여기로 — 옛 판을 두고 건너뛴다
                print(f"옛 판 삭제 실패 {item['label']} {mid}: {e.read().decode()[:150]}")
                continue
            print("옛 판 지움", item["label"], mid)
            gone.append(mid)
    return gone


def run(item, check=True):
    try:
        dup = already_up(item) if check else []
        if dup and item.get("replace_old"):
            gone = replace_old(item, dup)
            dup = [d for d in dup if d not in gone]
            if dup:  # 옛 판을 못 지움 → 사용자가 앱에서 지울 때까지 5분마다 다시 보고, 지워지면 새 판을 올린다
                s, wk = load(STATE, {}), key(item) + " :old-waiting"
                if wk not in s:
                    notify(f"옛 판이 올라옴 — 인스타 앱에서 지우면 새 판을 올림: {item['label']}")
                    s[wk] = {"status": "waiting", "ids": [], "at": datetime.now(KST).isoformat(timespec="minutes")}
                    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))
                print("옛 판 지워지길 기다림", item["label"], dup)
                return False
        if dup:
            mark(item, dup, "business-suite")
            print("Business Suite 예약이 올라가 있어 건너뜀", item["label"], dup)
            return True
        ids = publish(item)
    except Exception as e:  # noqa: BLE001
        notify(f"실패: {item['label']} — {e}")
        print("실패", item["label"], e, file=sys.stderr)
        return False
    mark(item, ids)
    print(datetime.now(KST).strftime("%m-%d %H:%M"), "올림", item["label"], ids)
    return True


def due():
    now, s = datetime.now(KST), load(STATE, {})
    for item in load(QUEUE, []):
        if key(item) in s or when(item) > now:
            continue
        if item.get("manual"):  # 퀴즈 스티커는 API로 못 붙여서(Meta 문서) 알림만 — 폰 앱에서 직접 올린다
            notify(f"{item['label']} · 스티커: {item.get('sticker', '')}")
            if sys.platform == "darwin":
                subprocess.run(["open", raw_url(item, item["images"][0])], check=False)
            mark(item, [], "notified")
            continue
        if now - when(item) > STALE:
            mark(item, [], "skipped-stale")
            notify(f"시간이 너무 지나 건너뜀: {item['label']}")
            continue
        run(item)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    q = load(QUEUE, [])
    if cmd == "upload":
        upload()
    elif cmd == "due":
        due()
        sys.exit(1 if ALERTS else 0)
    elif cmd == "now":
        sys.exit(0 if run(q[int(sys.argv[2])], check=False) else 1)
    else:
        s = load(STATE, {})
        for i, item in enumerate(q):
            print(i, item["at"], item.get("account", "en"), item["label"], s.get(key(item), {}).get("status", "대기"))


if __name__ == "__main__":
    main()
