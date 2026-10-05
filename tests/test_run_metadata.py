"""Synthetic, offline saved-run setup checks. No Actor or source requests."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_client_report import ACTOR_ID, build_report, render_html


RUN_ID = "SyntheticRun00001"
BUILD_ID = "SyntheticBuild001"
STARTED = "2026-10-01T10:00:00Z"
OBSERVED = "2026-10-01T10:00:10Z"
FINISHED = "2026-10-01T10:01:00Z"


class SavedRunMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.metadata = {"id": RUN_ID, "actId": ACTOR_ID, "status": "SUCCEEDED",
                         "startedAt": STARTED, "finishedAt": FINISHED,
                         "buildId": BUILD_ID, "buildNumber": "0.4.18"}
        self.output = {"runId": RUN_ID, "status": "SUCCEEDED", "startedAt": STARTED,
                       "finishedAt": OBSERVED, "collection": {"mode": "limit", "maxReviewsPerHotel": 1},
                       "hotels": [{"hotelId": "synthetic-hotel", "hotelName": "Synthetic hotel", "reviewsOutput": 1}]}
        self.run = {"runId": RUN_ID, "buildNumber": "0.4.18", "datasetFile": "rows.json",
                    "outputFile": "OUTPUT.json", "runFile": "run.json"}
        self.rows = [{"hotelId": "synthetic-hotel", "hotelName": "Synthetic hotel", "reviewId": "synthetic-review",
                      "entryDate": "2026-10-01T09:00:00Z", "overallRating": 8}]

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def build(self, metadata=None, output=None, run=None, report=None):
        self.save("rows.json", self.rows)
        self.save("run.json", self.metadata if metadata is None else metadata)
        self.save("OUTPUT.json", self.output if output is None else output)
        entry = copy.deepcopy(self.run if run is None else run)
        if report is not None:
            self.save("report.json", report)
            entry["reportFile"] = "report.json"
        return build_report({"runs": [entry]}, self.root, "2026-10-05T10:00:00Z")

    def test_succeeded_metadata_sets_status_build_and_hashed_provenance(self):
        report = self.build()
        record = report["runs"][0]
        self.assertEqual(record["platformStatus"], "SUCCEEDED")
        self.assertEqual(record["buildNumber"], "0.4.18")
        self.assertEqual(record["observedAt"], OBSERVED)
        self.assertEqual(record["platformRun"], self.metadata)
        self.assertEqual(record["files"]["runFile"]["sha256"], hashlib.sha256((self.root / "run.json").read_bytes()).hexdigest())

    def test_data_envelope_supported(self):
        report = self.build({"data": self.metadata})
        self.assertEqual(report["runs"][0]["platformStatus"], "SUCCEEDED")

    def test_failures_preserve_partial_rows_and_platform_status(self):
        for status in ("FAILED", "TIMED-OUT", "ABORTED"):
            with self.subTest(status=status):
                report = self.build(dict(self.metadata, status=status))
                observation = report["hotels"][0]["observations"][0]
                self.assertEqual(report["runs"][0]["platformStatus"], status)
                self.assertEqual(observation["collection"]["status"], "partial")
                self.assertEqual(observation["collection"]["outputStatus"], "SUCCEEDED")
                self.assertIn(status, render_html(report))

    def test_conflicting_explicit_platform_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "platformStatus conflicts"):
            self.build(run=dict(self.run, platformStatus="FAILED"))

    def test_matching_explicit_platform_status_is_allowed(self):
        self.assertEqual(self.build(run=dict(self.run, platformStatus="SUCCEEDED"))["runs"][0]["platformStatus"], "SUCCEEDED")

    def test_wrong_run_identity_and_actor_are_rejected(self):
        for change in ({"id": "DifferentRun00001"}, {"id": None}, {"id": 123},
                       {"actId": "DifferentActor001"}, {"actId": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.build(dict(self.metadata, **change))

    def test_nonterminal_and_wrong_type_status_are_rejected(self):
        for status in ("RUNNING", "READY", "ABORTING", None, [], True):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "terminal"):
                self.build(dict(self.metadata, status=status))

    def test_build_identity_conflicts_and_unsafe_types_are_rejected(self):
        for key, value in (("buildId", "DifferentBuild001"), ("buildNumber", "0.4.17")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "conflicts"):
                self.build(run=dict(self.run, **{key: value}))
        for change in ({"buildId": []}, {"buildNumber": 18}, {"buildNumber": "secret-value"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid"):
                self.build(dict(self.metadata, **change))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.build(output=dict(self.output, buildNumber="0.4.17"))

    def test_build_fields_are_optional_but_unverifiable_assertions_rejected(self):
        sparse = {key: value for key, value in self.metadata.items() if key not in {"buildId", "buildNumber"}}
        entry = {key: value for key, value in self.run.items() if key != "buildNumber"}
        self.assertIsNone(self.build(sparse, run=entry)["runs"][0]["buildNumber"])
        with self.assertRaisesRegex(ValueError, "absent"):
            self.build(sparse)

    def test_timezone_bearing_ordered_cloud_timestamps_required(self):
        for change in ({"startedAt": None}, {"finishedAt": None}, {"finishedAt": []},
                       {"finishedAt": "2026-10-01"}, {"finishedAt": "2026-10-01T10:01:00"},
                       {"startedAt": "2026-10-01T10:02:00Z"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "timestamps"):
                self.build(dict(self.metadata, **change))

    def test_long_natural_platform_finish_lag_is_allowed(self):
        report = self.build(dict(self.metadata, finishedAt="2026-10-01T11:01:00Z"))
        self.assertEqual(report["runs"][0]["observedAt"], OBSERVED)

    def test_output_time_outside_cloud_interval_or_reversed_rejected(self):
        for change in ({"startedAt": "2026-10-01T09:59:54Z"}, {"finishedAt": "2026-10-01T10:01:06Z"},
                       {"startedAt": "2026-10-01T10:00:30Z"}, {"finishedAt": "not-a-timestamp"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.build(output=dict(self.output, **change))

    def test_small_clock_tolerance_and_timezone_offsets(self):
        metadata = dict(self.metadata, startedAt="2026-10-01T12:00:00+02:00", finishedAt="2026-10-01T12:01:00+02:00")
        report = self.build(metadata, output=dict(self.output, startedAt="2026-10-01T09:59:55Z", finishedAt="2026-10-01T10:01:05Z"))
        self.assertEqual(report["runs"][0]["observedAt"], "2026-10-01T10:01:05Z")

    def test_missing_output_finish_uses_run_finish_never_report_generation(self):
        output = {key: value for key, value in self.output.items() if key != "finishedAt"}
        report = self.build(output=output, report={"runId": RUN_ID, "generatedAt": "2026-10-05T09:00:00Z"})
        self.assertEqual(report["runs"][0]["observedAt"], FINISHED)

    def test_conflicting_manifest_date_warns_without_relabelling(self):
        report = self.build(run=dict(self.run, observedAt="2026-10-05T09:00:00Z"))
        self.assertEqual(report["runs"][0]["observedAt"], OBSERVED)
        self.assertTrue(any("manifest observedAt differs" in warning for warning in report["warnings"]))

    def test_ambiguous_and_nonobject_metadata_rejected(self):
        for metadata in ([], {"data": []}, {"id": RUN_ID, "data": self.metadata}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.build(metadata)

    def test_private_unrecognized_payload_never_enters_json_or_html(self):
        secret = "SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_COPY"
        metadata = dict(self.metadata, userId=secret, options={"token": secret}, secrets=[secret])
        report = self.build(metadata)
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn(secret, render_html(report))
        self.assertEqual(set(report["runs"][0]["platformRun"]), set(self.metadata))

    def test_no_runfile_keeps_existing_unknown_platform_behavior(self):
        entry = {key: value for key, value in self.run.items() if key != "runFile"}
        report = self.build(run=entry)
        self.assertEqual(report["runs"][0]["platformStatus"], "unknown")
        self.assertNotIn("platformRun", report["runs"][0])
        self.assertEqual(set(report["runs"][0]["files"]), {"datasetFile", "outputFile"})
        self.assertEqual(report["hotels"][0]["observations"][0]["collection"]["status"], "completed_capped")

    def test_earliest_datetime_interval_does_not_underflow(self):
        metadata = dict(self.metadata, startedAt="0001-01-01T00:00:00Z", finishedAt="0001-01-01T00:01:00Z")
        output = dict(self.output, startedAt=metadata["startedAt"], finishedAt="0001-01-01T00:00:10Z")
        self.assertEqual(self.build(metadata, output=output)["runs"][0]["observedAt"], output["finishedAt"])

    def test_latest_datetime_interval_does_not_overflow(self):
        metadata = dict(self.metadata, startedAt="9999-12-31T23:58:59Z", finishedAt="9999-12-31T23:59:59Z")
        output = dict(self.output, startedAt=metadata["startedAt"], finishedAt=metadata["finishedAt"])
        self.assertEqual(self.build(metadata, output=output)["runs"][0]["observedAt"], output["finishedAt"])


if __name__ == "__main__":
    unittest.main()
