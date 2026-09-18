"""Tests gegen gespeicherte Abbilder echter Seiten von twomoons.ch.

Die Abbilder in tests/fixtures/ sind gekürzte Nachbauten dessen, was die
Diagnose-Läufe im echten Shop gefunden haben — samt der Eigenheiten, über die
der Parser gestolpert ist (Empfehlungs-Slider mit fremden Badges,
Staffelpreise, dreiteilige Überschrift, HTML-Kommentare).
"""

import gzip
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
CONFIG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))

CHEWBACCA = "https://www.twomoons.ch/homeworlds-spotlight-deck-chewbacca-chewbacca-englisch"
DISPLAY = "https://www.twomoons.ch/disney-lorcana-hyperia-city-booster-display-booster-box-deutsch"
EVENT = "https://www.twomoons.ch/zuerich-cube-open"
KATEGORIE = "https://www.twomoons.ch/star-wars/preorder"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse(name: str, url: str = CHEWBACCA, config: dict | None = None) -> notifier.Product:
    return notifier.parse_product(fixture(name), url, config or CONFIG)


def default_args(**overrides) -> Namespace:
    args = Namespace(
        config=REPO / "config.json",
        state=REPO / "state.json",
        dry_run=False,
        post_from="",
        heading="",
        post_existing=False,
        reset=False,
        limit=0,
        verbose=False,
        inspect=False,
        inspect_url=None,
        samples=5,
        survey=0,
        dump_html=False,
        dump_selector=None,
        dump_bytes=3000,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class ProduktseiteTest(unittest.TestCase):
    def test_dreiteilige_ueberschrift_wird_zerlegt(self):
        product = parse("produkt_mit_badges.html")
        self.assertEqual(product.name, "Homeworlds Spotlight Deck - Chewbacca")
        self.assertEqual(product.manufacturer, "Star Wars Unlimited")
        self.assertEqual(product.variant_text, "Chewbacca | Englisch")

    def test_kommentare_landen_nicht_im_namen(self):
        self.assertNotIn("deprecated", parse("produkt_mit_badges.html").name)

    def test_preis_bild_und_sprache(self):
        product = parse("produkt_mit_badges.html")
        self.assertEqual(product.price, "CHF 24.90")
        self.assertEqual(product.languages, ["Englisch"])
        self.assertEqual(
            product.image_url,
            "https://www.twomoons.ch/media/9b/a3/ab/1788255962/Chewbacca2EN.webp?ts=1788255962",
        )

    def test_kategorien_und_eigenschaften(self):
        product = parse("produkt_mit_badges.html")
        self.assertEqual(product.categories, ["Home", "Sammelkarten", "Star Wars: Unlimited"])
        self.assertEqual(product.properties["Sprache"], "Englisch")

    def test_badges_der_empfehlungs_slider_bleiben_draussen(self):
        """Der Fehler, der im echten Shop jedem Produkt dieselben Badges gab."""
        product = parse("produkt_mit_badges.html")
        self.assertEqual(product.badges, [])
        self.assertNotIn("Französisch", product.languages)

    def test_staffelpreis_verfaelscht_den_preis_nicht(self):
        product = parse("produkt_staffelpreis.html", DISPLAY)
        # "Ab 4" in der Staffeltabelle ist eine Stückzahl, kein Ab-Preis.
        self.assertEqual(product.price, "CHF 114.90")

    def test_echter_ab_preis_wird_als_solcher_angezeigt(self):
        product = parse("produkt_ab_preis.html")
        self.assertEqual(product.price, "Ab CHF 114.90")

    def test_sprache_aus_den_varianten_optionen(self):
        self.assertEqual(parse("produkt_ab_preis.html").languages, ["Japanisch"])

    def test_kategorieseite_ist_kein_produkt(self):
        product = parse("keine_produktseite.html", KATEGORIE)
        self.assertFalse(product.is_product)
        self.assertEqual(product.name, "")


class FilterTest(unittest.TestCase):
    def test_eventticket_wird_ueber_die_kategorie_gefiltert(self):
        product = parse("eventticket.html", EVENT)
        self.assertTrue(product.is_product)
        self.assertEqual(notifier.blocked_category(product, CONFIG), "Events")

    def test_normales_produkt_bleibt(self):
        self.assertEqual(notifier.blocked_category(parse("produkt_mit_badges.html"), CONFIG), "")

    def test_include_kategorien_schraenken_ein(self):
        config = dict(CONFIG, filters={"exclude_categories": [], "include_categories": ["Brettspiele"]})
        product = parse("produkt_mit_badges.html", config=config)
        self.assertEqual(notifier.blocked_category(product, config), "(keine erlaubte Kategorie)")

    def test_url_muster(self):
        config = {"filters": {"include_patterns": [], "exclude_patterns": [r"/blog/"]}}
        urls = {"https://www.twomoons.ch/booster": "", "https://www.twomoons.ch/blog/neues": ""}
        kept, dropped = notifier.filter_urls(urls, config)
        self.assertEqual(list(kept), ["https://www.twomoons.ch/booster"])
        self.assertEqual(dropped, {"/blog/": 1})

    def test_kaputtes_muster_stoppt_den_lauf_nicht(self):
        config = {"filters": {"include_patterns": [], "exclude_patterns": ["([unfertig"]}}
        kept, _ = notifier.filter_urls({"https://www.twomoons.ch/a": ""}, config)
        self.assertEqual(list(kept), ["https://www.twomoons.ch/a"])


class ListenkarteTest(unittest.TestCase):
    def test_badges_und_sprache_von_der_passenden_karte(self):
        badges, languages = notifier.parse_card_badges(
            fixture("suche.html"), "https://www.twomoons.ch/search?search=x", CHEWBACCA, CONFIG
        )
        self.assertEqual(badges, ["Neu", "Vorbestellung"])
        self.assertEqual(languages, ["Englisch"])

    def test_fremde_karten_werden_nicht_verwechselt(self):
        badges, languages = notifier.parse_card_badges(
            fixture("suche.html"), "https://www.twomoons.ch/search?search=x",
            "https://www.twomoons.ch/gibt-es-nicht", CONFIG,
        )
        self.assertEqual((badges, languages), ([], []))

    def test_produktseite_und_karte_ergeben_zusammen_das_ganze_bild(self):
        pages = {CHEWBACCA: fixture("produkt_mit_badges.html"), "search": fixture("suche.html")}
        with mock.patch.object(notifier, "fetch_text", side_effect=lambda url, config: pages["search" if "search" in url else url]):
            product = notifier.fetch_product(CHEWBACCA, CONFIG)
        self.assertEqual(product.name, "Homeworlds Spotlight Deck - Chewbacca")
        self.assertEqual(product.badges, ["Neu", "Vorbestellung"])
        self.assertEqual(product.languages, ["Englisch"])


class StartseiteTest(unittest.TestCase):
    """Probe mit echten Produkten aus dem Bereich "Neu im Shop"."""

    def test_nur_der_gewaehlte_bereich(self):
        urls = notifier.urls_from_listing(fixture("startseite.html"), "https://www.twomoons.ch/", "Neu im Shop")
        self.assertEqual(urls, [
            "https://www.twomoons.ch/armory-deck-malice-englisch",
            DISPLAY,
        ])

    def test_ohne_bereich_alle_slider(self):
        urls = notifier.urls_from_listing(fixture("startseite.html"), "https://www.twomoons.ch/")
        self.assertIn("https://www.twomoons.ch/ein-bestseller", urls)
        self.assertEqual(len(urls), 3)

    def test_unbekannter_bereich_liefert_nichts(self):
        self.assertEqual(
            notifier.urls_from_listing(fixture("startseite.html"), "https://www.twomoons.ch/", "Gibt es nicht"),
            [],
        )

    def test_probe_postet_auch_bekannte_produkte_ohne_den_erstlauf_schutz_zu_setzen(self):
        state = notifier.empty_state()
        state["known"] = [CHEWBACCA]
        with tempfile.TemporaryDirectory() as tmp:
            args = default_args(
                state=Path(tmp) / "state.json",
                post_from="https://www.twomoons.ch/",
                heading="Neu im Shop",
                limit=1,
            )
            seiten = {
                "https://www.twomoons.ch/": fixture("startseite.html"),
                "https://www.twomoons.ch/armory-deck-malice-englisch": fixture("produkt_mit_badges.html"),
            }
            with mock.patch.object(notifier, "fetch_text", side_effect=lambda url, config: seiten.get(url, fixture("suche.html"))), \
                 mock.patch.object(notifier, "post_embed", return_value="777") as post, \
                 mock.patch.object(notifier.time, "sleep"), \
                 mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
                posted, _ = notifier.run_from_listing(CONFIG, state, args)

        self.assertEqual((posted, post.call_count), (1, 1))
        # Der Erstlauf-Schutz darf nicht anspringen, sonst gälte beim nächsten
        # regulären Lauf das ganze übrige Sortiment als schon bekannt.
        self.assertFalse(state["initialized"])


SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.twomoons.ch/sitemap/teil-1.xml.gz</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PART = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.twomoons.ch/booster-box/</loc><lastmod>2026-09-17T10:00:00+00:00</lastmod></url>
  <url><loc>https://www.twomoons.ch/kontakt</loc></url>
</urlset>
"""


class SitemapTest(unittest.TestCase):
    def test_index_und_eintraege_getrennt(self):
        self.assertEqual(
            notifier.parse_sitemap(SITEMAP_INDEX),
            (["https://www.twomoons.ch/sitemap/teil-1.xml.gz"], []),
        )
        children, entries = notifier.parse_sitemap(SITEMAP_PART)
        self.assertEqual(children, [])
        self.assertEqual(entries[0][1], "2026-09-17T10:00:00+00:00")

    def test_gzip_wird_am_magic_byte_erkannt(self):
        self.assertEqual(notifier.decode_body(gzip.compress(SITEMAP_PART.encode())), SITEMAP_PART)
        self.assertEqual(notifier.decode_body(SITEMAP_PART.encode()), SITEMAP_PART)

    def test_teil_sitemaps_werden_verfolgt(self):
        pages = {
            "https://www.twomoons.ch/sitemap.xml": SITEMAP_INDEX.encode(),
            "https://www.twomoons.ch/sitemap/teil-1.xml.gz": gzip.compress(SITEMAP_PART.encode()),
        }
        with mock.patch.object(notifier, "fetch_bytes", side_effect=lambda url, config: pages[url]):
            found = notifier.collect_sitemap_urls("https://www.twomoons.ch/sitemap.xml", {})
        self.assertEqual(
            sorted(found), ["https://www.twomoons.ch/booster-box", "https://www.twomoons.ch/kontakt"]
        )

    def test_schrägstrich_und_query_ergeben_keinen_zweiten_eintrag(self):
        self.assertEqual(
            notifier.normalise_url("https://www.twomoons.ch/booster-box/?c=12"),
            notifier.normalise_url("https://WWW.twomoons.ch/booster-box"),
        )

    def test_neueste_zuerst(self):
        urls = {"https://www.twomoons.ch/alt": "2026-09-01", "https://www.twomoons.ch/neu": "2026-09-18"}
        self.assertEqual(notifier.sort_candidates(urls)[0], "https://www.twomoons.ch/neu")


class UrlKodierungTest(unittest.TestCase):
    """Discord lehnt Embeds mit ungültigen URLs ab (400 {"embeds": ["0"]})."""

    def test_leerzeichen_werden_kodiert(self):
        self.assertEqual(
            notifier.safe_url("https://www.twomoons.ch/media/1/Armory Deck Malice.webp?ts=17"),
            "https://www.twomoons.ch/media/1/Armory%20Deck%20Malice.webp?ts=17",
        )

    def test_klammern_und_umlaute(self):
        kodiert = notifier.safe_url("https://www.twomoons.ch/media/30th Display_(1).jpg")
        self.assertNotIn(" ", kodiert)
        self.assertIn("(1)", kodiert)
        self.assertNotIn(" ", notifier.safe_url("https://www.twomoons.ch/media/Würfel Set.png"))

    def test_bereits_kodierte_urls_bleiben_wie_sie_sind(self):
        fertig = "https://www.twomoons.ch/media/Bereits%20Kodiert.png"
        self.assertEqual(notifier.safe_url(fertig), fertig)

    def test_embed_bekommt_die_kodierte_bild_url(self):
        product = notifier.Product(
            url=CHEWBACCA,
            name="Armory Deck Malice",
            image_url="https://www.twomoons.ch/media/1/Armory Deck Malice.webp?ts=17",
        )
        embed = notifier.build_embed(product, CONFIG)
        self.assertNotIn(" ", embed["thumbnail"]["url"])
        self.assertNotIn(" ", embed["url"])


class AufraeumenTest(unittest.TestCase):
    """Im Kanal sollen nur die neuesten Meldungen stehen bleiben."""

    def zustand(self, anzahl: int) -> dict:
        state = notifier.empty_state()
        for index in range(anzahl):
            state["products"][f"https://www.twomoons.ch/produkt-{index:02d}"] = {
                "name": f"Produkt {index:02d}",
                "message_id": f"{1000 + index}",
                # Je höher der Index, desto neuer.
                "first_seen": f"2026-09-{index + 1:02d}T07:00:00Z",
            }
        state["known"] = list(state["products"])
        return state

    def aufraeumen(self, state, config, **overrides):
        args = default_args(**overrides)
        with mock.patch.object(notifier, "delete_message", return_value=True) as geloescht, \
             mock.patch.object(notifier.time, "sleep"):
            entfernt = notifier.cleanup_channel(config, state, args, "https://discord.test/hook")
        return entfernt, geloescht

    def test_die_zwanzig_neuesten_bleiben(self):
        state = self.zustand(25)
        config = dict(CONFIG, cleanup={"enabled": True, "keep_newest": 20, "delete_after_days": 0})
        entfernt, geloescht = self.aufraeumen(state, config)

        self.assertEqual(entfernt, 5)
        self.assertEqual(len(state["products"]), 20)
        # Gelöscht werden die ältesten, nicht die neuesten.
        self.assertNotIn("https://www.twomoons.ch/produkt-00", state["products"])
        self.assertIn("https://www.twomoons.ch/produkt-24", state["products"])
        self.assertEqual([call.args[1] for call in geloescht.call_args_list], ["1000", "1001", "1002", "1003", "1004"])

    def test_geloeschte_produkte_bleiben_bekannt(self):
        state = self.zustand(22)
        config = dict(CONFIG, cleanup={"enabled": True, "keep_newest": 20, "delete_after_days": 0})
        self.aufraeumen(state, config)
        # Sonst würde das Produkt beim nächsten Lauf erneut gemeldet.
        self.assertIn("https://www.twomoons.ch/produkt-00", state["known"])

    def test_unter_der_grenze_wird_nichts_geloescht(self):
        state = self.zustand(5)
        config = dict(CONFIG, cleanup={"enabled": True, "keep_newest": 20, "delete_after_days": 0})
        entfernt, geloescht = self.aufraeumen(state, config)
        self.assertEqual(entfernt, 0)
        geloescht.assert_not_called()

    def test_abgeschaltet_loescht_nie(self):
        state = self.zustand(25)
        config = dict(CONFIG, cleanup={"enabled": False, "keep_newest": 20, "delete_after_days": 0})
        entfernt, geloescht = self.aufraeumen(state, config)
        self.assertEqual(entfernt, 0)
        geloescht.assert_not_called()

    def test_dry_run_loescht_nichts(self):
        state = self.zustand(25)
        config = dict(CONFIG, cleanup={"enabled": True, "keep_newest": 20, "delete_after_days": 0})
        entfernt, geloescht = self.aufraeumen(state, config, dry_run=True)
        self.assertEqual(entfernt, 0)
        geloescht.assert_not_called()
        self.assertEqual(len(state["products"]), 25)

    def test_zusaetzlich_nach_alter(self):
        import time as uhr

        def vor_tagen(tage: float) -> str:
            return uhr.strftime("%Y-%m-%dT%H:%M:%SZ", uhr.localtime(uhr.time() - tage * 86400))

        state = self.zustand(3)
        state["products"]["https://www.twomoons.ch/produkt-00"]["first_seen"] = vor_tagen(40)
        state["products"]["https://www.twomoons.ch/produkt-01"]["first_seen"] = vor_tagen(31)
        state["products"]["https://www.twomoons.ch/produkt-02"]["first_seen"] = vor_tagen(1)
        config = dict(CONFIG, cleanup={"enabled": True, "keep_newest": 0, "delete_after_days": 30})
        entfernt, _ = self.aufraeumen(state, config)
        self.assertEqual(entfernt, 2)
        self.assertEqual(list(state["products"]), ["https://www.twomoons.ch/produkt-02"])


class EmbedTest(unittest.TestCase):
    def bauen(self, **kwargs):
        return notifier.build_embed(notifier.Product(url=CHEWBACCA, **kwargs), CONFIG)

    def test_reihenfolge_und_badge_zeile(self):
        embed = self.bauen(
            name="Booster Box", price="Ab CHF 114.90", languages=["Deutsch", "Englisch"],
            badges=["Neu", "Vorbestellung"], manufacturer="Lorcana",
            image_url="https://www.twomoons.ch/media/box.jpg",
        )
        self.assertEqual(
            embed["description"].split("\n"),
            [
                "🏷️ **Neu** · **Vorbestellung**",
                "**Preis:** Ab CHF 114.90",
                "**Sprachen:** Deutsch, Englisch",
                "**Hersteller:** Lorcana",
                f"**Link:** [Zum Produkt]({CHEWBACCA})",
            ],
        )
        self.assertEqual(embed["thumbnail"]["url"], "https://www.twomoons.ch/media/box.jpg")

    def test_fehlende_angaben_hinterlassen_keine_leerzeilen(self):
        embed = self.bauen(name="Sleeves", price="CHF 9.90")
        self.assertEqual(
            embed["description"].split("\n"),
            ["**Preis:** CHF 9.90", f"**Link:** [Zum Produkt]({CHEWBACCA})"],
        )
        self.assertNotIn("thumbnail", embed)

    def test_fingerabdruck_umfasst_das_ganze_embed(self):
        embed = self.bauen(name="Booster", price="CHF 5.00")
        self.assertNotEqual(notifier.embed_digest(embed), notifier.embed_digest(dict(embed, color=1)))


class AblaufTest(unittest.TestCase):
    """Ganzer Lauf mit gemocktem Shop und gemocktem Discord."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state_path = self.tmp / "state.json"
        self.urls = {CHEWBACCA: "2026-09-18", KATEGORIE: "2026-09-01"}
        self.pages = {
            CHEWBACCA: fixture("produkt_mit_badges.html"),
            KATEGORIE: fixture("keine_produktseite.html"),
            EVENT: fixture("eventticket.html"),
            DISPLAY: fixture("produkt_staffelpreis.html"),
        }

    def seite(self, url, config):
        if "search" in url:
            return fixture("suche.html")
        return self.pages[url]

    def lauf(self, state, **overrides):
        args = default_args(state=self.state_path, **overrides)
        with mock.patch.object(notifier, "discover_urls", return_value=self.urls), \
             mock.patch.object(notifier, "fetch_text", side_effect=self.seite), \
             mock.patch.object(notifier, "post_embed", return_value="999") as post, \
             mock.patch.object(notifier.time, "sleep"):
            posted, report = notifier.run(CONFIG, state, args)
        return posted, report, post

    def erstlauf(self, state):
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            return self.lauf(state)

    def test_erster_lauf_merkt_nur(self):
        state = notifier.empty_state()
        posted, _, post = self.erstlauf(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(len(state["known"]), 2)
        self.assertTrue(state["initialized"])

    def test_zweiter_lauf_postet_nur_das_neue_produkt(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        self.urls[DISPLAY] = "2026-09-19"
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, _, post = self.lauf(state)

        self.assertEqual(posted, 1)
        self.assertEqual(post.call_count, 1)
        self.assertIn(DISPLAY, state["products"])
        self.assertEqual(state["products"][DISPLAY]["message_id"], "999")
        self.assertEqual(state["products"][DISPLAY]["price"], "CHF 114.90")

    def test_eventticket_wird_gemerkt_aber_nicht_gepostet(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        self.urls[EVENT] = "2026-09-19"
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, report, post = self.lauf(state)

        post.assert_not_called()
        self.assertEqual(posted, 0)
        self.assertIn(EVENT, state["known"])
        self.assertTrue(any("gefiltert (Events)" in line for line in report))

    def test_dry_run_aendert_nichts(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        self.urls[DISPLAY] = "2026-09-19"
        vorher = list(state["known"])
        gespeichert = self.state_path.read_text(encoding="utf-8")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, _, post = self.lauf(state, dry_run=True)

        post.assert_not_called()
        self.assertEqual(posted, 1)  # gezählt, aber nur im Log
        self.assertEqual(state["known"], vorher)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), gespeichert)

    def test_obergrenze_laesst_den_rest_fuer_den_naechsten_lauf(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        for index, url in enumerate([DISPLAY, EVENT.replace("zuerich", "zuerich-2"), CHEWBACCA + "-2"]):
            self.urls[url] = f"2026-09-2{index}"
            self.pages[url] = fixture("produkt_staffelpreis.html")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, _, post = self.lauf(state, limit=2)

        self.assertEqual(posted, 2)
        self.assertEqual(len(state["products"]), 2)

    def test_ohne_webhook_bleibt_das_produkt_offen(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        self.urls[DISPLAY] = "2026-09-19"
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": ""}):
            posted, report, post = self.lauf(state)

        post.assert_not_called()
        self.assertEqual(posted, 0)
        # Nicht als gesehen markiert — der nächste Lauf holt es nach.
        self.assertNotIn(DISPLAY, state["known"])
        self.assertTrue(any("fehlt" in line for line in report))

    def test_ein_abgelehntes_produkt_stoppt_die_uebrigen_nicht(self):
        """Der Fehler, der den ersten echten Lauf nach vier Posts beendet hat."""
        state = notifier.empty_state()
        self.erstlauf(state)
        for index, url in enumerate([DISPLAY, CHEWBACCA + "-2", CHEWBACCA + "-3"]):
            self.urls[url] = f"2026-09-2{index}"
            self.pages[url] = fixture("produkt_staffelpreis.html")

        args = default_args(state=self.state_path)
        abgelehnt = RuntimeError('Discord lehnte den Post ab (400): {"embeds": ["0"]}')
        with mock.patch.object(notifier, "discover_urls", return_value=self.urls), \
             mock.patch.object(notifier, "fetch_text", side_effect=self.seite), \
             mock.patch.object(notifier, "post_embed", side_effect=["111", abgelehnt, "333"]) as post, \
             mock.patch.object(notifier.time, "sleep"), \
             mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, report = notifier.run(CONFIG, state, args)

        self.assertEqual(post.call_count, 3)
        self.assertEqual(posted, 2)
        self.assertTrue(any("FEHLER" in line for line in report))
        # Der Stand muss trotz des Fehlers auf der Platte liegen.
        gespeichert = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(gespeichert["products"]), 2)

    def test_stand_ueberlebt_einen_absturz_nach_dem_posten(self):
        state = notifier.empty_state()
        self.erstlauf(state)
        self.urls[DISPLAY] = "2026-09-19"

        args = default_args(state=self.state_path)
        with mock.patch.object(notifier, "discover_urls", return_value=self.urls), \
             mock.patch.object(notifier, "fetch_text", side_effect=self.seite), \
             mock.patch.object(notifier, "post_embed", return_value="999"), \
             mock.patch.object(notifier, "cleanup_channel", side_effect=RuntimeError("Discord kaputt")), \
             mock.patch.object(notifier.time, "sleep"), \
             mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            with self.assertRaises(RuntimeError):
                notifier.run(CONFIG, state, args)

        gespeichert = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn(DISPLAY, gespeichert["products"])
        self.assertIn(DISPLAY, gespeichert["known"])

    def test_post_existing_merkt_sich_auch_die_nicht_geposteten(self):
        """Sonst tröpfelte das Altsortiment jahrelang in den Kanal."""
        state = notifier.empty_state()
        for index, url in enumerate([DISPLAY, CHEWBACCA + "-2", CHEWBACCA + "-3"]):
            self.urls[url] = f"2026-09-2{index}"
            self.pages[url] = fixture("produkt_staffelpreis.html")

        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, report = self.lauf(state, post_existing=True, limit=1)[:2]

        self.assertEqual(posted, 1)
        self.assertEqual(sorted(state["known"]), sorted(self.urls))
        self.assertTrue(any("nur gemerkt" in line for line in report))

    def test_kategorieseite_wird_gemerkt_aber_nicht_gepostet(self):
        state = notifier.empty_state()
        state["initialized"] = True
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            _, _, post = self.lauf(state)

        self.assertEqual(post.call_count, 1)  # nur die Produktseite
        self.assertIn(KATEGORIE, state["known"])
        self.assertNotIn(KATEGORIE, state["products"])


if __name__ == "__main__":
    unittest.main()
