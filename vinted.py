"""#vinted-finds: members post what they found on Vinted, the bot answers with the eBay market price.

Vinted itself is never read (its terms forbid automated access). The bot only reads what members write
in the channel, e.g. "Ralph Lauren cable knit jumper M 18€", and checks that product on eBay.de.

On first use it creates the channel (next to the clothing channels), a webhook and a pinned guide that
explains how to set up Vinted's own saved-search alerts.

Needs DISCORD_BOT_TOKEN with Manage Channels, Manage Webhooks, Manage Messages and the
"Message Content Intent" switched on in the Discord developer portal (otherwise message text is empty).
"""

import os
import re
import statistics
import sys

import requests

from cleanup import API, bot_token, call
from deal_alerts import (DEFAULT_COLOR, guild_id_for, item_query, now, sold_link, webhook_for)

CHANNEL_NAME = "vinted-finds"
NEAR_CHANNEL = "clothing-deals"   # the new channel goes into this channel's category
PRICE_RE = re.compile(r"(?:€\s*(\d{1,4}(?:[.,]\d{1,2})?)|(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euro\b))", re.I)
URL_RE = re.compile(r"https?://\S+")

GUIDE = (
    "Found something on Vinted? Post it here and the bot checks it against eBay.\n\n"
    "**How to post**\n"
    "Write the item and the price, e.g. `Ralph Lauren cable knit jumper M 18€`.\n"
    "Brand + model works best. Add a Vinted link or photo if you like.\n"
    "Within 20 minutes you get the eBay market price, the estimated profit and a link to recent sales.\n\n"
    "**Get Vinted alerts 24/7 (in the Vinted app)**\n"
    "1. Search a brand, e.g. *Ralph Lauren*\n"
    "2. Set filters: category, size, condition and a max price\n"
    "3. Tap **Save search**\n"
    "4. Turn on notifications for saved searches in your Vinted settings\n"
    "Now every new listing that matches pings your phone.\n\n"
    "**Good starting searches (max price)**\n"
    "{searches}"
)
GUIDE_FOOTER = "Channel guide · Alternative Reselling"


def bot_headers():
    token = bot_token()
    return {"Authorization": f"Bot {token}", "User-Agent": "DealAlertsVinted (github actions, 1.0)"} if token else None


def setup(cfg, state, bot):
    """Find or create the channel, its webhook and the pinned guide. Returns (channel_id, webhook_url)."""
    v = state.setdefault("vinted", {})
    if v.get("channel_id") and v.get("hook"):
        return v["channel_id"], v["hook"]
    near_hook = webhook_for(NEAR_CHANNEL)
    if not near_hook:
        return None, None
    near = call("GET", near_hook).json()
    guild = guild_id_for(near_hook)
    channels = call("GET", f"{API}/guilds/{guild}/channels", headers=bot)
    if not channels.ok:
        print(f"Vinted: can't list channels ({channels.status_code})", file=sys.stderr)
        return None, None
    channel = next((c for c in channels.json() if c.get("type") == 0 and c["name"].endswith(CHANNEL_NAME)), None)
    if not channel:
        parent = next((c.get("parent_id") for c in channels.json() if c["id"] == near.get("channel_id")), None)
        r = call("POST", f"{API}/guilds/{guild}/channels", headers=bot, json={
            "name": f"🔎・{CHANNEL_NAME}", "type": 0, "parent_id": parent,
            "topic": "🔎 Post your Vinted finds with the price. The bot checks them against eBay and shows the profit."})
        if not r.ok:
            print(f"Vinted: can't create channel ({r.status_code}: {r.text[:120]})", file=sys.stderr)
            return None, None
        channel = r.json()
        print(f"Vinted: created #{channel['name']}")
    hooks = call("GET", f"{API}/channels/{channel['id']}/webhooks", headers=bot)
    hook = next((h for h in (hooks.json() if hooks.ok else []) if h.get("token")), None)
    if not hook:
        r = call("POST", f"{API}/channels/{channel['id']}/webhooks", headers=bot, json={"name": "Deal Alerts"})
        if not r.ok:
            print(f"Vinted: can't create webhook ({r.status_code})", file=sys.stderr)
            return None, None
        hook = r.json()
    url = f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"

    lines = [f"• {s['name']}: under €{round(s['ref_price'] * 0.5)}" for s in cfg["searches"]
             if s["channel"] in cfg.get("vinted_channels", []) and s.get("ref_price")]
    embed = {"author": {"name": "ALTERNATIVE RESELLING"}, "title": "🔎  Vinted Finds",
             "description": GUIDE.format(searches="\n".join(lines))[:4000],
             "color": DEFAULT_COLOR, "footer": {"text": GUIDE_FOOTER}}
    msg = call("POST", url, params={"wait": "true"}, json={"username": "Deal Alerts", "embeds": [embed]})
    if msg.ok:
        call("PUT", f"{API}/channels/{channel['id']}/pins/{msg.json()['id']}", headers=bot)
        v["last_id"] = msg.json()["id"]
    v.update(channel_id=channel["id"], hook=url)
    return channel["id"], url


def parse_find(text):
    """(price, product words) from a member message, or None."""
    m = PRICE_RE.search(text)
    if not m:
        return None
    price = float((m.group(1) or m.group(2)).replace(",", "."))
    words = URL_RE.sub(" ", PRICE_RE.sub(" ", text))
    query = item_query(words)
    return (price, query) if price > 0 and len(query.split()) >= 2 else None


def check_embed(ebay, price, query, vc):
    try:
        prices = ebay.market_prices(query, new_only=False)
    except requests.RequestException as e:
        print(f"Vinted: eBay lookup '{query}' failed: {e}", file=sys.stderr)
        return None
    sold = sold_link(query)
    if len(prices) < vc.get("min_comparables", 5):
        return {"title": f"🔎 {query}", "color": DEFAULT_COLOR,
                "description": f"Not enough eBay listings to judge this one. [Check recent sales]({sold})"}
    market = statistics.median(prices)
    cost = price * (1 + vc.get("buyer_fee_pct", 0.05)) + vc.get("buyer_fee_fixed", 0.70)
    profit = market * (1 - vc.get("ebay_fee", 0.13)) - vc.get("shipping_cost", 5) - cost
    if profit >= vc.get("good_profit", 15):
        verdict = "✅ Good find"
    elif profit > 0:
        verdict = "⚠️ Small margin"
    else:
        verdict = "❌ Not worth flipping"
    embed = {
        "author": {"name": f"{verdict} · eBay check"},
        "title": query[:256],
        "url": sold,
        "color": DEFAULT_COLOR,
        "fields": [
            {"name": "Vinted price", "value": f"**€{price:.2f}** (~€{cost:.2f} with buyer fee)", "inline": True},
            {"name": "eBay market", "value": f"~€{market:.0f} ({len(prices)} listings)", "inline": True},
            {"name": "Est. profit", "value": f"~€{profit:.0f}", "inline": True},
            {"name": "📊 Demand", "value": f"[See what sold recently]({sold})", "inline": False},
        ],
        "footer": {"text": f"After ~{vc.get('ebay_fee', 0.13):.0%} eBay fees and €{vc.get('shipping_cost', 5)} "
                           "shipping · Estimate from current asking prices"},
        "timestamp": now().isoformat(),
    }
    if getattr(ebay, "last_image", None):
        embed["thumbnail"] = {"url": ebay.last_image}
    return embed


def run_vinted_finds(ebay, cfg, state):
    bot = bot_headers()
    vc = cfg.get("vinted_finds") or {}
    if not bot or not vc.get("enabled") or ebay is None:
        return 0
    channel_id, hook = setup(cfg, state, bot)
    if not channel_id:
        return 0
    v = state["vinted"]
    params = {"limit": 50}
    if v.get("last_id"):
        params["after"] = v["last_id"]
    r = call("GET", f"{API}/channels/{channel_id}/messages", headers=bot, params=params)
    if r.status_code == 404:  # channel was deleted: set it up again next round
        state.pop("vinted", None)
        return 0
    if not r.ok:
        print(f"Vinted: can't read messages ({r.status_code})", file=sys.stderr)
        return 0
    answered = 0
    for m in sorted(r.json(), key=lambda m: int(m["id"])):
        v["last_id"] = m["id"]
        if m.get("webhook_id") or m["author"].get("bot") or answered >= vc.get("max_per_run", 10):
            continue
        find = parse_find(m.get("content", ""))
        if not find:
            continue
        embed = check_embed(ebay, *find, vc)
        if embed:
            call("POST", hook, json={"username": "Deal Alerts", "content": f"<@{m['author']['id']}>",
                                     "allowed_mentions": {"users": [m["author"]["id"]]}, "embeds": [embed]})
            answered += 1
    return answered
