# TwoMoons Release Notifier

Spiegelt die Seite [„Neu im Shop"](https://www.twomoons.ch/neu-im-shop/) von
twomoons.ch stündlich in einen Discord-Kanal — mit Name, Preis, Sprachen, Badges
(„Neu", „Vorbestellung"), Produktbild und Link.

Im Kanal stehen immer genau die **20 obersten Produkte dieser Seite**: Kommt ein
Produkt dazu, wird es gepostet; rutscht eines aus den obersten 20 heraus,
verschwindet seine Nachricht wieder.

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
6. [Was im Kanal steht](#was-im-kanal-steht)
7. [`state.json` — das Gedächtnis](#statejson--das-gedächtnis)
8. [Diagnose ohne Knopfdruck](#diagnose-ohne-knopfdruck)
9. [Lokal ausführen](#lokal-ausführen)
10. [Profilbild des Absenders](#profilbild-des-absenders)
11. [Wenn etwas nicht klappt](#wenn-etwas-nicht-klappt)
12. [Was am echten Shop gemessen wurde](#was-am-echten-shop-gemessen-wurde)

## Wie es funktioniert

Ein Lauf besteht aus vier Schritten:

1. **Seite holen.** `https://www.twomoons.ch/neu-im-shop/` liefert die Neuheiten
   in der Reihenfolge „neuste zuerst" — alle Produkte in einem Abruf, ohne
   Paginierung.
2. **Vergleichen.** Die obersten 20 werden gegen das gehalten, was laut
   `state.json` bereits im Kanal steht.
3. **Produktseiten auswerten.** Nur für die neu hinzugekommenen Produkte werden
   Name, Preis, Sprachen, Hersteller und Bild von der Produktseite gelesen. Die
   **Badges stehen nur auf den Listenkarten**, nicht auf der Produktseite — sie
   kommen deshalb direkt von der Karte der Listenseite. Nur wenn die Karte keine
   hergibt, wird das Produkt zusätzlich in der Shop-Suche nachgeschlagen.
4. **Posten und aufräumen.** Neue Produkte werden gepostet — das älteste zuerst,
   damit der Kanal von oben nach unten chronologisch liest. Produkte, die nicht
   mehr unter den obersten 20 stehen, verlieren ihre Nachricht. Danach wird
   `state.json` aktualisiert und vom Workflow zurück ins Repo committet.

Der Kanal pflegt sich damit von selbst: keine Alterslogik, kein Rückstau, keine
Obergrenze, die irgendwann Produkte verschluckt. Was im Shop oben steht, steht im
Kanal.

**Schutz gegen Fehlalarm:** Liefert die Seite plötzlich weniger als
`channel.min_listing_items` Produkte (Umbau, Wartungsseite, Bot-Schutz), bricht
der Lauf ab, ohne etwas zu posten oder zu löschen. Der Workflow wird dann rot —
gewollt, damit es auffällt.

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

**Actions** → **TwoMoons Release Notifier** → **Run workflow**. Für den ersten
Versuch `dry_run` anhaken: Der Lauf postet dann nichts und ändert `state.json`
nicht, zeigt im Log aber, was er täte.

Im Log muss stehen, wie viele Produkte die Seite geliefert hat (derzeit rund 88)
und welche davon in den Kanal kämen.

### 4. Ab jetzt läuft es von allein

Sieht der Probelauf gut aus, denselben Lauf ohne `dry_run` starten: Er füllt den
Kanal mit den obersten 20 Produkten. Danach prüft der Workflow stündlich die
Seite und hält den Kanal aktuell — neue Produkte kommen dazu, herausgefallene
verschwinden.

Mehr ist nicht zu tun.

### Optional: einzelne Produkte auf Verdacht posten

Zum Ausprobieren lässt sich ein beliebiger Produktbereich einer Seite posten,
auch wenn die Produkte längst bekannt sind:

| Schalter | Wert |
|---|---|
| `post_from` | `https://www.twomoons.ch/` |
| `heading` | `Neu im Shop` |
| `limit` | `3` |

Das umgeht den Spiegel-Abgleich und ist für den Alltag nicht nötig — die
Testnachrichten räumt der nächste reguläre Lauf wieder weg, sobald die Produkte
nicht mehr unter den obersten 20 stehen.

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

Die Seite „Neu im Shop" ist bereits eine Auswahl des Shops — viel zu filtern gibt
es da nicht mehr. Die Listen greifen trotzdem, falls etwas durchrutscht, und sind
im Sitemap-Modus die Hauptbremse. Standardmässig bleiben nur
Veranstaltungstickets draussen (Breadcrumb-Kategorie „Events") — dafür gibt es
den Schwester-Melder
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
| `channel.*` | siehe [Was im Kanal steht](#was-im-kanal-steht) |
| `shop.sitemap_url` | nur für `mode: "sitemap"`: Einstieg in die Sitemap (auch `.gz`) |
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
| `discord.color`, `footer`, `username` | Aussehen der Nachricht |
| `discord.avatar_url` | Profilbild des Absenders (leer = das in Discord hinterlegte) |
| `discord.max_posts_per_run` | Obergrenze pro Lauf im Sitemap-Modus (Standard 10); im Spiegel-Modus zählt `channel.keep` |
| `discord.max_detail_fetches_per_run` | Obergrenze an Seitenabrufen pro Lauf (Standard 60) |
| `update.*` | Nachkontrolle bereits geposteter Produkte (Preisänderungen) |
| `cleanup.*` | nur für `mode: "sitemap"`: Kanal nach Anzahl/Alter begrenzen |
| `inspect.*` | nur für Diagnose-Läufe |

Eine Änderung an `config.json` wirkt ab dem nächsten Lauf. Änderungen an Farbe
oder Fusszeile schlagen auch auf **bereits gepostete** Nachrichten durch, sobald
die Nachkontrolle das Produkt erneut prüft: Der Fingerabdruck wird über das ganze
Embed gebildet, nicht nur über den Text.

## Was im Kanal steht

```json
"channel": {
  "mode": "mirror",
  "listing_url": "https://www.twomoons.ch/neu-im-shop/",
  "heading": "",
  "keep": 20,
  "min_listing_items": 10
}
```

* `listing_url` — die Seite, die gespiegelt wird. Willst du eine andere
  Sortierung, öffne die Seite im Browser, wähle sie dort aus und kopiere die URL
  aus der Adresszeile hierher (sie enthält dann einen `?order=`-Teil).
  Es geht auch jede andere Kategorieseite des Shops.
* `keep` — so viele Produkte stehen im Kanal (Standard 20).
* `heading` — nur nötig, wenn die Seite mehrere Produktbereiche hat (z. B. die
  Startseite): dann hier die Überschrift des gewünschten Bereichs eintragen,
  etwa `"Neu im Shop"`.
* `min_listing_items` — Untergrenze für den Schutz oben.
* `mode` — `"mirror"` spiegelt die Seite. `"sitemap"` schaltet auf das
  ursprüngliche Verfahren um: ganzer Shop über die Sitemap, nur Neuzugänge
  melden, Kanal über `cleanup.*` begrenzen. Für den Alltag ist `mirror` gedacht.

Gelöscht wird nur, was dieser Webhook selbst gepostet hat — andere Nachrichten im
Kanal bleiben unberührt. Schlägt ein Löschen fehl (jemand hat die Nachricht schon
von Hand entfernt), läuft der Rest trotzdem durch.

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

* `known` — alle je gesehenen URLs. Im Spiegel-Modus wird die Liste nicht
  gebraucht; sie stammt aus dem Sitemap-Modus und bleibt nur stehen, damit ein
  Wechsel dorthin nicht den ganzen Shop erneut für neu hält.
* `products` — **das ist der Kanal**: Was hier steht, hat eine Nachricht in
  Discord, samt Message-ID. Verschwindet ein Produkt aus den obersten `keep`,
  wird die Nachricht gelöscht und der Eintrag entfernt.
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

## Profilbild des Absenders

Im Ordner `assets/` liegen zwei Dateien:

| Datei | Wofür |
|---|---|
| `bot-avatar.png` | 512×512, das Bild, das Discord tatsächlich anzeigt |
| `bot-avatar.svg` | dieselbe Grafik als Vektor — zum Bearbeiten in Illustrator, Inkscape oder Figma |

Der Webhook holt sich das PNG über `discord.avatar_url` in der `config.json`
direkt aus diesem Repo. **Der Branchname steht in der URL** — wird der Branch
später umbenannt oder nach `main` gemerget, muss die URL mitgezogen werden,
sonst zeigt Discord wieder das Standardbild.

**Eigenes Bild verwenden**, zwei Wege:

1. *Über das Repo:* `bot-avatar.svg` bearbeiten, als 512×512-PNG exportieren,
   beide Dateien ersetzen, committen. Ab dem nächsten Post gilt das neue Bild —
   für **neue** Nachrichten; bereits gepostete behalten das alte Bild.
2. *Direkt in Discord:* Im Webhook (Kanal bearbeiten → Integrationen → Webhooks)
   ein Bild hochladen und in der `config.json` `"avatar_url": ""` setzen. Dann
   bestimmt Discord das Bild, das Repo redet nicht mehr mit.

Zum SVG: Die Sichel ist ein Pfad aus zwei Kreisbögen statt eines
zusammengesetzten Pfads. Grund steht als Kommentar in der Datei — `evenodd`
ergibt bei zwei Kreisen die symmetrische Differenz statt der Subtraktion, was
beim Bauen erst wie ein Ring aussah.

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
