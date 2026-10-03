import json
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_client_report import build_report, render_html, safe_url


OBSERVED = "2026-09-30T14:30:32Z"


def row(hotel="a", identity="r1", rating=8):
    return {"hotelId": hotel, "hotelName": "Hotel " + hotel, "reviewId": identity,
            "overallRating": rating, "entryDate": "2026-09-29T09:00:00Z",
            "recommendation": True, "verifiedReservation": False,
            "sourceUrl": "https://www.holidaycheck.de/hr/" + hotel,
            "reviewUrl": "https://www.holidaycheck.de/hr/" + hotel + "#" + identity,
            "hotel": {"rating6": 5.6, "reviewCount": 2000, "recommendationRate": 96},
            "aspectScores": {"room": {"score": .5}}}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, data):
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")
        return name

    def manifest_run(self, rows, run_id="run1", output=None, report=None):
        item = {"runId": run_id, "observedAt": OBSERVED}
        if rows is not None:
            item["datasetFile"] = self.file(run_id + "-rows.json", rows)
        if output is not None:
            item["outputFile"] = self.file(run_id + "-output.json", output)
        if report is not None:
            item["reportFile"] = self.file(run_id + "-report.json", report)
        return item

    def build(self, runs, **fields):
        return build_report({"runs": runs, **fields}, self.root, OBSERVED)

    def test_real_two_hotel_sample_and_provenance(self):
        base = Path(__file__).resolve().parents[1]
        manifest_path = base / "client-report-sample/manifest.json"
        report = build_report(json.loads(manifest_path.read_text()), manifest_path.parent, OBSERVED)
        self.assertEqual(report["portfolio"]["hotelCount"], 2)
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 40)
        self.assertEqual(report["portfolio"]["collectionStatuses"], {"completed_capped": 2})
        self.assertEqual({h["observations"][0]["sample"]["reviewCount"] for h in report["hotels"]}, {20})
        self.assertEqual({h["observations"][0]["collection"]["maxReviewsPerHotel"] for h in report["hotels"]}, {20})
        self.assertEqual({h["observations"][0]["sample"]["averageRating10"] for h in report["hotels"]}, {8.47, 9.37})
        self.assertEqual(report["hotels"][0]["observations"][0]["sample"]["period"]["from"], "2026-09-28T18:31:11.000Z")
        self.assertEqual(len(report["runs"][0]["files"]["datasetFile"]["sha256"]), 64)
        self.assertNotIn("current rating", render_html(report))

    def test_duplicate_reviews_count_once_across_runs(self):
        runs = [self.manifest_run([row(), row()], "one"), self.manifest_run([row()], "two")]
        report = self.build(runs)
        self.assertEqual(report["portfolio"]["hotelCount"], 1)
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 1)
        self.assertEqual(report["portfolio"]["duplicateRowsWithinRuns"], 1)
        self.assertEqual(report["portfolio"]["repeatedIdentifiedReviewsAcrossRuns"], 1)
        self.assertEqual(len(report["hotels"][0]["observations"]), 2)

    def test_review_identity_is_scoped_to_hotel(self):
        report = self.build([self.manifest_run([row("a"), row("b")])])
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 2)

    def test_nulls_and_invalid_values_are_unavailable(self):
        null_row = row(rating=None)
        null_row.update(recommendation=None, verifiedReservation=None, entryDate=None, aspectScores=None, hotel=None)
        invalid = row(identity="invalid", rating=99)
        invalid.update(recommendation=1, verifiedReservation="yes", entryDate="not a date", aspectScores={"room": {"score": 100}})
        report = self.build([self.manifest_run([null_row, invalid])])
        sample = report["hotels"][0]["observations"][0]["sample"]
        self.assertEqual(sample["reviewCount"], 2)
        self.assertIsNone(sample["averageRating10"])
        self.assertIsNone(sample["recommendation"]["rate"])
        self.assertEqual(sample["recommendation"]["knownCount"], 0)
        self.assertIsNone(sample["period"]["from"])
        self.assertIsNone(sample["aspects"][0]["score"])

    def test_known_boolean_denominator_and_source_scope(self):
        a, b, c = row(identity="1", rating=1), row(identity="2", rating=9), row(identity="3", rating=None)
        b["recommendation"], c["recommendation"] = False, None
        report = self.build([self.manifest_run([a, b, c])])
        observation = report["hotels"][0]["observations"][0]
        self.assertEqual(observation["sample"]["averageRating10"], 5)
        self.assertEqual(observation["sample"]["ratingCount"], 2)
        self.assertEqual(observation["sample"]["recommendation"], {"positive": 1, "knownCount": 2, "rate": .5})
        self.assertEqual(observation["sourceHotelSummary"]["rating6"], 5.6)
        self.assertEqual(observation["sourceHotelSummary"]["reviewCount"], 2000)

    def test_empty_failed_run_preserves_status_without_inventing_hotel(self):
        report = self.build([self.manifest_run([], output={"status": "FAILED", "hotels": [], "failedHotels": 1})])
        self.assertEqual(report["portfolio"]["hotelCount"], 0)
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 0)
        self.assertIn("FAILED", report["warnings"][0])
        self.assertIn("No hotel collection status available", render_html(report))

    def test_mixed_collection_retains_failed_hotel_and_no_false_complete(self):
        output = {"status": "SUCCEEDED", "failedHotels": 1, "collection": {"mode": "limit", "maxReviewsPerHotel": 20},
                  "hotels": [{"hotelId": "a", "hotelName": "Hotel a", "sourceUrl": "https://www.holidaycheck.de/hr/a", "reviewsOutput": 1}],
                  "failedUrls": [{"url": "https://www.holidaycheck.de/hr/b", "error": "source unreachable"}]}
        run = self.manifest_run([row()], output=output)
        run["expectedHotels"] = [{"hotelId": "b", "hotelName": "Hotel b", "sourceUrl": "https://www.holidaycheck.de/hr/b"}]
        report = self.build([run])
        self.assertEqual(report["portfolio"]["collectionStatuses"], {"completed_in_partial_run": 1, "failed": 1})
        self.assertEqual(report["hotels"][1]["observations"][0]["sample"]["reviewCount"], 0)
        self.assertFalse(any(o["collection"]["completeSourceHistory"] for h in report["hotels"] for o in h["observations"]))

    def test_billing_limited_is_not_completed(self):
        output = {"status": "SUCCEEDED", "billing": {"limitReached": True}, "hotels": [{"hotelId": "a"}]}
        report = self.build([self.manifest_run([row()], output=output)])
        self.assertEqual(report["portfolio"]["collectionStatuses"], {"limited": 1})

    def test_report_only_does_not_fabricate_row_identity_or_denominator(self):
        summary = {"generatedAt": OBSERVED, "hotels": [{"hotelId": "a", "reviewCount": 20, "averageRating10": 8.3,
                    "recommendationRate": .9, "firstReviewDate": "2026-09-01", "latestReviewDate": "2026-09-03", "latestAspects": None}]}
        report = self.build([self.manifest_run(None, report=summary)])
        sample = report["hotels"][0]["observations"][0]["sample"]
        self.assertEqual(sample["reviewCount"], 20)
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 0)
        self.assertEqual(report["portfolio"]["summaryOnlyObservationCount"], 1)
        self.assertIsNone(sample["ratingCount"])
        self.assertIsNone(sample["recommendation"]["positive"])
        self.assertEqual(sample["reviewEvidence"], [])

    def test_dataset_wins_over_inconsistent_report(self):
        summary = {"hotels": [{"hotelId": "a", "reviewCount": 999, "averageRating10": 10}]}
        report = self.build([self.manifest_run([row(rating=4)], report=summary)])
        observation = report["hotels"][0]["observations"][0]
        self.assertEqual(observation["sample"]["averageRating10"], 4)
        self.assertTrue(any("differs" in w for w in observation["warnings"]))

    def test_escaping_and_privacy_in_all_exports(self):
        malicious = row()
        malicious.update(hotelName='<script>alert("name")</script>', text="PRIVATE_REVIEW_TEXT", reviewer={"name": "PRIVATE_NAME"},
                         reviewUrl="javascript:alert(1)", reviewId='<img src=x onerror="alert(1)">')
        report = self.build([self.manifest_run([malicious])], title="<img src=x onerror=alert(2)>")
        page = render_html(report)
        self.assertNotIn("<script>", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;img", page)
        for output in [page, json.dumps(report)]:
            self.assertNotIn("PRIVATE_REVIEW_TEXT", output)
            self.assertNotIn("PRIVATE_NAME", output)
        self.assertIsNone(safe_url("https://user:secret@example.com/"))
        self.assertIsNone(safe_url("data:text/html,unsafe"))

    def test_conflicting_source_aggregate_remains_unavailable(self):
        a, b = row(identity="a"), row(identity="b")
        b["hotel"]["rating6"] = 3.4
        report = self.build([self.manifest_run([a, b])])
        observation = report["hotels"][0]["observations"][0]
        self.assertIsNone(observation["sourceHotelSummary"]["rating6"])
        self.assertTrue(any("Conflicting" in warning for warning in observation["warnings"]))

    def test_missing_identity_is_not_claimed_unique(self):
        a = row()
        a["reviewId"] = None
        report = self.build([self.manifest_run([a])])
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 0)
        self.assertEqual(report["portfolio"]["unidentifiedReviewRows"], 1)

    def test_dataset_envelopes_and_single_record(self):
        for data in [[row()], {"items": [row()]}, {"data": {"items": [row()]}}, row()]:
            with self.subTest(data_type=type(data).__name__):
                self.assertEqual(self.build([self.manifest_run(data)])["portfolio"]["identifiedUniqueReviewCount"], 1)

    def test_invalid_manifest_and_rows_fail_with_actionable_error(self):
        for bad in [{}, {"runs": []}, {"runs": [{}]}]:
            with self.subTest(manifest=bad), self.assertRaises(ValueError):
                build_report(bad, self.root)
        a = self.manifest_run([row()])
        with self.assertRaisesRegex(ValueError, "once"):
            self.build([a, a])
        a["observedAt"] = "bad"
        with self.assertRaisesRegex(ValueError, "observedAt"):
            self.build([a])
        bad = self.manifest_run([None])
        with self.assertRaisesRegex(ValueError, "Dataset"):
            self.build([bad])
        bad = self.manifest_run([{"reviewId": "x"}])
        with self.assertRaisesRegex(ValueError, "hotelId"):
            self.build([bad])

    def test_historical_existing_redacted_schema_examples_remain_dated(self):
        base = Path(__file__).resolve().parents[1]
        for filename in ["01_verified_review_output.json", "02_low_rating_aspect_output.json", "03_elevated_burst_signal_output.json"]:
            with self.subTest(filename=filename):
                manifest = {"runs": [{"runId": "KHUAJkF5fVUqKtd75", "observedAt": "2026-07-28", "datasetFile": filename}]}
                report = build_report(manifest, base, OBSERVED)
                self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 1)
                self.assertEqual(report["hotels"][0]["latestObservedAt"], "2026-07-28")
                self.assertEqual(report["portfolio"]["collectionStatuses"], {"unknown": 1})

    def test_all_original_inputs_preserve_caps_modes_and_sort(self):
        base = Path(__file__).resolve().parents[1]
        inputs = ["01_public_store_example_input.json", "02_negative_review_triage_saved_task_input.json",
                  "03_since_monitoring_recipe_input.json", "04_german_management_report_input.json", "05_competitor_benchmark_input.json"]
        for filename in inputs:
            with self.subTest(filename=filename):
                original = json.loads((base / filename).read_text())
                run = self.manifest_run([row()], output={"status": "SUCCEEDED", "hotels": [{"hotelId": "a"}]})
                run["inputFile"] = self.file(filename, original)
                report = self.build([run])
                collection = report["hotels"][0]["observations"][0]["collection"]
                self.assertEqual(collection["mode"], original["collectionMode"])
                self.assertEqual(collection["maxReviewsPerHotel"], original.get("maxReviewsPerHotel"))
                self.assertEqual(collection["sort"], original["sort"])
                self.assertEqual(collection["cutoffDate"], original.get("cutoffDate"))
                self.assertFalse(collection["completeSourceHistory"])

    def test_nonfinite_and_boolean_numeric_fields_do_not_leak_to_json(self):
        a, b = row(identity="a", rating=float("nan")), row(identity="b", rating=True)
        a["aspectScores"]["room"]["score"] = float("inf")
        b["aspectScores"]["room"]["score"] = False
        report = self.build([self.manifest_run([a, b])])
        sample = report["hotels"][0]["observations"][0]["sample"]
        self.assertIsNone(sample["averageRating10"])
        self.assertIsNone(sample["aspects"][0]["score"])
        json.dumps(report, allow_nan=False)

    def test_observation_order_uses_timezone_and_does_not_pool_samples(self):
        earlier = self.manifest_run([row(rating=1)], "earlier")
        earlier["observedAt"] = "2026-09-30T18:00:00+02:00"
        later = self.manifest_run([row(rating=10)], "later")
        later["observedAt"] = "2026-09-30T17:00:00Z"
        report = self.build([earlier, later])
        observations = report["hotels"][0]["observations"]
        self.assertEqual(observations[0]["runId"], "later")
        self.assertEqual([o["sample"]["averageRating10"] for o in observations], [10, 1])
        self.assertEqual(report["portfolio"]["identifiedUniqueReviewCount"], 1)
        self.assertNotIn("averageRating", report["portfolio"])

    def test_html_structure_has_evidence_and_no_remote_assets(self):
        class Inspect(HTMLParser):
            def __init__(self):
                super().__init__()
                self.headings, self.links, self.tags, self.columns = [], [], [], []
                self.current_columns = None

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                attrs = dict(attrs)
                if tag in ("h1", "h2", "h3"):
                    self.headings.append(tag)
                if tag == "a":
                    self.links.append(attrs.get("href"))
                if tag == "tr":
                    self.current_columns = 0
                if tag in ("th", "td"):
                    self.current_columns += 1

            def handle_endtag(self, tag):
                if tag == "tr":
                    self.columns.append(self.current_columns)
                    self.current_columns = None

        page = render_html(self.build([self.manifest_run([row()])]))
        parser = Inspect()
        parser.feed(page)
        self.assertEqual(parser.headings.count("h1"), 1)
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("img", parser.tags)
        self.assertNotIn("iframe", parser.tags)
        self.assertNotIn("link", parser.tags)
        self.assertTrue(all(safe_url(url) for url in parser.links))
        self.assertTrue(all(columns in (3, 4) for columns in parser.columns))
        self.assertIn("scope=\"row\"", page)
        self.assertIn("@media(max-width:700px)", page)
        self.assertIn("@media print", page)

    def test_cli_builds_both_artifacts(self):
        manifest = {"runs": [self.manifest_run([row()])]}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        script = Path(__file__).resolve().parents[1] / "build_client_report.py"
        process = subprocess.run([sys.executable, str(script), str(path), "--output-dir", str(self.root / "result")], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue((self.root / "result/report.html").is_file())
        self.assertEqual(json.loads((self.root / "result/report.json").read_text())["portfolio"]["identifiedUniqueReviewCount"], 1)

    def test_truncated_export_retains_rows_but_is_not_completed(self):
        output = {"status": "SUCCEEDED", "collection": {"mode": "limit", "maxReviewsPerHotel": 20},
                  "hotels": [{"hotelId": "a", "reviewsOutput": 20}]}
        report = self.build([self.manifest_run([row()], output=output)])
        observation = report["hotels"][0]["observations"][0]
        self.assertEqual(observation["sample"]["reviewCount"], 1)
        self.assertEqual(observation["collection"]["status"], "incomplete_export")
        self.assertEqual(observation["collection"]["datasetCompleteness"], "incomplete")
        self.assertIn("incomplete export", render_html(report))

    def test_extra_rows_are_explicitly_inconsistent_not_completed(self):
        output = {"status": "SUCCEEDED", "hotels": [{"hotelId": "a", "reviewsOutput": 1}]}
        report = self.build([self.manifest_run([row(identity="1"), row(identity="2")], output=output)])
        state = report["hotels"][0]["observations"][0]["collection"]
        self.assertEqual(state["status"], "inconsistent_export")
        self.assertEqual(state["datasetCompleteness"], "count_mismatch")

    def test_raw_duplicate_download_count_is_not_a_partial_export(self):
        output = {"status": "SUCCEEDED", "collection": {"mode": "limit"}, "hotels": [{"hotelId": "a", "reviewsOutput": 2}]}
        report = self.build([self.manifest_run([row(), row()], output=output)])
        observation = report["hotels"][0]["observations"][0]
        self.assertEqual(observation["sample"]["reviewCount"], 1)
        self.assertEqual(observation["collection"]["localDatasetRows"], 2)
        self.assertEqual(observation["collection"]["datasetCompleteness"], "matched")
        self.assertEqual(observation["collection"]["status"], "completed_capped")

    def test_failed_platform_status_overrides_stale_success_output(self):
        for platform in ["FAILED", "TIMED-OUT", "ABORTED"]:
            with self.subTest(platform=platform):
                run = self.manifest_run([row()], output={"status": "SUCCEEDED", "hotels": [{"hotelId": "a", "reviewsOutput": 1}]})
                run["platformStatus"] = platform
                report = self.build([run])
                observation = report["hotels"][0]["observations"][0]
                self.assertEqual(observation["collection"]["status"], "partial")
                self.assertEqual(observation["collection"]["platformStatus"], platform)
                self.assertEqual(observation["sample"]["reviewCount"], 1)
                self.assertTrue(any("statuses conflict" in w for w in observation["warnings"]))
                self.assertEqual(report["runs"][0]["platformStatus"], platform)
                self.assertEqual(report["runs"][0]["outputStatus"], "SUCCEEDED")
                page = render_html(report)
                self.assertIn(platform + "<br><small>OUTPUT SUCCEEDED", page)
                self.assertNotIn("<td>SUCCEEDED</td>", page)

    def test_manifest_date_does_not_relabel_old_export_as_current(self):
        run = self.manifest_run([row()], output={"status": "SUCCEEDED", "finishedAt": "2026-07-01T12:00:00Z",
                                                "hotels": [{"hotelId": "a", "reviewsOutput": 1}]})
        report = self.build([run])
        self.assertEqual(report["hotels"][0]["latestObservedAt"], "2026-07-01T12:00:00Z")
        self.assertTrue(any("timestamp takes precedence" in w for w in report["warnings"]))

    def test_run_chronology_and_explicit_foreign_report_run_are_rejected(self):
        run = self.manifest_run([row()], output={"startedAt": "2026-10-01T00:00:00Z", "finishedAt": OBSERVED})
        with self.assertRaisesRegex(ValueError, "chronology"):
            self.build([run])
        run = self.manifest_run([row()], report={"runId": "foreignRun"})
        with self.assertRaisesRegex(ValueError, "runId conflicts"):
            self.build([run])

    def test_conflicting_nested_hotel_identity_is_rejected(self):
        bad = row()
        bad["hotel"]["id"] = "other-hotel"
        with self.assertRaisesRegex(ValueError, "hotelId conflicts"):
            self.build([self.manifest_run([bad])])

    def test_repeated_hotel_summary_is_not_silently_overwritten(self):
        output = {"status": "SUCCEEDED", "hotels": [{"hotelId": "a", "reviewsOutput": 1}, {"hotelId": "a", "reviewsOutput": 99}]}
        with self.assertRaisesRegex(ValueError, "Duplicate hotel"):
            self.build([self.manifest_run([row()], output=output)])

    def test_overflowing_timestamp_is_unavailable_not_an_uncaught_error(self):
        bad = row()
        bad["entryDate"] = "9999-12-31T23:59:59-14:00"
        report = self.build([self.manifest_run([bad])])
        self.assertIsNone(report["hotels"][0]["observations"][0]["sample"]["period"]["from"])

    def test_review_date_after_collection_has_an_explicit_warning(self):
        bad = row()
        bad["entryDate"] = "2026-10-02T00:00:00Z"
        report = self.build([self.manifest_run([bad])])
        self.assertTrue(any("after the collection timestamp" in w for w in report["hotels"][0]["observations"][0]["warnings"]))

    def test_invalid_generation_date_cannot_be_report_metadata(self):
        with self.assertRaisesRegex(ValueError, "generatedAt"):
            build_report({"runs": [self.manifest_run([row()])]}, self.root, "invalid")

    def test_missing_platform_metadata_is_not_inferred_from_output_success(self):
        run = self.manifest_run([row()], output={"status": "SUCCEEDED", "hotels": [{"hotelId": "a", "reviewsOutput": 1}]})
        report = self.build([run])
        self.assertEqual(report["runs"][0]["platformStatus"], "unknown")
        self.assertEqual(report["runs"][0]["outputStatus"], "SUCCEEDED")
        self.assertIn("unknown<br><small>OUTPUT SUCCEEDED", render_html(report))


if __name__ == "__main__":
    unittest.main()
