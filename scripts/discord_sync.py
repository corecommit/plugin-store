import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import websockets

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_API   = "https://discord.com/api/v10"
PLUGINS_DIR   = Path("plugins")
MANIFEST_FILE = Path("plugins.json")
RAW_BASE      = "https://raw.githubusercontent.com/{repo}/main/plugins/{filename}"
CUTOFF_DATE = datetime(2026, 5, 29, 0, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
# ─────────────────────────────────────────────────────────────────────────────


def discord_headers() -> dict:
    token = os.environ["DISCORD_TOKEN"]
    return {"Authorization": token}


def snowflake_to_datetime(snowflake: str) -> datetime:
    ts_ms = (int(snowflake) >> 22) + 1420070400000
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

def fetch_messages(channel_id: str, after: str | None) -> list[dict]:
    params: dict = {"limit": 100}
    if after:
        params["after"] = after

    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    resp = requests.get(url, headers=discord_headers(), params=params, timeout=15)

    if resp.status_code == 401:
        print("[err] Invalid Discord user token — check your DISCORD_TOKEN secret.")
        sys.exit(1)
    if resp.status_code == 403:
        print("[err] Token lacks permission to read that channel.")
        sys.exit(1)
    if resp.status_code == 404:
        print("[err] Channel not found — double-check DISCORD_CHANNEL_ID.")
        sys.exit(1)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 5)
        print(f"[rate] Rate limited — waiting {retry_after}s...")
        time.sleep(float(retry_after) + 1)
        return fetch_messages(channel_id, after)

    resp.raise_for_status()

    # Discord returns newest-first; reverse so we process oldest → newest
    return list(reversed(resp.json()))


def load_manifest() -> list[dict]:
    if not MANIFEST_FILE.exists():
        return []
    with MANIFEST_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_manifest(entries: list[dict]) -> None:
    with MANIFEST_FILE.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sanitize_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "._- ")
    return safe.strip()


def download_attachment(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except requests.RequestException as exc:
        print(f"  [warn] Failed to download {url}: {exc}")
        return False


def author_from_message(msg: dict) -> str:
    author = msg.get("author", {})
    return author.get("global_name") or author.get("username") or "unknown"

def process_message(msg: dict, entries: list, existing_names: set, repo: str) -> str | None:
    msg_id = msg["id"]
    msg_datetime = snowflake_to_datetime(msg_id)

    if msg_datetime <= CUTOFF_DATE:
        print(f"  [skip] Message {msg_id} ({msg_datetime.date()}) is on/before cutoff — skipping.")
        return None

    attachments = msg.get("attachments", [])
    iy_attachments = [a for a in attachments if a.get("filename", "").endswith(".iy")]
    if not iy_attachments:
        return None

    author = author_from_message(msg)

    for attachment in iy_attachments:
        raw_name = attachment.get("filename", "plugin.iy")
        filename = sanitize_filename(raw_name)
        if not filename.endswith(".iy"):
            filename += ".iy"

        if filename in existing_names:
            existing = next(e for e in entries if e["name"] == filename)
            existing_msg_id = int(existing.get("message_id", 0))
            if int(msg_id) <= existing_msg_id:
                print(f"  [skip] {filename} already up to date — skipping.")
                continue
            print(f"  [upd] {filename} has a newer version — overwriting...")
            entries[:] = [e for e in entries if e["name"] != filename]
            existing_names.discard(filename)

        dest = PLUGINS_DIR / filename
        print(f"  [dl]  Downloading {filename} (by {author}, {msg_datetime.date()})...")
        if not download_attachment(attachment["url"], dest):
            continue

        url = RAW_BASE.format(repo=repo, filename=filename)
        description = ""
        try:
            text = dest.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[:30]:
                stripped = line.strip().lstrip("-").lstrip("#").strip()
                m = re.match(r"@desc(?:ription)?\s+(.+)", stripped, re.IGNORECASE)
                if m:
                    description = m.group(1).strip()
                    break
        except OSError:
            pass
        if not description:
            description = f"{filename} plugin for Infinite Yield"

        entries.insert(0, {
            "author": author, "name": filename, "description": description,
            "url": url, "message_id": msg_id,
        })
        existing_names.add(filename)
        print(f"  [ok]  Registered: {filename} by {author}")
        return filename
    return None


def main() -> None:
    channel_id   = os.environ["DISCORD_CHANNEL_ID"]
    repo         = os.environ.get("GITHUB_REPOSITORY", "unknown/plugin-store")
    cursor_file = Path(".last_message_id")
    last_msg_id = cursor_file.read_text().strip() if cursor_file.exists() else None

    PLUGINS_DIR.mkdir(exist_ok=True)

    print(f"Fetching messages from channel {channel_id}"
          f"{f' after {last_msg_id}' if last_msg_id else ' (first run)'}…")

    messages = fetch_messages(channel_id, last_msg_id)
    if not messages:
        print("[ok] No new messages.")
        return

    entries        = load_manifest()
    existing_names = {e["name"] for e in entries}
    new_last_id    = last_msg_id
    added: list[str] = []

    for msg in messages:
        new_last_id = msg["id"]
        name = process_message(msg, entries, existing_names, repo)
        if name:
            added.append(name)

    if added:
        save_manifest(entries)
        print(f"\n[manifest] plugins.json updated — {len(added)} plugin(s) added: {', '.join(added)}")
    else:
        print("[ok] No new .iy plugins found in new messages.")

    if new_last_id and new_last_id != last_msg_id:
        Path(".last_message_id").write_text(new_last_id)
        print(f"[cursor] Cursor advanced to message {new_last_id}")


async def listen_forever():
    channel_id = os.environ["DISCORD_CHANNEL_ID"]
    repo       = os.environ.get("GITHUB_REPOSITORY", "unknown/plugin-store")
    token      = os.environ["DISCORD_TOKEN"]
    cursor_file = Path(".last_message_id")

    print("[listen] Starting Gateway listener…")
    while True:
        try:
            async with websockets.connect("wss://gateway.discord.gg/?v=10&encoding=json") as ws:
                hello = json.loads(await ws.recv())
                hb_interval = hello["d"]["heartbeat_interval"] / 1000

                await ws.send(json.dumps({
                    "op": 2, "d": {
                        "token": token,
                        "properties": {"$os": "linux", "$browser": "opencode", "$device": "opencode"},
                        "capabilities": 125,
                    }
                }))

                ready = json.loads(await ws.recv())
                print(f"[ok] Connected as {ready['d']['user']['username']}")

                async def heartbeat():
                    while True:
                        await asyncio.sleep(hb_interval)
                        await ws.send(json.dumps({"op": 1, "d": None}))

                hb = asyncio.create_task(heartbeat())
                last_id = cursor_file.read_text().strip() if cursor_file.exists() else None

                async for raw in ws:
                    p = json.loads(raw)
                    op = p.get("op")
                    if op == 0 and p.get("t") == "MESSAGE_CREATE":
                        msg = p["d"]
                        if msg.get("channel_id") != channel_id:
                            continue
                        msg_id = msg["id"]
                        if last_id and int(msg_id) <= int(last_id):
                            continue
                        iy = [a for a in msg.get("attachments", []) if a.get("filename", "").endswith(".iy")]
                        if not iy:
                            continue
                        print(f"\n[+] New plugin message {msg_id} — {iy[0]['filename']}")
                        PLUGINS_DIR.mkdir(exist_ok=True)
                        entries = load_manifest()
                        existing_names = {e["name"] for e in entries}
                        name = process_message(msg, entries, existing_names, repo)
                        if name:
                            save_manifest(entries)
                            cursor_file.write_text(msg_id)
                            print(f"[ok] Synced — plugins.json updated")
                            last_id = msg_id
                    elif op == 7:
                        print("[warn] Gateway requested reconnect")
                        break
                    elif op == 9:
                        print("[warn] Invalid session — reconnecting...")
                        break
                hb.cancel()
        except Exception as e:
            print(f"[warn] Gateway error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    if "--listen" in sys.argv:
        asyncio.run(listen_forever())
    else:
        main()
