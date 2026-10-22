"""Delete old bot posts from every deal channel, so only posts made with the current quality filter remain.

Only unpinned webhook posts are touched; member messages, bot messages and pinned posts stay. "Old" means posted before
quality_since in state.json (set the first time the stricter filter ran), or before CUTOFF if given.

Env:
  DISCORD_BOT_TOKEN   bot in the server with "Read Message History" (and "Manage Messages" for
                      posts by webhooks that no longer exist)
  WEBHOOKS            same secret as deal_alerts.py, tells which channels to clean
  CUTOFF              optional ISO time; default: quality_since from state.json
  DRY_RUN=1           only count what would be deleted
"""

import json
import os
import sys
import time

import requests

from deal_alerts import STATE_PATH, load_secrets, webhooks_from_env

API = "https://discord.com/api/v10"


def call(method, url, **kw):
    for _ in range(5):
        r = requests.request(method, url, timeout=30, **kw)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.2)
            continue
        return r
    return r


def main():
    load_secrets()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("DISCORD_BOT_TOKEN is missing")
    bot = {"Authorization": f"Bot {token}", "User-Agent": "DealAlertsCleanup (github actions, 1.0)"}
    dry = os.environ.get("DRY_RUN") == "1"
    cutoff = os.environ.get("CUTOFF", "").strip()
    if not cutoff:
        with open(STATE_PATH, encoding="utf-8") as f:
            cutoff = json.load(f).get("quality_since", "")
    if not cutoff:
        sys.exit("No cutoff: run deal_alerts.py once first or set CUTOFF")
    print(f"Deleting bot posts older than {cutoff}{' (dry run)' if dry else ''}")

    total = 0
    for name, hook in webhooks_from_env().items():
        info = call("GET", hook).json()
        channel_id, hook_id = info.get("channel_id"), info.get("id")
        if not channel_id:
            print(f"#{name}: webhook not reachable, skipped")
            continue
        deleted, before = 0, None
        while True:
            params = {"limit": 100}
            if before:
                params["before"] = before
            r = call("GET", f"{API}/channels/{channel_id}/messages", headers=bot, params=params)
            if not r.ok:
                print(f"#{name}: can't read messages ({r.status_code}: {r.text[:120]})")
                break
            msgs = r.json()
            if not msgs:
                break
            before = msgs[-1]["id"]
            for m in msgs:
                if m["timestamp"] >= cutoff or not m.get("webhook_id") or m.get("pinned"):
                    continue  # keep new posts, member and bot messages (e.g. channel intros), pinned posts
                if dry:
                    deleted += 1
                    continue
                if m.get("webhook_id") == hook_id:  # own webhook posts need no extra permission
                    d = call("DELETE", f"{hook}/messages/{m['id']}")
                else:
                    d = call("DELETE", f"{API}/channels/{channel_id}/messages/{m['id']}", headers=bot)
                if d.ok:
                    deleted += 1
                else:
                    print(f"#{name}: couldn't delete {m['id']} ({d.status_code})")
        print(f"#{name}: {deleted} old posts {'would be ' if dry else ''}deleted")
        total += deleted
    print(f"Total: {total}")


if __name__ == "__main__":
    main()
