#!/usr/bin/env python3
"""TikTok autopost: exchange auth code, refresh tokens, publish next video from queue/.

Secrets (env): TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET.
Token state is stored encrypted (AES-256, key derived from the client secret) in state/token.enc.
"""
import json, math, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "token.enc"
QUEUE = ROOT / "queue"
POSTED = ROOT / "posted"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
API = "https://open.tiktokapis.com"
REDIRECT_URI = CONFIG["redirect_uri"]
VIDEO_EXT = (".mp4", ".mov", ".webm")


def env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"Missing secret {name}. Add it in GitHub: Settings -> Secrets and variables -> Actions.")
    return v


def http(method, url, data=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"HTTP {e.code} from {url.split('?')[0]}: {body[:2000].decode('utf-8', 'replace')}")
        if raw:
            raise
        try:
            return json.loads(body)
        except ValueError:
            sys.exit(1)
    if raw:
        return body
    return json.loads(body) if body else {}


def api(path, token, payload):
    return http("POST", API + path, json.dumps(payload).encode(), {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    })


def check(resp, what):
    err = resp.get("error") or {}
    if err.get("code") not in (None, "ok"):
        sys.exit(f"{what} failed: {err.get('code')} - {err.get('message')} (log_id {err.get('log_id')})")
    return resp.get("data", {})


# ---------- token storage ----------
def _openssl(args, data):
    p = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-a", "-A",
                        "-pass", "env:TIKTOK_CLIENT_SECRET", *args], input=data, capture_output=True)
    if p.returncode != 0:
        sys.exit("Token encryption error: " + p.stderr.decode())
    return p.stdout


def save_tokens(tok):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_bytes(_openssl([], json.dumps(tok).encode()))


def load_tokens():
    if not STATE.exists():
        sys.exit("TikTok is not connected yet. Run the 'Connect TikTok' workflow first.")
    return json.loads(_openssl(["-d"], STATE.read_bytes()))


def token_request(fields):
    fields.update(client_key=env("TIKTOK_CLIENT_KEY"), client_secret=env("TIKTOK_CLIENT_SECRET"))
    resp = http("POST", API + "/v2/oauth/token/", urllib.parse.urlencode(fields).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"})
    if "access_token" not in resp:
        sys.exit(f"Token request failed: {resp.get('error')} - {resp.get('error_description')}")
    resp["obtained_at"] = int(time.time())
    return resp


def connect(code):
    code = urllib.parse.unquote(code.strip())
    tok = token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI})
    save_tokens(tok)
    print(f"Connected. Scopes: {tok.get('scope')}. Refresh token valid for {tok.get('refresh_expires_in', 0)//86400} days.")


def fresh_access_token():
    old = load_tokens()
    tok = token_request({"grant_type": "refresh_token", "refresh_token": old["refresh_token"]})
    save_tokens(tok)  # refresh token may rotate
    return tok["access_token"]


# ---------- publishing ----------
RELEASE_TAG = "queue"
GH_API = "https://api.github.com"


def gh(method, path):
    token = os.environ.get("GITHUB_TOKEN", "")
    return http(method, GH_API + path, None, {
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "User-Agent": "autopost"}, raw=(method == "DELETE"))


def release_assets():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not os.environ.get("GITHUB_TOKEN"):
        return []
    try:
        rel = gh("GET", f"/repos/{repo}/releases/tags/{RELEASE_TAG}")
    except SystemExit:
        return []
    return rel.get("assets", []) if isinstance(rel, dict) else []


def next_video():
    """Return (name, source) of the next video: local file in queue/ or asset in release 'queue'."""
    items = []
    if QUEUE.exists():
        items += [(p.name, ("local", p)) for p in QUEUE.iterdir() if p.suffix.lower() in VIDEO_EXT]
    assets = release_assets()
    by_name = {a["name"]: a for a in assets}
    for a in assets:
        if Path(a["name"]).suffix.lower() in VIDEO_EXT:
            txt = by_name.get(Path(a["name"]).stem + ".txt")
            items.append((a["name"], ("release", a, txt)))
    items.sort(key=lambda x: x[0].lower())
    return items[0] if items else None


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "autopost"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while True:
            block = r.read(8 * 1024 * 1024)
            if not block:
                break
            f.write(block)


def prepare(item):
    """Return (video_path, caption)."""
    name, src = item
    if src[0] == "local":
        video = src[1]
        txt = video.with_suffix(".txt")
        caption = txt.read_text(encoding="utf-8").strip() if txt.exists() else CONFIG.get("default_caption", "")
        return video, caption[:2200]
    _, asset, txt_asset = src
    tmp = ROOT / ".tmp"
    tmp.mkdir(exist_ok=True)
    video = tmp / asset["name"]
    print(f"Downloading {asset['name']} from release '{RELEASE_TAG}' ({asset['size']/1024/1024:.1f} MB)...")
    download(asset["browser_download_url"], video)
    caption = CONFIG.get("default_caption", "")
    if txt_asset:
        t = tmp / txt_asset["name"]
        download(txt_asset["browser_download_url"], t)
        caption = t.read_text(encoding="utf-8-sig").strip()
    return video, caption[:2200]


def finish(item, video):
    name, src = item
    POSTED.mkdir(exist_ok=True)
    if src[0] == "local":
        for p in (video, video.with_suffix(".txt")):
            if p.exists():
                p.rename(POSTED / p.name)
        print(f"Done: {name} moved to posted/")
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    for a in (src[1], src[2]):
        if a:
            gh("DELETE", f"/repos/{repo}/releases/assets/{a['id']}")
    with open(POSTED / "released.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}  {name}\n")
    print(f"Done: {name} removed from release '{RELEASE_TAG}' and logged in posted/released.log")


def upload(url, path, size, chunk_size, total):
    ctype = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}[path.suffix.lower()]
    with open(path, "rb") as f:
        for i in range(total):
            start = i * chunk_size
            end = size - 1 if i == total - 1 else start + chunk_size - 1
            f.seek(start)
            chunk = f.read(end - start + 1)
            http("PUT", url, chunk, {"Content-Type": ctype, "Content-Length": str(len(chunk)),
                                     "Content-Range": f"bytes {start}-{end}/{size}"}, raw=True)
            print(f"Uploaded chunk {i+1}/{total}")


def publish():
    item = next_video()
    if not item:
        print("Queue is empty, nothing to publish.")
        return
    token = fresh_access_token()
    creator = check(api("/v2/post/publish/creator_info/query/", token, {}), "Creator info")
    print(f"Account: {creator.get('creator_nickname')} (@{creator.get('creator_username')}), "
          f"max video duration {creator.get('max_video_post_duration_sec')} s")
    video, caption = prepare(item)
    options = creator.get("privacy_level_options", [])
    privacy = CONFIG["privacy_level"]
    if privacy not in options:
        print(f"Privacy '{privacy}' not allowed now (allowed: {options}); using SELF_ONLY.")
        privacy = "SELF_ONLY"

    size = video.stat().st_size
    MB = 1024 * 1024
    if size <= 64 * MB:
        chunk_size, total = size, 1
    else:
        chunk_size = 10 * MB
        total = size // chunk_size
    post_info = {
        "title": caption,
        "privacy_level": privacy,
        "disable_comment": CONFIG.get("disable_comment", False) or creator.get("comment_disabled", False),
        "disable_duet": CONFIG.get("disable_duet", False) or creator.get("duet_disabled", False),
        "disable_stitch": CONFIG.get("disable_stitch", False) or creator.get("stitch_disabled", False),
        "video_cover_timestamp_ms": CONFIG.get("cover_timestamp_ms", 1000),
    }
    data = check(api("/v2/post/publish/video/init/", token, {
        "post_info": post_info,
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
                        "total_chunk_count": total},
    }), "Publish init")
    publish_id = data["publish_id"]
    print(f"Publishing {video.name} ({size/MB:.1f} MB), publish_id={publish_id}")
    upload(data["upload_url"], video, size, chunk_size, total)

    status = "?"
    for _ in range(60):
        time.sleep(10)
        st = check(api("/v2/post/publish/status/fetch/", token, {"publish_id": publish_id}), "Status")
        status = st.get("status")
        print("Status:", json.dumps(st, ensure_ascii=False))
        if status == "PUBLISH_COMPLETE":
            break
        if status == "FAILED":
            sys.exit(f"TikTok rejected the video: {st.get('fail_reason')}")
    if status not in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
        print("Still processing on TikTok side; it should appear shortly.")

    finish(item, video)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "connect":
        connect(os.environ.get("AUTH_CODE") or sys.argv[2])
    elif cmd == "publish":
        publish()
    else:
        sys.exit("usage: tiktok.py connect CODE | publish")
