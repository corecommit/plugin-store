import json
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

from dotenv import load_dotenv
load_dotenv()

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
        print("❌  Invalid Discord user token — check your DISCORD_TOKEN secret.")
        sys.exit(1)
    if resp.status_code == 403:
        print("❌  Token lacks permission to read that channel.")
        sys.exit(1)
    if resp.status_code == 404:
        print("❌  Channel not found — double-check DISCORD_CHANNEL_ID.")
        sys.exit(1)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 5)
        print(f"⏳  Rate limited — waiting {retry_after}s…")
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
        print(f"  ⚠️  Failed to download {url}: {exc}")
        return False


def author_from_message(msg: dict) -> str:
    author = msg.get("author", {})
    return author.get("global_name") or author.get("username") or "unknown"


def main() -> None:
    channel_id   = os.environ["DISCORD_CHANNEL_ID"]
    repo         = os.environ.get("GITHUB_REPOSITORY", "unknown/plugin-store")
    last_msg_id  = os.environ.get("LAST_MESSAGE_ID", "").strip() or None

    PLUGINS_DIR.mkdir(exist_ok=True)

    print(f"🔍  Fetching messages from channel {channel_id}"
          f"{f' after {last_msg_id}' if last_msg_id else ' (first run)'}…")

    messages = fetch_messages(channel_id, last_msg_id)

    if not messages:
        print("✅  No new messages.")
        return

    entries          = load_manifest()
    existing_names   = {e["name"] for e in entries}
    added: list[str] = []
    new_last_id      = last_msg_id

    for msg in messages:
        msg_id   = msg["id"]
        msg_datetime = snowflake_to_datetime(msg_id)

        # Always advance the cursor, even if we skip the message
        new_last_id = msg_id

        # Skip messages sent before or on the cutoff
        if msg_datetime <= CUTOFF_DATE:
            print(f"  ⏭  Message {msg_id} ({msg_datetime.date()}) is on/before cutoff — skipping.")
            continue

        attachments = msg.get("attachments", [])
        iy_attachments = [a for a in attachments if a.get("filename", "").endswith(".iy")]

        if not iy_attachments:
            continue

        author = author_from_message(msg)

        for attachment in iy_attachments:
            raw_name = attachment.get("filename", "plugin.iy")
            filename = sanitize_filename(raw_name)

            if not filename.endswith(".iy"):
                filename += ".iy"

            if filename in existing_names:
                print(f"  ⚠️  {filename} already in plugins.json — skipping.")
                continue

            dest = PLUGINS_DIR / filename
            print(f"  ⬇️  Downloading {filename} (by {author}, {msg_datetime.date()})…")

            if not download_attachment(attachment["url"], dest):
                continue

            url = RAW_BASE.format(repo=repo, filename=filename)

            # Try to pull a description from the first 30 lines of the file
            description = ""
            try:
                text = dest.read_text(encoding="utf-8", errors="replace")
                import re
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

            entry = {
                "author":      author,
                "name":        filename,
                "description": description,
                "url":         url,
            }

            entries.insert(0, entry)
            existing_names.add(filename)
            added.append(filename)
            print(f"  ✅  Registered: {filename} by {author}")

    # Persist the updated manifest
    if added:
        save_manifest(entries)
        print(f"\n📝  plugins.json updated — {len(added)} plugin(s) added: {', '.join(added)}")
    else:
        print("✅  No new .iy plugins found in new messages.")

    # Tell the workflow the new cursor value via an env file
    if new_last_id and new_last_id != last_msg_id:
        env_file = os.environ.get("GITHUB_ENV", "")
        if env_file:
            with open(env_file, "a") as f:
                f.write(f"NEW_LAST_MESSAGE_ID={new_last_id}\n")
        print(f"📌  Cursor advanced to message {new_last_id}")


if __name__ == "__main__":
    main()
