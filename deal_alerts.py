"""Find newly listed eBay.de deals and post them to Discord webhooks.

Runs once per invocation (Windows Task Scheduler or GitHub Actions cron). State lives in state.json:
which items were already posted, and candidates for the delayed free-deals.

Env:
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET   eBay developer app keys (production)
  EBAY_CAMPAIGN_ID                     optional eBay Partner Network campaign id
  WEBHOOK_DEAL_ALERTS, WEBHOOK_RETRO_GAMES,
  WEBHOOK_CLOTHING_DEALS, WEBHOOK_FREE_DEALS
  DRY_RUN=1                            print posts instead of sending

On a PC the same values can live in secrets.env next to this script (KEY=value per line).
"""

import base64
import email.utils
import json
import os
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
STATE_PATH = os.path.join(HERE, "state.json")
SECRETS_PATH = os.path.join(HERE, "secrets.env")
LOG_PATH = os.path.join(HERE, "deal_alerts.log")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{}"

WEBHOOK_ENV = {
    "deal-alerts": "WEBHOOK_DEAL_ALERTS",
    "retro-games": "WEBHOOK_RETRO_GAMES",
    "clothing-deals": "WEBHOOK_CLOTHING_DEALS",
    "free-deals": "WEBHOOK_FREE_DEALS",
}
COLORS = {}      # every channel uses the gold brand color

DEFAULT_COLOR = 0xD4AF37   # gold, matches the server branding
SEEN_TTL_DAYS = 14
CONDITIONS = {"Neu": "New", "Neu mit Etikett": "New with tags", "Neu ohne Etikett": "New without tags",
              "Gebraucht": "Used", "Sehr gut": "Very good", "Gut": "Good", "Akzeptabel": "Acceptable"}



def load_secrets():
    """Fill missing env vars from secrets.env, so the PC setup needs no system-wide variables."""
    if not os.path.exists(SECRETS_PATH):
        return
    with open(SECRETS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_secrets()
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def now():
    return datetime.now(timezone.utc)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": {}, "candidates": [], "free_posted": {}, "initialized": False}


def save_state(state):
    cutoff = (now() - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    state["messages"] = [m for m in state.get("messages", []) + _messages if m[2] >= cutoff]
    _messages.clear()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


class Ebay:
    def __init__(self, marketplace, campaign_id=None):
        self.marketplace = marketplace
        self.currency, self.country = "EUR", "DE"  # switched by use_market() for other eBay sites
        self.campaign_id = campaign_id
        self.token = self._get_token()

    def _get_token(self):
        creds = f"{os.environ['EBAY_CLIENT_ID'].strip()}:{os.environ['EBAY_CLIENT_SECRET'].strip()}"
        r = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": "Basic " + base64.b64encode(creds.encode()).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]

    def _headers(self):
        h = {"Authorization": f"Bearer {self.token}",
             "X-EBAY-C-MARKETPLACE-ID": self.marketplace}
        if self.campaign_id:
            h["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={self.campaign_id}"
        return h

    def use_market(self, market):
        self.marketplace, self.currency, self.country = market["id"], market["currency"], market["country"]

    def search(self, query, max_price, country=None):
        filters = [
            f"price:[..{max_price}]",
            f"priceCurrency:{self.currency}",
            "buyingOptions:{FIXED_PRICE}",
            f"itemLocationCountry:{self.country}",
        ]
        r = requests.get(
            SEARCH_URL,
            headers=self._headers(),
            params={"q": query, "sort": "newlyListed", "limit": 50,
                    "filter": ",".join(filters)},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("itemSummaries", [])

    def market_prices(self, query, new_only=True, skip_id=None):
        """Asking prices of fixed-price listings on this eBay site: a rough proxy for what an item resells for."""
        filters = f"buyingOptions:{{FIXED_PRICE}},priceCurrency:{self.currency},itemLocationCountry:{self.country}"
        if new_only:
            filters += ",conditions:{NEW}"
        r = requests.get(
            SEARCH_URL,
            headers=self._headers(),
            params={"q": query, "limit": 50, "filter": filters},
            timeout=30,
        )
        r.raise_for_status()
        items = [i for i in r.json().get("itemSummaries", []) if "price" in i and i.get("itemId") != skip_id]
        self.last_image = next((image_url(i) for i in items if image_url(i)), None)  # photo for shop deals without one
        return [float(i["price"]["value"]) for i in items]

    def get_item(self, item_id):
        r = requests.get(ITEM_URL.format(item_id), headers=self._headers(), timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def image_url(item):
    """Biggest available eBay photo of a listing (search results don't always carry the main image)."""
    for img in [item.get("image")] + (item.get("thumbnailImages") or []) + (item.get("additionalImages") or []):
        if img and img.get("imageUrl"):
            return re.sub(r"\.webp$", ".jpg", re.sub(r"s-l\d+\.", "s-l1600.", img["imageUrl"]))
    return None


def shipping_cost(item):
    opts = item.get("shippingOptions") or []
    if not opts or "shippingCost" not in opts[0]:
        return None
    return float(opts[0]["shippingCost"]["value"])


def is_excluded(title, words):
    t = title.lower()
    return any(w.lower() in t for w in words)


def has_keyword(title, keywords):
    t = title.lower()
    return any(re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", t) for k in keywords)


def route(title, cfg):
    """First routing rule whose keyword appears in the title, as a whole word."""
    for rule in cfg.get("routing", []):
        if has_keyword(title, rule["keywords"]):
            return rule["channel"]
    return None


_top_posted = 0


def discount(price, ref):
    if not ref:
        return 0.0
    return max(0.0, 1 - price / ref)


DE_MARKET = {"id": "EBAY_DE", "currency": "EUR", "country": "DE", "symbol": "€", "site": "eBay.de", "flag": "🇩🇪", "fx": 1}


def sold_link(query, site=DE_MARKET):
    """eBay search of sold listings: the real sales of the last weeks (eBay's API has no sales numbers)."""
    return (f"https://www.{site['site'].lower()}/sch/i.html?_nkw={requests.utils.quote(query)}"
            "&LH_Sold=1&LH_Complete=1")


def make_embed(item, search, channel, market, n, site=DE_MARKET):
    c = site["symbol"]
    price = float(item["price"]["value"])
    ship = shipping_cost(item) or 0
    total = price + ship
    url = item.get("itemAffiliateWebUrl") or item["itemWebUrl"]
    off = discount(total, market)
    fields = [
        {"name": "Price", "value": f"**{c}{price:.2f}**" + (f" + {c}{ship:.2f} shipping" if ship else " · free shipping"),
         "inline": True},
        {"name": "eBay market", "value": f"~{c}{market:.0f} ({n} listings)", "inline": True},
        {"name": "You save", "value": f"~{c}{market - total:.0f} ({off:.0%})", "inline": True},
        {"name": "📊 Demand", "value": f"[See what sold recently]({sold_link(item_query(item['title']), site)})",
         "inline": False},
    ]
    details = []
    if item.get("condition"):
        details.append(CONDITIONS.get(item["condition"], item["condition"]))
    seller = item.get("seller") or {}
    if seller.get("feedbackPercentage"):
        details.append(f"Seller {seller['feedbackPercentage']}% ({seller.get('feedbackScore', '?')})")
    embed = {
        "author": {"name": f"🔥 {off:.0%} below market · {site['flag']} {site['site']} · {search['name']}"},
        "title": item["title"][:256],
        "url": url,
        "color": COLORS.get(channel, DEFAULT_COLOR),
        "fields": fields,
        "footer": {"text": " · ".join(details + [f"Ships from {site['country']}", "Price may change"])},
        "timestamp": now().isoformat(),
    }
    img = image_url(item)
    if img:
        embed["image"] = {"url": img}
    return embed


def webhooks_from_env():
    """WEBHOOKS secret: one "channel-name=https://discord.com/api/webhooks/..." per line."""
    hooks = {}
    for line in os.environ.get("WEBHOOKS", "").splitlines():
        if "=" in line:
            name, url = line.split("=", 1)
            hooks[name.strip()] = url.strip()
    return hooks


def webhook_for(channel):
    return webhooks_from_env().get(channel) or os.environ.get(WEBHOOK_ENV.get(channel, ""))


DIGEST_CHANNEL = "deal-alerts"
_digest = []        # deals posted to niche channels this run, announced in deal-alerts at the end
_messages = []      # (channel, message id) of everything posted this run, so cleanup.py can remove it later
_guild_ids = {}


def guild_id_for(hook):
    if hook not in _guild_ids:
        try:
            _guild_ids[hook] = requests.get(hook, timeout=30).json().get("guild_id")
        except (requests.RequestException, ValueError):
            _guild_ids[hook] = None
    return _guild_ids[hook]


def send(hook, payload):
    """Post to a webhook and return the created message, or None."""
    for _ in range(3):
        r = requests.post(hook, params={"wait": "true"}, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        if r.ok:
            time.sleep(1)  # stay well below Discord's webhook rate limit
            return r.json()
        print(f"Discord error {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    return None


def post(channel, embed):
    hook = webhook_for(channel)
    if DRY_RUN or not hook:
        print(f"[{'dry-run' if DRY_RUN else 'no webhook'}] #{channel}: {embed['title']} {embed['url']}")
        return True
    msg = send(hook, {"username": "Deal Alerts", "embeds": [embed]})
    if msg is None:
        return False
    print(f"Posted to #{channel}: {embed.get('title', '')[:80]}")
    _messages.append([channel, msg["id"], now().isoformat()])
    if channel != DIGEST_CHANNEL:
        guild = guild_id_for(hook)
        link = f"https://discord.com/channels/{guild}/{msg['channel_id']}/{msg['id']}" if guild else embed["url"]
        price = next((f["value"] for f in embed.get("fields", []) if f["name"] in ("Price", "Buy price")), "")
        _digest.append({"channel_id": msg["channel_id"], "title": embed["title"], "price": price, "link": link,
                        "image": (embed.get("image") or {}).get("url")})
    return True


def send_digest():
    """One short notice in deal-alerts per run, pointing to every new deal in the niche channels."""
    hook = webhook_for(DIGEST_CHANNEL)
    if not _digest or not hook or DRY_RUN:
        return 0
    lines = [f"<#{d['channel_id']}> · [{d['title'][:80]}]({d['link']})" + (f" · {d['price']}" if d["price"] else "")
             for d in _digest]
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for i, text in enumerate(chunks):
        title = f"🆕 {len(_digest)} new deal{'s' if len(_digest) != 1 else ''}" if i == 0 else None
        embed = {"description": text, "color": DEFAULT_COLOR, "timestamp": now().isoformat()}
        if title:
            embed["title"] = title
            img = next((d["image"] for d in _digest if d.get("image")), None)
            if img:
                embed["thumbnail"] = {"url": img}
        msg = send(hook, {"username": "Deal Alerts", "embeds": [embed]})
        if msg:
            _messages.append([DIGEST_CHANNEL, msg["id"], now().isoformat()])
    return len(_digest)


def item_query(title):
    """First words of an eBay title, without condition and filler words, as a search for the same product."""
    words = [w for w in re.findall(r"[\w+'-]+", title.lower())
             if len(w) > 1 and w not in QUERY_STOPWORDS]
    return " ".join(words[:6])


QUERY_STOPWORDS = {"neu", "new", "ovp", "top", "gebraucht", "zustand", "sehr", "gut", "guter", "selten", "rar",
                   "original", "originalverpackt", "versand", "kostenlos", "inkl", "mit", "und", "der", "die",
                   "das", "für", "fur", "wie", "sammlung", "konvolut", "getestet", "tested", "pal", "blitzversand",
                   "used", "brand", "sealed", "authentic", "genuine", "rare", "free", "shipping", "fast", "the",
                   "and", "with", "for", "lot", "cib", "complete"}


def good_seller(item, qc):
    seller = item.get("seller") or {}
    try:
        pct = float(seller.get("feedbackPercentage", 0))
        score = int(seller.get("feedbackScore", 0))
    except (TypeError, ValueError):
        return False
    return pct >= qc.get("min_seller_feedback", 97) and score >= qc.get("min_seller_score", 20)


def run_searches(ebay, cfg, state):
    """New eBay listings, posted only when they are clearly below what the same product sells for on eBay.

    Each search's new listings are pre-sorted by how far they are below the search's rough ref_price.
    The best ones are then checked against current eBay prices for that exact product; only a big,
    plausible gap (not a different item or an accessory) gets posted.
    """
    markets = cfg.get("markets") or [DE_MARKET]
    turn = state.get("market_turn", 0) % len(markets)
    state["market_turn"] = turn + 1
    site = {**DE_MARKET, **markets[turn]}  # one eBay site per run keeps us inside eBay's daily API limit
    ebay.use_market(site)
    done_markets = state.setdefault("initialized_markets", ["EBAY_DE"] if state.get("initialized") else [])
    first_run = site["id"] not in done_markets
    print(f"eBay site this run: {site['site']}{' (first run, only reading)' if first_run else ''}")
    qc = cfg.get("quality") or {}
    exclude = cfg.get("exclude_words", []) + qc.get("exclude_words", [])
    fx = site.get("fx", 1)
    candidates = []
    for search in cfg["searches"]:
        if site["id"] not in search.get("markets", [m["id"] for m in markets]):
            continue
        query = search["query"] if site["id"] == "EBAY_DE" else search.get("query_en") or translate(search["query"])
        try:
            items = ebay.search(query, round(search["max_price"] * fx))
        except requests.RequestException as e:
            print(f"Search '{search['name']}' failed: {e}", file=sys.stderr)
            continue
        for item in items:
            item_id = item["itemId"]
            if item_id in state["seen"]:
                continue
            state["seen"][item_id] = now().isoformat()
            if first_run or is_excluded(item["title"], exclude) or not good_seller(item, qc):
                continue
            total = float(item["price"]["value"]) + (shipping_cost(item) or 0)
            if search.get("ref_price") and total > search["ref_price"] * fx * (1 - qc.get("min_discount", 0.3)):
                continue  # not even cheap compared to the rough reference price
            candidates.append((discount(total, (search.get("ref_price") or 0) * fx), total, item, search))
    state["initialized"] = True
    if site["id"] not in done_markets:
        done_markets.append(site["id"])

    posted_per_channel, lookups = {}, 0
    for _, total, item, search in sorted(candidates, key=lambda c: -c[0]):
        channel = search["channel"]
        if lookups >= qc.get("max_lookups_per_run", 20):
            break
        if posted_per_channel.get(channel, 0) >= cfg.get("max_posts_per_run", 2):
            continue
        query = item_query(item["title"])
        if len(query.split()) < 2:
            continue
        lookups += 1
        try:
            prices = ebay.market_prices(query, new_only=False, skip_id=item["itemId"])
        except requests.RequestException as e:
            print(f"eBay lookup '{query}' failed: {e}", file=sys.stderr)
            continue
        if len(prices) < qc.get("min_comparables", 8):
            continue  # too few matches to know the market price
        market = statistics.median(prices)
        off = discount(total, market)
        if off < qc.get("min_discount", 0.3) or off > qc.get("max_discount", 0.75) \
                or market - total < qc.get("min_saving", 15) * fx:
            continue  # too small a saving, or so big that it's probably a different item
        if not image_url(item):
            try:
                full = ebay.get_item(item["itemId"]) or {}
            except requests.RequestException:
                full = {}
            item = {**item, "image": full.get("image"), "additionalImages": full.get("additionalImages")}
            if not image_url(item):
                continue  # every post gets a product photo
        if post(channel, make_embed(item, search, channel, market, len(prices), site)):
            posted_per_channel[channel] = posted_per_channel.get(channel, 0) + 1
            state["candidates"].append({
                "item_id": item["itemId"],
                "search": search["name"],
                "score": off,
                "posted_at": now().isoformat(),
            })
    ebay.use_market(DE_MARKET)  # flips compare German shop prices with eBay.de
    return posted_per_channel


TRANSLATE = [("karten sammlung", "cards collection"), ("set neu ovp", "set new sealed"), ("trainingsjacke", "track jacket"),
             ("minifiguren", "minifigures"), ("konvolut", "lot"), ("sammlung", "collection"), ("spiele", "games"),
             ("spiel", "game"), ("modul", "cartridge"), ("konsole", "console"), ("jacke", "jacket"),
             ("pullover", "sweater"), ("hemd", "shirt"), ("karten", "cards")]


def translate(query):
    """German search words to English for eBay.com and eBay.co.uk."""
    for de, en in TRANSLATE:
        query = re.sub(rf"\b{de}\b", en, query)
    return query


PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{2})?)\s*(?:€|EUR)")
NS_PEPPER = "{http://www.pepper.com/rss}"


def fetch_retail_deals(feed_url):
    """Parse a mydealz RSS feed into (id, title, link, price, merchant, image)."""
    r = requests.get(feed_url, headers={"User-Agent": "Mozilla/5.0 (deal-alerts)"}, timeout=30)
    r.raise_for_status()
    deals = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        merchant_el = it.find(f"{NS_PEPPER}merchant")
        price_txt = merchant_el.get("price") if merchant_el is not None else None
        if not price_txt:
            m = PRICE_RE.search(title)
            price_txt = m.group(1) if m else None
        if not price_txt or not link:
            continue
        try:
            price = float(price_txt.replace("€", "").replace(".", "").replace(",", ".").strip()) \
                if "," in price_txt else float(price_txt.replace("€", "").strip())
        except ValueError:
            continue
        media = it.find("{http://search.yahoo.com/mrss/}content")
        deals.append({
            "id": it.findtext("guid") or link,
            "title": title,
            "link": link,
            "price": price,
            "merchant": merchant_el.get("name") if merchant_el is not None else None,
            "image": media.get("url") if media is not None else None,
            "published": parse_date(it.findtext("pubDate")),
        })
    return deals


def parse_date(text):
    try:
        return email.utils.parsedate_to_datetime(text) if text else None
    except (TypeError, ValueError):
        return None


def search_query_from_title(title):
    """Drop price, shop and filler text so the title works as an eBay search."""
    t = re.split(r"\s[|–-]\s|\sfür\s|\s@\s|\(", title)[0]
    t = PRICE_RE.sub("", t)
    words = [w for w in re.findall(r"[\w+-]+", t) if len(w) > 1]
    return " ".join(words[:7])


def run_hot_deals(cfg, state):
    """Without eBay keys: post recent reduced deals that match a niche channel, without a resale check.

    A deal is posted once (hot_posted). Recent deals that were read earlier but never posted still
    qualify, so quiet channels fill up with current offers instead of staying empty.
    """
    hc = cfg.get("hot_deals")
    fc = cfg.get("flips") or {}
    if not hc or not hc.get("enabled"):
        return 0
    seen = state.setdefault("flips_seen", {})  # shared with flips, so nothing is posted twice later
    if "hot_posted" not in state and seen:
        # Older versions didn't record what was posted. Only the very first batch is known
        # to be unposted; treat everything read after it as posted so nothing repeats.
        first_batch = min(seen.values())[:16]
        state["hot_posted"] = {k: v for k, v in seen.items() if v[:16] != first_batch}
    done = state.setdefault("hot_posted", {})
    first_run = not state.get("flips_initialized")
    max_age = timedelta(hours=hc.get("max_age_hours", 48))
    per_channel, posted = {}, 0
    for feed in fc.get("feeds", []):
        try:
            deals = fetch_retail_deals(feed)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"Feed {feed} failed: {e}", file=sys.stderr)
            continue
        print(f"Feed {feed}: {len(deals)} deals, {sum(d['id'] not in seen for d in deals)} new")
        for d in deals:
            seen.setdefault(d["id"], now().isoformat())
            if first_run or d["id"] in done or posted >= hc.get("max_posts_per_run", 30):
                continue
            if d.get("published") and now() - d["published"] > max_age:
                continue
            if is_excluded(d["title"], cfg.get("exclude_words", []) + fc.get("exclude_words", [])):
                continue
            channel = route(d["title"], cfg)
            if not channel or per_channel.get(channel, 0) >= hc.get("max_per_channel", 3):
                continue
            if post(channel, make_hot_embed(d, channel)):
                done[d["id"]] = now().isoformat()
                per_channel[channel] = per_channel.get(channel, 0) + 1
                posted += 1
    state["flips_initialized"] = True
    cutoff = (now() - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    state["hot_posted"] = {k: v for k, v in done.items() if v >= cutoff}
    return posted


def make_hot_embed(d, channel):
    query = search_query_from_title(d["title"])
    ebay_link = "https://www.ebay.de/sch/i.html?_nkw=" + requests.utils.quote(query) + "&LH_Sold=1&LH_Complete=1"
    fields = [{"name": "Price", "value": f"€{d['price']:.2f}", "inline": True}]
    if d.get("merchant"):
        fields.append({"name": "Shop", "value": d["merchant"], "inline": True})
    fields.append({"name": "Resale check", "value": f"[Sold prices on eBay]({ebay_link})", "inline": True})
    embed = {
        "title": ("🔥 " + d["title"])[:256],
        "url": d["link"],
        "color": COLORS.get(channel, DEFAULT_COLOR),
        "fields": fields,
        "footer": {"text": f"Price drop via mydealz · Check sold prices before buying · "
                           f"{now().strftime('%d %b %Y, %H:%M UTC')}"},
        "timestamp": now().isoformat(),
    }
    if d.get("image"):
        embed["image"] = {"url": d["image"]}
    return embed


def run_flips(ebay, cfg, state):
    """Reduced retail deals whose typical eBay price leaves a profit after fees and shipping."""
    fc = cfg.get("flips")
    if not fc or not fc.get("enabled"):
        return 0
    seen = state.setdefault("flips_seen", {})
    first_run = not state.get("flips_initialized")
    posted = 0
    for feed in fc["feeds"]:
        try:
            deals = fetch_retail_deals(feed)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"Feed {feed} failed: {e}", file=sys.stderr)
            continue
        for d in deals:
            if d["id"] in seen:
                continue
            seen[d["id"]] = now().isoformat()
            if first_run or posted >= fc.get("max_posts_per_run", 3):
                continue
            if d["price"] < fc.get("min_buy_price", 5) or d["price"] > fc.get("max_buy_price", 150):
                continue
            if is_excluded(d["title"], cfg.get("exclude_words", []) + fc.get("exclude_words", [])):
                continue
            query = search_query_from_title(d["title"])
            if len(query.split()) < 2:
                continue
            try:
                prices = ebay.market_prices(query)
            except requests.RequestException as e:
                print(f"eBay lookup '{query}' failed: {e}", file=sys.stderr)
                continue
            if len(prices) < fc.get("min_comparables", 5):
                continue  # too few matches to trust the estimate
            resale = statistics.median(prices)
            if resale > d["price"] * fc.get("max_resale_factor", 4):
                continue  # likely matched a different, pricier product
            profit = resale * (1 - fc.get("ebay_fee", 0.13)) - fc.get("shipping_cost", 5) - d["price"]
            if profit < fc.get("min_profit", 10) or profit / d["price"] < fc.get("min_roi", 0.25):
                continue
            channel = route(d["title"], cfg) or fc.get("channel", "tech-flips")
            d = {**d, "image": d.get("image") or ebay.last_image}
            if not d["image"]:
                continue  # every post gets a product photo
            if post(channel, make_flip_embed(d, query, resale, profit, len(prices), fc)):
                posted += 1
                state.setdefault("hot_posted", {})[d["id"]] = now().isoformat()  # never repost as plain deal
    state["flips_initialized"] = True
    cutoff = (now() - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    state["flips_seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    return posted


def make_flip_embed(d, query, resale, profit, n, fc):
    ebay_link = "https://www.ebay.de/sch/i.html?_nkw=" + requests.utils.quote(query) + "&LH_Sold=1&LH_Complete=1"
    embed = {
        "author": {"name": f"💸 Flip · ~€{profit:.0f} profit ({profit / d['price']:.0%})"},
        "title": d["title"][:256],
        "url": d["link"],
        "color": DEFAULT_COLOR,
        "fields": [
            {"name": "Buy", "value": f"**€{d['price']:.2f}**" + (f" at {d['merchant']}" if d.get("merchant") else ""),
             "inline": True},
            {"name": "Sells on eBay", "value": f"~€{resale:.0f} ({n} listings)", "inline": True},
            {"name": "Profit", "value": f"~€{profit:.0f} after fees · [sold prices]({ebay_link})", "inline": True},
        ],
        "footer": {"text": f"After ~{fc.get('ebay_fee', 0.13):.0%} eBay fees and €{fc.get('shipping_cost', 5)} "
                           f"shipping · Check sold prices before buying"},
        "timestamp": now().isoformat(),
    }
    if d.get("image"):
        embed["image"] = {"url": d["image"]}
    return embed


def run_free_deals(ebay, cfg, state):
    """Repost the best premium deals from 24-48 h ago to free-deals, re-fetched so data is current."""
    delay = timedelta(hours=cfg.get("free_deals_delay_hours", 24))
    today = now().strftime("%Y-%m-%d")
    done_today = state["free_posted"].get(today, 0)
    limit = cfg.get("free_deals_per_day", 2)
    searches = {s["name"]: s for s in cfg["searches"]}

    ready, keep = [], []
    for c in state["candidates"]:
        age = now() - datetime.fromisoformat(c["posted_at"])
        if age < delay:
            keep.append(c)
        elif age < delay * 2:
            ready.append(c)
    state["candidates"] = keep

    # Spread the daily picks: at most one per run, and only after the delay has passed.
    if done_today >= limit or not ready:
        state["candidates"].extend(ready)
        return 0
    ready.sort(key=lambda c: c["score"], reverse=True)
    for i, c in enumerate(ready):
        search = searches.get(c["search"])
        if not search:
            continue
        item = ebay.get_item(c["item_id"])
        if not item or item.get("estimatedAvailabilities", [{}])[0].get("estimatedAvailabilityStatus") == "OUT_OF_STOCK":
            continue
        if post("free-deals", make_embed(item, search, "free-deals", search.get("ref_price") or float(item["price"]["value"]), 0)):
            state["free_posted"] = {today: done_today + 1}
            state["candidates"].extend(r for r in ready[i + 1:])
            return 1
    return 0


def main():
    if sys.stdout is None:  # pythonw (Task Scheduler) has no console, so log to a file
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8")
    print(f"--- {now().isoformat()}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    state = load_state()
    state.setdefault("quality_since", now().isoformat())  # cleanup.py removes bot posts older than this
    ebay = None
    if os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"):
        try:
            ebay = Ebay(cfg.get("marketplace", "EBAY_DE"), os.environ.get("EBAY_CAMPAIGN_ID") or None)
        except requests.RequestException as e:
            print(f"eBay login failed ({e}); falling back to shop deals only.", file=sys.stderr)
    if ebay is None:
        # Without working eBay keys, only matching shop price drops are posted.
        hot = run_hot_deals(cfg, state)
        send_digest()
        save_state(state)
        print(f"No eBay access: eBay searches skipped; shop deals posted: {hot}")
        return
    posted = run_searches(ebay, cfg, state)
    flips = run_flips(ebay, cfg, state)
    hot = run_hot_deals(cfg, state)  # off by default: shop deals without a profit check
    free = run_free_deals(ebay, cfg, state)
    send_digest()
    save_state(state)
    print(f"Posted: {posted or 'nothing new'}; flips: {flips}; shop deals: {hot}; free-deals: {free}")


if __name__ == "__main__":
    main()
