import re

from django.test import SimpleTestCase

from .formatting import describe_status, format_size, format_timestamp, shorten_path


class FormatSizeTests(SimpleTestCase):
    def test_sizes_use_the_largest_sensible_unit(self):
        cases = {
            0: "0 B",
            1: "1 B",
            1023: "1023 B",
            1024: "1.0 KB",
            1536: "1.5 KB",
            5 * 1024**2: "5.0 MB",
            3 * 1024**3: "3.0 GB",
            5000 * 1024**3: "5000.0 GB",
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(format_size(size), expected)


class FormatTimestampTests(SimpleTestCase):
    def test_missing_values_show_a_dash(self):
        self.assertEqual(format_timestamp(None), "—")
        self.assertEqual(format_timestamp(""), "—")

    def test_server_timestamps_become_local_date_and_time(self):
        self.assertRegex(format_timestamp("2026-09-20T10:15:30.123456Z"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertRegex(format_timestamp("2026-09-20T10:15:30+00:00"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class DescribeStatusTests(SimpleTestCase):
    def test_known_statuses_have_text_and_a_color(self):
        for status, text in (
            ("completed", "Completed"),
            ("failed", "Failed"),
            ("in_progress", "In progress"),
            ("pending", "Pending"),
        ):
            with self.subTest(status=status):
                shown, color = describe_status(status)
                self.assertEqual(shown, text)
                self.assertTrue(re.fullmatch(r"#[0-9a-f]{6}", color))

    def test_unknown_statuses_are_still_readable(self):
        self.assertEqual(describe_status("half_done")[0], "Half done")


class ShortenPathTests(SimpleTestCase):
    def test_short_paths_are_left_alone(self):
        for path in ("C:\\docs", "C:\\docs\\a", "/home/sam", "/home/sam/docs", "notes.txt"):
            with self.subTest(path=path):
                self.assertEqual(shorten_path(path), path)

    def test_long_paths_keep_their_last_two_parts(self):
        self.assertEqual(shorten_path("C:\\Users\\sam\\Projects\\Thesis"), "…\\Projects\\Thesis")
        self.assertEqual(shorten_path("/home/sam/work/reports/2026"), "…/reports/2026")

    def test_a_trailing_separator_is_ignored_when_counting(self):
        self.assertEqual(shorten_path("C:\\a\\b\\c\\d\\"), "…\\c\\d")
