# TwoMoons Release Notifier

Meldet neue Produkte im Shop [twomoons.ch](https://www.twomoons.ch/) stündlich in
einen Discord-Kanal — mit Name, Preis, Sprachen, Badges („Neu", „Vorbestellung"),
Produktbild und Link.

```
┌──────────────────────────────────────────────┐
│ Homeworlds Spotlight Deck - Chewbacca        │
│ 🏷️ Neu · Vorbestellung                       │
│ Preis: CHF 24.90                             │
│ Sprachen: Englisch                    [Bild] │
│ Hersteller: Star Wars Unlimited              │
│ Link: Zum Produkt                            │
│ Neu im Shop · twomoons.ch                    │
└──────────────────────────────────────────────┘
```

Läuft ausschliesslich über GitHub Actions — kein Server, keine Datenbank.

## Inhalt

1. [Wie es funktioniert](#wie-es-funktioniert)
2. [Einrichtung Schritt für Schritt](#einrichtung-schritt-für-schritt)
3. [Zeitplan und Default-Branch](#zeitplan-und-default-branch)
4. [Filter: was gemeldet wird und was nicht](#filter-was-gemeldet-wird-und-was-nicht)
5. [`config.json` im Detail](#configjson-im-detail)
6. [`state.json` — das Gedächtnis](#statejson--das-gedächtnis)
7. [Diagnose ohne Knopfdruck](#diagnose-ohne-knopfdruck)
8. [Lokal ausführen](#lokal-ausführen)
9. [Wenn etwas nicht klappt](#wenn-etwas-nicht-klappt)
10. [Was am echten Shop gemessen wurde](#was-am-echten-shop-gemessen-wurde)

## Wie es funktioniert

Ein Lauf besteht aus vier Schritten:

1. **Produkte finden.** Der Shop läuft auf Shopware 6 und veröffentlicht eine
   Sitemap: `https://www.twomoons.ch/sitemap.xml` verweist auf eine gepackte
   Teil-Sitemap mit rund 6600 URLs. Die Sitemap überlebt Umbauten am Layout und
   ist deshalb die Hauptquelle. Fällt sie aus, können ersatzweise Listing-Seiten
   abgeklappert werden (`shop.listing_urls`).
2. **Vergleichen.** Die gefundenen URLs werden gegen `state.json` gehalten. Nur
   wirklich neue URLs werden weiterverfolgt — deshalb kostet ein stündlicher Lauf
   im Normalfall nur einen einzigen Abruf.
3. **Produktseite auswerten.** Von jeder neuen Seite werden Name, Preis,
   Sprachen, Hersteller/Serie, Bild und Kategorie gelesen. Anschliessend wird das
   Produkt in der Shop-Suche nachgeschlagen, denn die **Badges stehen nur auf den
   Listenkarten**, nicht auf der Produktseite selbst.
4. **Posten und merken.** Je Produkt ein Discord-Embed, danach wird `state.json`
   aktualisiert und vom Workflow zurück ins Repo committet.

Beim **allerersten Lauf wird nichts gepostet**: Der Ist-Stand wird nur als bekannt
gespeichert, sonst landete das komplette Sortiment im Kanal.

## Einrichtung Schritt für Schritt

### 1. Webhook in Discord erstellen

1. In Discord mit der rechten Maustaste auf den Zielkanal → **Kanal bearbeiten**
2. **Integrationen** → **Webhooks** → **Neuer Webhook**
3. Namen vergeben (z. B. „TwoMoons Releases"), optional ein Profilbild setzen
4. **Webhook-URL kopieren** — sie sieht so aus:
   `https://discord.com/api/webhooks/1234567890/AbCdEf...`

Diese URL ist ein Passwort: Wer sie hat, kann in den Kanal schreiben. Sie gehört
deshalb **nicht** in die `config.json`, sondern in ein GitHub Secret.

### 2. Secret im Repo hinterlegen

1. Im Repo auf **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**
3. Name exakt: `DISCORD_WEBHOOK_RELEASES`
4. Wert: die kopierte Webhook-URL → **Add secret**

### 3. Workflow manuell testen

**Actions** → **TwoMoons Release Notifier** → **Run workflow**. Empfehlung für den
ersten Versuch:

| Schalter | Wert | Wirkung |
|---|---|---|
| `dry_run` | ✅ an | postet nichts, ändert `state.json` nicht |
| `debug` | ✅ an | ausführliches Log |

Im Log muss stehen, wie viele URLs die Sitemap geliefert hat. Sieht das gut aus,
den Lauf ohne `dry_run` wiederholen — das ist der Erstlauf, der den Ist-Stand
merkt und noch nichts postet.

### 4. Probe mit echten Produkten (optional, empfohlen)

Bis der erste echte Neuzugang im Shop erscheint, kann es dauern. Damit du siehst,
wie eine Meldung in Discord wirklich aussieht, gibt es eine Probe mit bestehenden
Produkten aus dem Bereich **„Neu im Shop"** auf der Startseite:

**Actions** → **Run workflow** mit

| Schalter | Wert |
|---|---|
| `post_from` | `https://www.twomoons.ch/` |
| `heading` | `Neu im Shop` |
| `limit` | `3` |

Das postet die ersten drei Produkte dieses Bereichs — echte Produkte mit echten
Badges, Preisen und Bildern. Vorher ansehen, ohne zu posten: zusätzlich
`dry_run` anhaken, dann stehen die Embeds nur im Log.

Diese Probe ist bewusst harmlos:

* Sie postet auch Produkte, die schon bekannt sind — sonst käme nichts.
* Sie setzt den **Erstlauf-Schutz nicht**. Ohne diese Vorsicht würde der nächste
  reguläre Lauf das ganze übrige Sortiment für neu halten.
* Die geposteten Produkte werden gemerkt, kommen also später nicht ein zweites Mal.

Die Testnachrichten kannst du danach in Discord einfach löschen.

### 5. Ab jetzt läuft es von allein

Stündlich prüft der Workflow den Shop und postet, was neu dazugekommen ist. Mehr
ist nicht zu tun.

Soll der Kanal bewusst mit dem aktuellen Sortiment gefüllt werden, hilft
`post_existing` zusammen mit `reset` — Achtung, das postet sehr viel (die
Obergrenze `discord.max_posts_per_run` bremst pro Lauf).

## Zeitplan und Default-Branch

Der Cron-Eintrag steht auf `17 * * * *`, also stündlich zur Minute 17 (UTC).
Bewusst nicht zur vollen Stunde: Dann sind die GitHub-Runner am stärksten belegt
und Läufe starten verspätet.

**Wichtig:** GitHub startet geplante Läufe ausschliesslich auf dem
**Default-Branch** des Repos. Wird die Arbeit später auf einen anderen Branch
(z. B. `main`) gemerget, muss dieser auch der Default-Branch sein, sonst schweigt
der stündliche Lauf. Manuelle Läufe und der Auslöser über `debug-run.txt`
funktionieren auf jedem Branch.

## Filter: was gemeldet wird und was nicht

Standardmässig wird **das ganze Sortiment** gemeldet, mit einer Ausnahme:
Veranstaltungstickets (Breadcrumb-Kategorie „Events") bleiben draussen — dafür
gibt es den Schwester-Melder
[`twomoons-discord-notifier`](https://github.com/pabloesteves91/twomoons-discord-notifier).

```json
"filters": {
  "include_patterns": [],
  "exclude_patterns": [],
  "include_categories": [],
  "exclude_categories": ["Events"]
}
```

* `exclude_categories` / `include_categories` prüfen den **Kategorienpfad**
  (Breadcrumb) der Produktseite, z. B. `Home › Sammelkarten › Star Wars: Unlimited`.
  Gross-/Kleinschreibung ist egal, es genügt ein Teilstück des Namens.
  Steht etwas in `include_categories`, wird **nur** noch gemeldet, was dazu passt.
* `exclude_patterns` / `include_patterns` sind reguläre Ausdrücke auf die **URL**.
  Sie greifen schon vor dem Abruf der Seite und sind deshalb günstiger — nützen
  hier aber wenig, weil im Shop alle Produkte direkt auf der Wurzelebene liegen
  (`/armory-deck-malice-englisch`). Über die URL sind Einzelkarten, Zubehör und
  Boosterboxen nicht zu unterscheiden.

Welche Kategorien es überhaupt gibt, zeigt eine Stichprobe — siehe
[Diagnose](#diagnose-ohne-knopfdruck). Eine Messung über 30 zufällige Produkte ergab:

```
Sammelkarten 9 · Accessories 8 · Brettspiele 4 · Magic the Gathering 4
Deckboxen 3 · Tabletop 3 · Warhammer 40k 3 · Sleeves 2 · Star Wars 2 · Pokémon 2
```

Wird der Kanal zu voll, sind `Accessories`, `Sleeves`, `Deckboxen` oder `Würfel`
die naheliegenden Kandidaten für `exclude_categories`.

## `config.json` im Detail

| Bereich | Bedeutung |
|---|---|
| `shop.sitemap_url` | Einstieg in die Sitemap (Index oder einzelne Datei, auch `.gz`) |
| `shop.listing_urls` | Ersatzquelle, falls die Sitemap ausfällt; wird Seite für Seite abgeklappert |
| `shop.always_use_listings` | `true` nutzt beide Quellen gleichzeitig |
| `request.*` | Zeitlimit, Wiederholungen, Pause zwischen Abrufen, User-Agent |
| `filters.*` | siehe oben |
| `product.container_anchors` | Ankerpunkt, von dem aus der Produktbereich bestimmt wird |
| `product.container_selectors` | Ersatz, falls der Anker fehlt |
| `product.*_selectors` | Selektoren für Name, Preis, Bild, Varianten, Sprachflagge |
| `product.badge_aliases` | vereinheitlicht Badge-Texte („pre-order" → „Vorbestellung") |
| `product.manufacturer_properties` | Eigenschaften, die als Hersteller gelten (z. B. „Brand") |
| `languages` | Stichwörter je Sprache, falls es keine Sprachflagge gibt |
| `card_lookup.*` | Nachschlag der Badges über die Shop-Suche |
| `discord.webhook_env` | **Name** der Umgebungsvariable, nicht die URL selbst |
| `discord.color`, `footer`, `username`, `avatar_url` | Aussehen der Nachricht |
| `discord.max_posts_per_run` | Obergrenze pro Lauf (Standard 10) |
| `discord.max_detail_fetches_per_run` | Obergrenze an Seitenabrufen pro Lauf (Standard 60) |
| `update.*` | Nachkontrolle bereits geposteter Produkte (Preisänderungen) |
| `inspect.*` | nur für Diagnose-Läufe |

Eine Änderung an `config.json` wirkt ab dem nächsten Lauf. Änderungen an Farbe
oder Fusszeile schlagen auch auf **bereits gepostete** Nachrichten durch, sobald
die Nachkontrolle das Produkt erneut prüft: Der Fingerabdruck wird über das ganze
Embed gebildet, nicht nur über den Text.

## `state.json` — das Gedächtnis

```json
{
  "version": 1,
  "initialized": true,
  "last_run": "2026-09-18T08:17:03Z",
  "known": ["https://www.twomoons.ch/armory-deck-malice-englisch", "..."],
  "products": {
    "https://www.twomoons.ch/armory-deck-malice-englisch": {
      "name": "Armory Deck Malice",
      "price": "CHF 39.90",
      "message_id": "1550392002632487032",
      "digest": "d1583e2be251992c"
    }
  }
}
```

* `known` — alle je gesehenen URLs. Was hier steht, gilt nicht mehr als neu.
* `products` — die geposteten Produkte samt Discord-Message-ID. Damit kann eine
  bestehende Nachricht später aktualisiert werden, statt dasselbe Produkt ein
  zweites Mal zu posten.
* Ein Produkt wird **erst dann** als gesehen eingetragen, wenn es wirklich
  gepostet wurde. Fehlt das Secret oder ist die Obergrenze erreicht, kommt es im
  nächsten Lauf dran.

Alles vergessen und von vorn anfangen: Workflow mit `reset` starten (ohne
`post_existing` wird der aktuelle Stand dann einfach neu gemerkt).

## Diagnose ohne Knopfdruck

Jede Änderung an **`debug-run.txt`** startet einen Lauf, der **niemals postet und
`state.json` nie verändert**. Zwei Zeilen in der Datei steuern, was passiert:

```
modus: inspect              Sitemap-Übersicht und je Stichprobe, welcher
                            Selektor was gefunden hat — inklusive fertigem Embed
modus: inspect+dump         zusätzlich ein aufgeräumtes HTML-Abbild
urls: <URL>, <URL>          genau diese Produktseiten ansehen
stichprobe: 30              so viele zufällige Produktseiten abrufen und ihre
                            Kategorien zählen
```

Ohne `modus:`-Zeile läuft ein normaler Dry-Run. Eine Vorschau der Embeds nach dem
Erstlauf gibt es lokal mit `--dry-run --reset --post-existing --limit 3` oder eben
über `modus: inspect`.

Die Übersicht steht immer am **Ende** des Logs — ein Diagnose-Log wird schnell
mehrere hundert Zeilen lang, und nur das Ende ist bequem lesbar.

## Lokal ausführen

```bash
pip install -r requirements.txt

# Probelauf ohne Discord und ohne state.json zu verändern
python notifier.py --dry-run --verbose

# Shop analysieren (Sitemap, Selektoren, fertige Embeds)
python notifier.py --inspect --samples 3

# Kategorien des Sortiments zählen
python notifier.py --inspect --samples 0 --survey 30

# Probe mit echten Produkten aus "Neu im Shop"
python notifier.py --post-from https://www.twomoons.ch/ --heading "Neu im Shop" --limit 3 --dry-run

# Echter Lauf
export DISCORD_WEBHOOK_RELEASES="https://discord.com/api/webhooks/..."
python notifier.py

# Tests
python -m unittest discover -s tests
```

## Wenn etwas nicht klappt

**Es wird nichts gepostet.** Im Log nachsehen: Steht dort „Erster Lauf", war es
der Erstlauf — der merkt nur. Steht dort „`DISCORD_WEBHOOK_RELEASES` ist nicht
gesetzt", fehlt das Secret (Name exakt so schreiben).

**Ein Produkt kam doppelt.** Fast immer ein **Re-run** eines alten Laufs: Der
wiederholt den damaligen Commit mitsamt altem `state.json`. Der Workflow weist
Re-runs deshalb ab. Statt „Re-run jobs" immer „Run workflow" benutzen.

**Badges fehlen im Embed.** Die Produktseite kennt keine Badges; sie werden über
die Shop-Suche nachgeschlagen. Findet die Suche das Produkt nicht (ungewöhnlicher
Name), bleibt die Badge-Zeile weg — der Rest des Embeds stimmt trotzdem. Im
Diagnose-Lauf steht dann „Listenkarte in der Suche gefunden: nein".

**Preis sieht falsch aus.** Produkte mit Staffelpreisen führen mehrere Beträge auf
der Seite. Gelesen wird der ausgezeichnete Wert (`meta[itemprop=price]`); „Ab" wird
nur übernommen, wenn tatsächlich „Ab CHF …" dasteht (und nicht „Ab 4 Stück").

**Der Shop hat sein Layout geändert.** Ein `modus: inspect`-Lauf zeigt je Feld,
welcher Selektor gegriffen hat. Was leer bleibt, wird in `config.json` unter
`product.*_selectors` nachgezogen — dafür ist kein Eingriff in `notifier.py` nötig.

**Nichts läuft mehr stündlich.** Prüfen, ob der Branch mit dem Workflow der
Default-Branch ist (siehe oben). GitHub schaltet geplante Läufe ausserdem in
Repos ohne Aktivität nach 60 Tagen ab.

## Was am echten Shop gemessen wurde

Diese Punkte haben den Bau bestimmt und sind der Grund für einige
Umständlichkeiten im Code:

* Die Sitemap ist ein Index auf eine **gepackte** Teil-Sitemap (`.xml.gz`) mit
  rund 6600 URLs. Davon sind knapp 6300 Produkte auf der Wurzelebene.
* Unter jedem Produkt stehen **Empfehlungs-Slider**, deren Karten exakt dieselben
  CSS-Klassen tragen wie das gezeigte Produkt. Wer die ganze Seite auswertet,
  bekommt fremde Badges und fremde Sprachflaggen — anfangs meldete jedes Produkt
  „Vorbestellung, Neu, Aldria Deal!". Ausgewertet wird deshalb nur der CMS-Block,
  in dem der Kaufen-Bereich steckt.
* Die Produktseite rendert **gar keine Badges** für das gezeigte Produkt. „Neu"
  und „Vorbestellung" gibt es nur auf Listenkarten — daher der Umweg über die Suche.
* Die Überschrift ist dreiteilig: `<span class="subheadings">` mit der Serie, der
  eigentliche Name, dann eine Variantenzeile („Booster Box | Deutsch"). Ohne
  Zerlegung stünde alles zusammen im Produktnamen.
* Die Sprache steht entweder in der Variantenzeile oder als Flaggenbild
  (`img.twomoons-language-badge`) mit der Sprache im `title`.
* Shopware-Vorlagen enthalten HTML-Kommentare wie `@deprecated tag:v6.8.0`. Die
  sind in BeautifulSoup ebenfalls Textknoten und müssen ausdrücklich übersprungen
  werden.
