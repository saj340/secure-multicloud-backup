import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from .api_client import ApiError
from .backup_service import (
    plan_restore,
    rerun_backup,
    restore_backup,
    restore_version,
    run_backup,
    safe_target,
)
from .test_gui import GuiTestCase

ROOT = Path(Path.cwd().anchor)  # an absolute folder on whatever system this runs on
BASE = ROOT / "Users" / "me"


def version(number, status="completed"):
    return {"id": number, "version_number": number, "upload_status": status}


def file(path, *versions):
    return {"id": 1, "original_path": str(path), "versions": list(versions) or [version(1)]}


def plan_for(destination, *files):
    return plan_restore({"files": list(files)}, destination)


class PlanRulesTests(SimpleTestCase):
    dest = ROOT / "restored"

    def targets(self, plan):
        return sorted(item.target for item in plan.items)

    def test_a_single_folder_comes_back_without_the_long_path_above_it(self):
        plan = plan_for(
            self.dest,
            file(BASE / "docs" / "a.txt"),
            file(BASE / "docs" / "sub" / "b.txt"),
        )
        self.assertEqual(self.targets(plan), [self.dest / "a.txt", self.dest / "sub" / "b.txt"])

    def test_several_folders_keep_their_own_names_under_the_folder_they_share(self):
        plan = plan_for(self.dest, file(BASE / "docs" / "a.txt"), file(BASE / "pics" / "c.jpg"))
        self.assertEqual(self.targets(plan), [self.dest / "docs" / "a.txt", self.dest / "pics" / "c.jpg"])

    def test_a_single_file_is_restored_under_its_own_name(self):
        plan = plan_for(self.dest, file(BASE / "docs" / "report.txt"))
        self.assertEqual(self.targets(plan), [self.dest / "report.txt"])

    def test_two_files_with_the_same_name_in_different_folders_do_not_collide(self):
        plan = plan_for(self.dest, file(BASE / "a" / "notes.txt"), file(BASE / "b" / "notes.txt"))
        self.assertEqual(len(set(self.targets(plan))), 2)

    def test_the_newest_completed_version_is_chosen(self):
        plan = plan_for(self.dest, file(BASE / "a.txt", version(1), version(2), version(3, "failed")))
        self.assertEqual(plan.items[0].version["version_number"], 2)

    def test_a_file_with_no_completed_version_is_reported_not_restored(self):
        never_stored = {"id": 2, "original_path": str(BASE / "empty.txt"), "versions": []}
        only_failed = file(BASE / "broken.txt", version(1, "failed"))

        plan = plan_for(self.dest, file(BASE / "good.txt"), never_stored, only_failed)

        self.assertEqual([item.source for item in plan.items], [str(BASE / "good.txt")])
        self.assertEqual(
            sorted(plan.unavailable), sorted([str(BASE / "empty.txt"), str(BASE / "broken.txt")])
        )

    @unittest.skipUnless(os.name == "nt", "drive letters only exist on Windows")
    def test_files_from_different_drives_get_the_drive_letter_as_a_folder(self):
        plan = plan_for(self.dest, file("C:\\a\\x.txt"), file("D:\\b\\y.txt"))
        self.assertEqual(self.targets(plan), [self.dest / "C" / "a" / "x.txt", self.dest / "D" / "b" / "y.txt"])

    def test_planning_writes_nothing(self):
        destination = ROOT / "smcb-should-never-be-created"
        plan_for(destination, file(BASE / "a.txt"))
        self.assertFalse(destination.exists())


class SafetyTests(SimpleTestCase):
    def setUp(self):
        self.dest = ROOT / "restored"

    def test_ordinary_relative_paths_are_accepted(self):
        self.assertEqual(safe_target(self.dest, Path("a.txt")), self.dest / "a.txt")
        self.assertEqual(safe_target(self.dest, Path("sub") / "b.txt"), self.dest / "sub" / "b.txt")

    def test_paths_that_climb_out_of_the_folder_are_refused(self):
        for bad in ("../evil.txt", "../../evil.txt", "sub/../../evil.txt", "sub/../.."):
            self.assertIsNone(safe_target(self.dest, Path(bad)), bad)

    def test_a_path_that_climbs_and_comes_back_is_still_refused(self):
        # It would end up inside, but a ".." from an untrusted source is never worth trusting.
        self.assertIsNone(safe_target(self.dest, Path("sub/../ok.txt")))

    def test_an_absolute_path_is_refused(self):
        self.assertIsNone(safe_target(self.dest, ROOT / "Windows" / "evil.txt"))

    def test_a_planned_file_that_would_escape_is_refused_and_named(self):
        evil = BASE / "docs" / "a.txt"
        with mock.patch("client.backup_service._relative_paths", return_value=[Path("..") / "evil.txt"]):
            plan = plan_for(ROOT / "restored", file(evil))
        self.assertEqual(plan.items, [])
        self.assertIn(str(evil), plan.unsafe)
        self.assertIn("outside the chosen folder", plan.unsafe[str(evil)])


class RestoreBackupTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.docs = self.make_folder("docs")
        (self.docs / "sub").mkdir()
        self.pics = self.make_folder("pics")
        (self.docs / "a.txt").write_text("alpha " * 200)
        (self.docs / "sub" / "b.txt").write_text("bravo " * 300)
        (self.pics / "c.bin").write_bytes(os.urandom(7000))
        result = run_backup(self.api, "All", [self.docs, self.pics])
        self.assertEqual(result.status, "completed")
        self.job_id = result.job_id
        self.dest = self.tmp / "restored"

    def job(self):
        return self.api.get_job(self.job_id)

    def restore(self, **kwargs):
        return restore_backup(self.api, self.job(), self.dest, **kwargs)

    def test_every_file_comes_back_byte_for_byte_with_its_folder_layout(self):
        summary = self.restore()

        self.assertEqual((len(summary.restored), summary.skipped, summary.failed), (3, [], {}))
        for original in (self.docs / "a.txt", self.docs / "sub" / "b.txt", self.pics / "c.bin"):
            restored = self.dest / original.relative_to(self.tmp)
            self.assertEqual(restored.read_bytes(), original.read_bytes(), restored)

    def test_files_that_already_exist_are_left_alone_unless_told_to_replace_them(self):
        existing = self.dest / "docs" / "a.txt"
        existing.parent.mkdir(parents=True)
        existing.write_text("my own version")

        summary = self.restore()
        self.assertEqual(len(summary.skipped), 1)
        self.assertEqual(len(summary.restored), 2)
        self.assertEqual(existing.read_text(), "my own version")

        summary = self.restore(overwrite=True)
        self.assertEqual((len(summary.restored), summary.skipped), (3, []))
        self.assertEqual(existing.read_text(), "alpha " * 200)

    def test_the_newest_version_of_a_file_is_the_one_restored(self):
        (self.docs / "a.txt").write_text("a newer version " * 50)
        self.assertEqual(rerun_backup(self.api, self.job_id).status, "completed")

        self.restore()

        self.assertEqual((self.dest / "docs" / "a.txt").read_text(), "a newer version " * 50)

    def test_one_file_failing_does_not_stop_the_others_and_leaves_no_partial_file(self):
        real = restore_version

        def fails_for_one(api, version_id, destination, **kwargs):
            if Path(destination).name == "a.txt":
                raise ApiError("the providers are down")
            return real(api, version_id, destination, **kwargs)

        with mock.patch("client.backup_service.restore_version", fails_for_one):
            summary = self.restore()

        self.assertEqual(len(summary.restored), 2)
        self.assertEqual([Path(path).name for path in summary.failed], ["a.txt"])
        self.assertIn("providers are down", next(iter(summary.failed.values())))
        self.assertFalse((self.dest / "docs" / "a.txt").exists())
        self.assertEqual(list(self.dest.rglob("*.part")), [])

    def test_when_every_provider_is_gone_every_file_is_reported_and_nothing_is_written(self):
        for provider in self.api.list_providers():
            self.api.disconnect_provider(provider["id"])

        summary = self.restore()

        self.assertEqual((summary.restored, summary.skipped), ([], []))
        self.assertEqual(len(summary.failed), 3)
        self.assertEqual([p for p in self.dest.rglob("*") if p.is_file()] if self.dest.exists() else [], [])

    def test_a_backup_that_never_stored_anything_has_nothing_to_restore(self):
        job = self.api.create_job("never ran", [str(self.docs / "a.txt")])
        summary = restore_backup(self.api, self.api.get_job(job["id"]), self.tmp / "nothing")
        self.assertEqual((summary.restored, summary.skipped), ([], []))
        self.assertIn("No complete version", next(iter(summary.failed.values())))

    def test_progress_counts_files_and_ends_complete(self):
        events = []
        self.restore(progress=lambda *args: events.append(args))

        stages = [event[0] for event in events]
        self.assertEqual(stages[0], "preparing")
        self.assertEqual(stages[-1], "completed")
        self.assertEqual([e[2:] for e in events if e[0] == "downloading"], [(0, 3), (1, 3), (2, 3)])
        self.assertEqual([e[2:] for e in events if e[0] == "processed"], [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(events[-1][2:], (3, 3))

    def test_each_restored_file_is_recorded_on_the_server(self):
        self.restore()
        recoveries = self.api.list_recoveries()
        self.assertEqual(len(recoveries), 3)
        self.assertEqual({r["status"] for r in recoveries}, {"completed"})
