"""Offline publication regressions; no Actor or network execution."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_client_report as builder


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        cls.manifest_path = cls.repo / "client-report-sample/manifest.json"
        cls.report = builder.build_report(
            json.loads(cls.manifest_path.read_text(encoding="utf-8")),
            cls.manifest_path.parent, "2026-10-04T08:00:00Z")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.destination = self.base / "report-2026-10-04"

    def tearDown(self):
        self.temp.cleanup()

    def assert_no_staging(self, parent=None):
        self.assertEqual(list((parent or self.base).glob(".*-staging-*")), [])

    def test_healthy_two_hotel_bundle_preserves_content_dates_and_permissions(self):
        destination = self.base / "private" / "reports" / "two-hotels"
        builder.publish_report(self.report, destination)
        self.assertEqual({file.name for file in destination.iterdir()}, {"report.json", "report.html"})
        saved = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["portfolio"]["hotelCount"], 2)
        self.assertEqual(saved["portfolio"]["identifiedUniqueReviewCount"], 40)
        self.assertEqual(saved["generatedAt"], "2026-10-04T08:00:00Z")
        self.assertEqual(saved["observationsPeriod"], self.report["observationsPeriod"])
        self.assertEqual(saved["runs"], self.report["runs"])
        self.assertEqual((destination / "report.json").read_bytes(),
                         (json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        self.assertEqual((destination / "report.html").read_text(encoding="utf-8"),
                         builder.render_html(self.report) + "\n")
        if os.name == "posix":
            for folder in (destination, destination.parent, destination.parent.parent):
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            for file in destination.iterdir():
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assert_no_staging(destination.parent)

    def test_existing_report_artifacts_remain_byte_identical(self):
        self.destination.mkdir()
        (self.destination / "report.json").write_bytes(b'{"historical":"keep"}\n')
        (self.destination / "report.html").write_bytes(b"Historical report\n")
        before = {path.name: path.read_bytes() for path in self.destination.iterdir()}
        with self.assertRaisesRegex(builder.PublicationError, "already exists"):
            builder.publish_report(self.report, self.destination)
        self.assertEqual({path.name: path.read_bytes() for path in self.destination.iterdir()}, before)
        self.assert_no_staging()

    def test_existing_empty_directory_is_not_replaced(self):
        self.destination.mkdir()
        before = self.destination.stat().st_ino
        with self.assertRaisesRegex(builder.PublicationError, "already exists"):
            builder.publish_report(self.report, self.destination)
        self.assertEqual(self.destination.stat().st_ino, before)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_existing_file_is_not_replaced(self):
        self.destination.write_bytes(b"existing file")
        with self.assertRaises(builder.PublicationError):
            builder.publish_report(self.report, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"existing file")

    def test_existing_html_directory_does_not_leave_new_json(self):
        self.destination.mkdir()
        (self.destination / "report.html").mkdir()
        with self.assertRaises(builder.PublicationError):
            builder.publish_report(self.report, self.destination)
        self.assertFalse((self.destination / "report.json").exists())
        self.assertTrue((self.destination / "report.html").is_dir())

    def test_destination_symlink_is_refused_without_touching_target(self):
        target = self.base / "historical"
        target.mkdir()
        (target / "sentinel").write_bytes(b"keep")
        self.destination.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(builder.PublicationError, "symlinks"):
            builder.publish_report(self.report, self.destination)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual({p.name for p in target.iterdir()}, {"sentinel"})

    def test_dangling_destination_symlink_is_refused(self):
        target = self.base / "absent"
        self.destination.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(builder.PublicationError, "symlinks"):
            builder.publish_report(self.report, self.destination)
        self.assertTrue(self.destination.is_symlink())
        self.assertFalse(target.exists())

    def test_symlink_ancestor_is_refused(self):
        target = self.base / "real-parent"
        target.mkdir()
        link = self.base / "linked-parent"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(builder.PublicationError, "symlinks"):
            builder.publish_report(self.report, link / "child" / "report")
        self.assertEqual(list(target.iterdir()), [])

    def test_file_ancestor_is_refused(self):
        parent = self.base / "parent-file"
        parent.write_bytes(b"keep")
        with self.assertRaisesRegex(builder.PublicationError, "not a directory"):
            builder.publish_report(self.report, parent / "report")
        self.assertEqual(parent.read_bytes(), b"keep")

    def test_second_write_failure_removes_staging_and_exposes_no_bundle(self):
        original = builder.write_artifact
        calls = []

        def write(path, data):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError("synthetic second write failure")
            original(path, data)

        with mock.patch.object(builder, "write_artifact", side_effect=write):
            with self.assertRaisesRegex(builder.PublicationError, "no report bundle published"):
                builder.publish_report(self.report, self.destination)
        self.assertEqual(calls, ["report.json", "report.html"])
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_fsync_failure_removes_staging_and_exposes_no_bundle(self):
        with mock.patch.object(builder.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(builder.PublicationError):
                builder.publish_report(self.report, self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_commit_failure_removes_staging_and_exposes_no_bundle(self):
        with mock.patch.object(builder, "rename_exclusive", side_effect=OSError("synthetic commit failure")):
            with self.assertRaises(builder.PublicationError):
                builder.publish_report(self.report, self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_concurrently_created_empty_target_survives_native_commit(self):
        native = builder.rename_exclusive

        def race(source, destination):
            destination.mkdir()
            native(source, destination)

        with mock.patch.object(builder, "rename_exclusive", side_effect=race):
            with self.assertRaisesRegex(builder.PublicationError, "already exists"):
                builder.publish_report(self.report, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_no_staging()

    def test_render_failure_creates_no_output_parent(self):
        destination = self.base / "new-parent" / "report"
        with mock.patch.object(builder, "render_html", side_effect=ValueError("synthetic render failure")):
            with self.assertRaises(ValueError):
                builder.publish_report(self.report, destination)
        self.assertFalse(destination.parent.exists())

    def test_unsupported_platform_fails_closed_and_removes_staging(self):
        with mock.patch.object(builder.sys, "platform", "unsupported-test-platform"):
            with self.assertRaisesRegex(builder.PublicationError, "unavailable"):
                builder.publish_report(self.report, self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_unicode_output_path_publishes_complete_bundle(self):
        destination = self.base / "Müşteri raporu ä" / "Ekim 4"
        builder.publish_report(self.report, destination)
        self.assertEqual(json.loads((destination / "report.json").read_text(encoding="utf-8")), self.report)
        self.assertTrue((destination / "report.html").is_file())

    def test_parent_traversal_is_refused_without_creating_paths(self):
        destination = self.base / "absent" / ".." / "report"
        with self.assertRaisesRegex(builder.PublicationError, "parent-directory traversal"):
            builder.publish_report(self.report, destination)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_final_directory_is_absent_until_both_artifacts_are_ready(self):
        original = builder.write_artifact
        observed = []

        def write(path, data):
            self.assertFalse(self.destination.exists())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            original(path, data)
            observed.append(path.name)

        with mock.patch.object(builder, "write_artifact", side_effect=write):
            builder.publish_report(self.report, self.destination)
        self.assertEqual(observed, ["report.json", "report.html"])
        self.assertEqual({p.name for p in self.destination.iterdir()}, set(observed))

    def test_cli_reports_refusal_and_preserves_previous_bundle(self):
        args = [str(self.manifest_path), "--output-dir", str(self.destination)]
        with contextlib.redirect_stdout(io.StringIO()):
            builder.main(args)
        before = {p.name: p.read_bytes() for p in self.destination.iterdir()}
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as stopped:
                builder.main(args)
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("already exists", errors.getvalue())
        self.assertEqual({p.name: p.read_bytes() for p in self.destination.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
