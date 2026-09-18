"""Tests für Quellen, Filter, Embed und Ablauf — ohne Netz, gegen Abbilder."""

import gzip
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
CONFIG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def default_args(**overrides) -> Namespace:
    args = Namespace(
        config=REPO / "config.json",
        state=REPO / "state.json",
        dry_run=False,
        post_existing=False,
        reset=False,
        limit=0,
        verbose=False,
        inspect=False,
        inspect_url=None,
        samples=5,
        dump_html=False,
        dump_bytes=12000,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.twomoons.ch/sitemap/teil-1.xml.gz</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PART = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.twomoons.ch/booster-box/</loc><lastmod>2026-09-17T10:00:00+00:00</lastmod></url>
  <url><loc>https://www.twomoons.ch/einzelkarten/lightning-bolt</loc><lastmod>2026-09-18T10:00:00+00:00</lastmod></url>
  <url><loc>https://www.twomoons.ch/kontakt</loc></url>
</urlset>
"""


class SitemapTest(unittest.TestCase):
    def test_index_and_entries_are_separated(self):
        children, entries = notifier.parse_sitemap(SITEMAP_INDEX)
        self.assertEqual(children, ["https://www.twomoons.ch/sitemap/teil-1.xml.gz"])
        self.assertEqual(entries, [])

        children, entries = notifier.parse_sitemap(SITEMAP_PART)
        self.assertEqual(children, [])
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0][1], "2026-09-17T10:00:00+00:00")

    def test_gzip_is_recognised_by_magic_bytes_not_by_suffix(self):
        packed = gzip.compress(SITEMAP_PART.encode("utf-8"))
        self.assertEqual(notifier.decode_body(packed), SITEMAP_PART)
        self.assertEqual(notifier.decode_body(SITEMAP_PART.encode("utf-8")), SITEMAP_PART)

    def test_index_is_followed_into_the_parts(self):
        pages = {
            "https://www.twomoons.ch/sitemap.xml": SITEMAP_INDEX.encode("utf-8"),
            "https://www.twomoons.ch/sitemap/teil-1.xml.gz": gzip.compress(SITEMAP_PART.encode("utf-8")),
        }
        with mock.patch.object(notifier, "fetch_bytes", side_effect=lambda url, config: pages[url]):
            found = notifier.collect_sitemap_urls("https://www.twomoons.ch/sitemap.xml", {})
        self.assertEqual(
            sorted(found),
            [
                "https://www.twomoons.ch/booster-box",
                "https://www.twomoons.ch/einzelkarten/lightning-bolt",
                "https://www.twomoons.ch/kontakt",
            ],
        )

    def test_trailing_slash_and_query_do_not_create_a_second_entry(self):
        self.assertEqual(
            notifier.normalise_url("https://www.twomoons.ch/booster-box/?c=12"),
            notifier.normalise_url("https://WWW.twomoons.ch/booster-box"),
        )


class FilterTest(unittest.TestCase):
    def test_exclude_patterns_are_counted_per_pattern(self):
        config = {"filters": {"include_patterns": [], "exclude_patterns": [r"/einzelkarten/"]}}
        urls = {
            "https://www.twomoons.ch/booster-box": "",
            "https://www.twomoons.ch/einzelkarten/lightning-bolt": "",
        }
        kept, dropped = notifier.filter_urls(urls, config)
        self.assertEqual(list(kept), ["https://www.twomoons.ch/booster-box"])
        self.assertEqual(dropped, {"/einzelkarten/": 1})

    def test_include_patterns_limit_the_selection(self):
        config = {"filters": {"include_patterns": [r"booster"], "exclude_patterns": []}}
        urls = {"https://www.twomoons.ch/booster-box": "", "https://www.twomoons.ch/sleeves": ""}
        kept, _ = notifier.filter_urls(urls, config)
        self.assertEqual(list(kept), ["https://www.twomoons.ch/booster-box"])

    def test_broken_pattern_does_not_stop_the_run(self):
        config = {"filters": {"include_patterns": [], "exclude_patterns": ["([unfertig"]}}
        kept, _ = notifier.filter_urls({"https://www.twomoons.ch/a": ""}, config)
        self.assertEqual(list(kept), ["https://www.twomoons.ch/a"])

    def test_newest_lastmod_is_offered_first(self):
        urls = {
            "https://www.twomoons.ch/alt": "2026-09-01",
            "https://www.twomoons.ch/neu": "2026-09-18",
        }
        self.assertEqual(notifier.sort_candidates(urls)[0], "https://www.twomoons.ch/neu")


class EmbedTest(unittest.TestCase):
    def build(self, **kwargs):
        product = notifier.Product(url="https://www.twomoons.ch/beispiel", **kwargs)
        return notifier.build_embed(product, CONFIG)

    def test_all_fields_are_shown_in_order(self):
        embed = self.build(
            name="Lorcana Booster Box",
            price="Ab CHF 114.90",
            languages=["Deutsch", "Englisch"],
            badges=["Neu", "Vorbestellung"],
            manufacturer="Lorcana",
            image_url="https://www.twomoons.ch/media/box.jpg",
        )
        self.assertEqual(embed["title"], "Lorcana Booster Box")
        self.assertEqual(
            embed["description"].split("\n"),
            [
                "🏷️ **Neu** · **Vorbestellung**",
                "**Preis:** Ab CHF 114.90",
                "**Sprachen:** Deutsch, Englisch",
                "**Hersteller:** Lorcana",
                "**Link:** [Zum Produkt](https://www.twomoons.ch/beispiel)",
            ],
        )
        self.assertEqual(embed["thumbnail"]["url"], "https://www.twomoons.ch/media/box.jpg")

    def test_missing_values_leave_no_empty_lines(self):
        embed = self.build(name="Sleeves", price="CHF 9.90")
        self.assertEqual(
            embed["description"].split("\n"),
            ["**Preis:** CHF 9.90", "**Link:** [Zum Produkt](https://www.twomoons.ch/beispiel)"],
        )
        self.assertNotIn("thumbnail", embed)
        self.assertNotIn("\n\n", embed["description"])

    def test_digest_covers_the_whole_embed_not_only_the_text(self):
        embed = self.build(name="Booster", price="CHF 5.00")
        other = dict(embed, color=123456)
        self.assertNotEqual(notifier.embed_digest(embed), notifier.embed_digest(other))


class RunTest(unittest.TestCase):
    """Ablauf mit gemocktem Shop und gemocktem Discord."""

    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.state_path = self.tmp / "state.json"
        self.urls = {
            "https://www.twomoons.ch/booster-box": "2026-09-18",
            "https://www.twomoons.ch/kontakt": "2026-09-01",
        }
        self.pages = {
            "https://www.twomoons.ch/booster-box": fixture("produkt_mit_badge.html"),
            "https://www.twomoons.ch/kontakt": fixture("keine_produktseite.html"),
        }

    def run_notifier(self, state, **overrides):
        args = default_args(state=self.state_path, **overrides)
        with mock.patch.object(notifier, "discover_urls", return_value=self.urls), mock.patch.object(
            notifier, "fetch_text", side_effect=lambda url, config: self.pages[url]
        ), mock.patch.object(notifier, "post_embed", return_value="999") as post, mock.patch.object(
            notifier, "time"
        ) as fake_time:
            fake_time.sleep.return_value = None
            fake_time.strftime.return_value = "2026-09-18T07:00:00Z"
            fake_time.time.return_value = 0.0
            posted, report = notifier.run(CONFIG, state, args)
        return posted, report, post

    def test_first_run_only_remembers(self):
        state = notifier.empty_state()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, _, post = self.run_notifier(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(len(state["known"]), 2)
        self.assertTrue(state["initialized"])

    def test_second_run_posts_only_the_new_product(self):
        state = notifier.empty_state()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            self.run_notifier(state)
            self.urls["https://www.twomoons.ch/neues-display"] = "2026-09-19"
            self.pages["https://www.twomoons.ch/neues-display"] = fixture("produkt_mit_badge.html")
            posted, _, post = self.run_notifier(state)

        self.assertEqual(posted, 1)
        self.assertEqual(post.call_count, 1)
        self.assertIn("https://www.twomoons.ch/neues-display", state["products"])
        self.assertEqual(state["products"]["https://www.twomoons.ch/neues-display"]["message_id"], "999")

    def test_dry_run_changes_nothing(self):
        state = notifier.empty_state()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            self.run_notifier(state)
            self.urls["https://www.twomoons.ch/neues-display"] = "2026-09-19"
            self.pages["https://www.twomoons.ch/neues-display"] = fixture("produkt_mit_badge.html")
            known_before = list(state["known"])
            posted, _, post = self.run_notifier(state, dry_run=True)

        post.assert_not_called()
        self.assertEqual(posted, 1)
        self.assertEqual(state["known"], known_before)
        self.assertFalse(self.state_path.exists())

    def test_limit_leaves_the_rest_for_the_next_run(self):
        state = notifier.empty_state()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            self.run_notifier(state)
            for index in range(3):
                url = f"https://www.twomoons.ch/display-{index}"
                self.urls[url] = f"2026-09-2{index}"
                self.pages[url] = fixture("produkt_mit_badge.html")
            posted, _, post = self.run_notifier(state, limit=2)

        self.assertEqual(posted, 2)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(state["products"]), 2)

    def test_missing_webhook_keeps_products_unposted(self):
        state = notifier.empty_state()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            self.run_notifier(state)
        self.urls["https://www.twomoons.ch/neues-display"] = "2026-09-19"
        self.pages["https://www.twomoons.ch/neues-display"] = fixture("produkt_mit_badge.html")

        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": ""}):
            posted, report, post = self.run_notifier(state)

        post.assert_not_called()
        self.assertEqual(posted, 0)
        # Nicht als gesehen markiert — der nächste Lauf holt es nach.
        self.assertNotIn("https://www.twomoons.ch/neues-display", state["known"])
        self.assertTrue(any("fehlt" in line for line in report))

    def test_non_product_page_is_remembered_but_not_posted(self):
        state = notifier.empty_state()
        state["initialized"] = True
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_RELEASES": "https://discord.test/hook"}):
            posted, _, post = self.run_notifier(state)

        self.assertEqual(post.call_count, 1)  # nur die Produktseite
        self.assertIn("https://www.twomoons.ch/kontakt", state["known"])
        self.assertNotIn("https://www.twomoons.ch/kontakt", state["products"])


if __name__ == "__main__":
    unittest.main()
