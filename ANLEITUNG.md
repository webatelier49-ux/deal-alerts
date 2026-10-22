# Deal-Alerts: Einrichtung

Das Skript sucht alle 20 Minuten neu eingestellte Sofort-Kaufen-Angebote auf eBay.de (offizielle Browse API) und postet passende Deals als englische Karten in die Discord-Kanäle.

Kanäle (Name ohne Emoji, so wie im Secret `WEBHOOKS`):

- **Gaming:** nintendo, game-boy, playstation, consoles, other-consoles (Sega, Xbox), retro-games, retro-bundles
- **Sneaker:** nike-dunks, jordans, air-max, new-balance, adidas
- **Kleidung:** carhartt, levis, ralph-lauren, the-north-face, vintage-nike, designer (Stone Island, CP Company, Moncler), clothing-deals (alles andere)
- **Sammeln:** lego, pokemon-cards, trading-cards
- **tech-flips:** Elektronik-Flips mit Gewinnrechnung
- **deal-alerts:** nach jedem Lauf eine Übersicht aller neuen Deals mit Link zum Kanal und zum Post

eBay-Suchen stehen unter `searches:`, die Zuordnung von mydealz-Deals und Flips nach Stichwort unter `routing:` in `config.yaml`.

**eBay-Seiten:** abwechselnd 🇩🇪 eBay.de, 🇺🇸 eBay.com und 🇬🇧 eBay.co.uk (`markets:` in `config.yaml`), pro Lauf eine Seite wegen eBays Tageslimit.

**Nur gute Deals:** Jedes neue eBay-Angebot wird mit den aktuellen eBay-Preisen für genau dieses Produkt verglichen. Gepostet wird nur, was mindestens 30 % und 15 € unter dem Marktpreis liegt, von Verkäufern mit mind. 97 % positiven Bewertungen. Mehr als 75 % drunter wird verworfen (meist Zubehör oder ein anderes Produkt). Shop-Deals (mydealz, DealDoktor, mein-deal) kommen nur als Flip, wenn nach eBay-Gebühren und Versand mind. 15 € und 30 % Gewinn bleiben. Max. 2 Posts pro Kanal und Lauf. Alle Grenzen stehen unter `quality:` und `flips:` in `config.yaml`.

Jede Karte zeigt ein großes Produktbild, Preis, eBay-Marktpreis und Ersparnis bzw. Gewinn, in Gold passend zum Server.

Beim allerersten Lauf wird nichts gepostet, damit keine Flut alter Angebote kommt.

## Alte Posts löschen

Actions → „Clean up old posts“ → „Run workflow“ löscht alle Bot-Posts aus der Zeit vor dem Qualitätsfilter (oder vor der Zeit unter „cutoff“). Nachrichten von Mitgliedern und angepinnte Nachrichten bleiben. Danach setzt es in jedem Kanal das Kanalthema und eine angepinnte Erklärung ganz oben (Texte unter `channel_info:` in `config.yaml`). Dafür braucht es das Secret `DISCORD_BOT_TOKEN` (der Token des Bots, mit dem der Server eingerichtet wurde).

## 1. eBay-Developer-Zugang (einmalig, kostenlos)

1. Auf developer.ebay.com mit deinem eBay-Konto registrieren.
2. Unter „Application Keys“ ein **Production**-Keyset anlegen (nicht Sandbox).
3. eBay fragt nach „Marketplace Account Deletion Notifications“. Da wir keine Nutzerdaten speichern, wählst du die Ausnahme („not persisting eBay data / opt out“).
4. **App ID (Client ID)** und **Cert ID (Client Secret)** brauchst du für Schritt 3. Nicht im Chat posten.

Optional: Beim eBay Partner Network (partnernetwork.ebay.de) anmelden und eine **Campaign ID** erstellen. Dann verdienst du an Käufen über die Links.

## 2. GitHub-Repository

https://github.com/webatelier49-ux/deal-alerts (öffentlich). Code und Suchliste sind sichtbar, Keys und Webhooks als Secrets nicht.

## 3. Geheimnisse hinterlegen

Im Repository unter Settings → Secrets and variables → Actions → „New repository secret“:

- `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`
- `EBAY_CAMPAIGN_ID` (optional)
- `DISCORD_BOT_TOKEN` (nur zum Löschen alter Posts)
- `WEBHOOKS`: eine Zeile pro Kanal im Format `kanalname=webhook-url` (z. B. `lego=https://discord.com/api/webhooks/...`)

## 4. Testen und starten

Actions → „Deal Alerts“ → „Run workflow“ mit „dry run“ zeigt die Treffer nur im Log. Danach läuft es automatisch alle 20 Minuten.

## Flips (reduziert kaufen, auf eBay teurer verkaufen)

Der Bot liest die heißen Deals von mydealz (RSS), sucht jedes Produkt auf eBay.de (neu, Sofort-Kaufen) und nimmt den mittleren Preis als Verkaufswert. Gepostet wird nur, wenn nach eBay-Gebühren (13 %) und Versand (5 €) mindestens 10 € und 25 % Gewinn bleiben. Das ist eine Schätzung aus aktuellen Angebotspreisen, nicht aus echten Verkäufen. Vor dem Kauf also kurz die verkauften Angebote auf eBay prüfen. Alle Grenzen stehen unter `flips:` in `config.yaml`.

## Suchen anpassen

Alles steht in `config.yaml`: Suchbegriff, Kanal, Höchstpreis und ein grober üblicher Verkaufspreis (für den Rabatt in der Karte).

## Hinweise

- eBay erlaubt, dass Angebotsdaten höchstens 6 Stunden alt sind. Deshalb steht in jeder Karte der Prüfzeitpunkt und „Price and availability may change“.
- Vinted wird bewusst nicht automatisch abgefragt, weil das gegen die Vinted-AGB verstößt.
- Ein Lauf bleibt ca. 5,5 Stunden aktiv und prüft selbst alle 20 Minuten (GitHubs eigener Zeitplan ist zu unzuverlässig). Stündlich startet GitHub bei Bedarf den nächsten. Die Merkliste liegt im Branch `state`.
- GitHub pausiert geplante Läufe in öffentlichen Repos nach 60 Tagen ohne Commit. Dann einfach im Actions-Tab wieder aktivieren.
