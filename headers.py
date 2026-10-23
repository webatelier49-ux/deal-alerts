"""Give every deal channel a clear guide at the top: a pinned card explaining what is posted and how to read it,
plus a matching channel topic. Running it again updates the existing card instead of posting a new one.

Env: DISCORD_BOT_TOKEN (Manage Channels, Manage Messages), WEBHOOKS (same secret as deal_alerts.py)
"""

import os
import sys

import yaml

from cleanup import API, bot_token, call
from deal_alerts import CONFIG_PATH, DEFAULT_COLOR, load_secrets, webhooks_from_env

GUIDE_FOOTER = "Channel guide · Alternative Reselling"
HOW_TO_READ = (
    "**How every deal is checked**\n"
    "• Compared with current eBay prices for the same product\n"
    "• At least 30% and €15 below market, trusted sellers only (97%+)\n"
    "• Searched on 🇩🇪 eBay.de, 🇺🇸 eBay.com and 🇬🇧 eBay.co.uk\n\n"
    "**How to read a card**\n"
    "**Price**: what you pay · **eBay market**: typical price now · **You save / Profit**: your margin\n\n"
    "Prices move fast. Check sold listings before you buy. 💬 Talk deals in the chat channels."
)


def main():
    load_secrets()
    token = bot_token()
    if not token:
        sys.exit("DISCORD_BOT_TOKEN is missing")
    bot = {"Authorization": f"Bot {token}", "User-Agent": "DealAlertsHeaders (github actions, 1.0)"}
    me = call("GET", f"{API}/users/@me", headers=bot)
    if not me.ok:  # without the bot we can't find the old guides and would post duplicates
        sys.exit(f"Bot token rejected ({me.status_code}); update the DISCORD_BOT_TOKEN secret")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        info = yaml.safe_load(f).get("channel_info", {})
    for name, hook in webhooks_from_env().items():
        if name not in info:
            continue
        emoji, title, text = info[name]
        hook_info = call("GET", hook).json()
        channel_id = hook_info.get("channel_id")
        if not channel_id:
            print(f"#{name}: webhook not reachable, skipped")
            continue
        topic = f"{emoji} {text} Only checked deals, 30%+ below eBay market price."
        r = call("PATCH", f"{API}/channels/{channel_id}", headers=bot, json={"topic": topic[:1024]})
        print(f"#{name}: topic {'set' if r.ok else f'not set ({r.status_code})'}")

        embed = {
            "author": {"name": "ALTERNATIVE RESELLING"},
            "title": f"{emoji}  {title}",
            "description": f"{text}\n\n{HOW_TO_READ}",
            "color": DEFAULT_COLOR,
            "footer": {"text": GUIDE_FOOTER},
        }
        payload = {"username": "Deal Alerts", "embeds": [embed]}
        pins = call("GET", f"{API}/channels/{channel_id}/pins", headers=bot)
        old = [m for m in (pins.json() if pins.ok else [])
               if m.get("webhook_id") == hook_info.get("id")
               and any((e.get("footer") or {}).get("text") == GUIDE_FOOTER for e in m.get("embeds", []))]
        if old:
            r = call("PATCH", f"{hook}/messages/{old[0]['id']}", json=payload)
            print(f"#{name}: guide {'updated' if r.ok else f'not updated ({r.status_code})'}")
            continue
        r = call("POST", hook, params={"wait": "true"}, json=payload)
        if not r.ok:
            print(f"#{name}: guide not posted ({r.status_code})")
            continue
        p = call("PUT", f"{API}/channels/{channel_id}/pins/{r.json()['id']}", headers=bot)
        print(f"#{name}: guide posted{'' if p.ok else f', pin failed ({p.status_code})'}")


if __name__ == "__main__":
    main()
