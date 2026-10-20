# Deal-Alerts: Einrichtung

Das Skript sucht alle 20 Minuten neu eingestellte Sofort-Kaufen-Angebote auf eBay.de (offizielle Browse API) und postet passende Deals als englische Karten in die Discord-Kanäle.

| Kanal | Inhalt |
|---|---|
| 🚨・deal-alerts | allgemeine Deals (z. B. Retro-Konsolen) und 💸 Flips: reduzierte Shop-Angebote, die auf eBay deutlich teurer verkauft werden |
| 🎮・retro-games | Retro-Spiele |
| 👕・clothing-deals | Vintage-Kleidung von eBay (deine Vinted-Funde postest du dort selbst) |

Ohne eBay-Keys postet der Bot reduzierte mydealz-Deals zu deinen Nischen (LEGO, Nike, Retro-Games, ohne Gewinn-Check, Regeln unter `hot_deals:`). Mit Keys kommen eBay-Suchen und Flips mit Gewinnrechnung dazu.

Eingebaut: Preisgrenze pro Suche, Ausschlusswörter (defekt, Fake, …), keine Doppelposts, max. 5 Posts pro Kanal und Lauf. Beim allerersten Lauf wird nichts gepostet, damit keine Flut alter Angebote kommt.

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
- `WEBHOOK_DEAL_ALERTS`, `WEBHOOK_RETRO_GAMES`, `WEBHOOK_CLOTHING_DEALS`

## 4. Testen und starten

Actions → „Deal Alerts“ → „Run workflow“ mit „dry run“ zeigt die Treffer nur im Log. Danach läuft es automatisch alle 20 Minuten.

## Flips (reduziert kaufen, auf eBay teurer verkaufen)

Der Bot liest die heißen Deals von mydealz (RSS), sucht jedes Produkt auf eBay.de (neu, Sofort-Kaufen) und nimmt den mittleren Preis als Verkaufswert. Gepostet wird nur, wenn nach eBay-Gebühren (13 %) und Versand (5 €) mindestens 10 € und 25 % Gewinn bleiben. Das ist eine Schätzung aus aktuellen Angebotspreisen, nicht aus echten Verkäufen. Vor dem Kauf also kurz die verkauften Angebote auf eBay prüfen. Alle Grenzen stehen unter `flips:` in `config.yaml`.

## Suchen anpassen

Alles steht in `config.yaml`: Suchbegriff, Kanal, Höchstpreis und ein grober üblicher Verkaufspreis (für den Rabatt in der Karte und die Auswahl der free-deals).

## Hinweise

- eBay erlaubt, dass Angebotsdaten höchstens 6 Stunden alt sind. Deshalb steht in jeder Karte der Prüfzeitpunkt und „Price and availability may change“.
- Vinted wird bewusst nicht automatisch abgefragt, weil das gegen die Vinted-AGB verstößt.
- GitHub kann geplante Läufe um einige Minuten verzögern. Öffentliche Repos haben unbegrenzt kostenlose Actions-Minuten.
- GitHub pausiert geplante Läufe in öffentlichen Repos nach 60 Tagen ohne Commit. Dann einfach im Actions-Tab wieder aktivieren.
