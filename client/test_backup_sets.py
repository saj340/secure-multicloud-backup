import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re

from .api_client import ApiError
from .backup_service import load_backups
from .backup_sets import run_set
from .test_gui import GuiTestCase


class RunSetTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.docs = self.make_folder("docs")
        self.pics = self.make_folder("pics")
        (self.docs / "report.txt").write_text("report " * 100)
        (self.pics / "photo.jpg").write_bytes(os.urandom(5000))

    def make_set(self, name="Everything", sources=None):
        sources = sources or [str(self.docs), str(self.pics)]
        return self.api.create_set(name, sources)

    def stored_names(self):
        return sorted(
            version["original_filename"]
            for job in load_backups(self.api)
            for file in job["files"]
            for version in file["versions"]
        )

    def set_state(self, backup_set):
        return next(s for s in self.api.list_sets() if s["id"] == backup_set["id"])

    def test_running_a_set_backs_up_every_place_it_points_at_as_a_dated_backup(self):
        backup_set = self.make_set()

        result = run_set(self.api, backup_set)

        self.assertEqual(result.status, "completed")
        self.assertEqual(self.stored_names(), ["photo.jpg", "report.txt"])
        job_names = [job["name"] for job in self.api.list_jobs()]
        self.assertEqual(len(job_names), 1)
        self.assertRegex(job_names[0], r"^Everything \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_files_added_since_last_time_are_picked_up_by_the_next_run(self):
        backup_set = self.make_set()
        run_set(self.api, backup_set)

        (self.docs / "new-file.txt").write_text("added later " * 50)
        run_set(self.api, backup_set)

        self.assertIn("new-file.txt", self.stored_names())
        newest_job = max(self.api.list_jobs(), key=lambda job: job["id"])
        self.assertEqual(newest_job["file_count"], 3)  # the new file is part of it

    def test_a_finished_run_is_remembered_on_the_set(self):
        backup_set = self.make_set()
        self.assertIsNone(backup_set["last_run_at"])

        run_set(self.api, backup_set)

        state = self.set_state(backup_set)
        self.assertEqual(state["last_result"], "completed")
        self.assertIsNotNone(state["last_run_at"])

    def test_a_folder_that_has_gone_is_reported_and_recorded_without_making_a_backup(self):
        backup_set = self.make_set(sources=[str(self.docs), str(self.tmp / "vanished")])

        with self.assertRaises(FileNotFoundError):
            run_set(self.api, backup_set)

        self.assertEqual(self.set_state(backup_set)["last_result"], "failed")
        self.assertEqual(self.api.list_jobs(), [])

    def test_too_few_providers_gives_a_failed_result_and_is_recorded(self):
        # Failures are tracked per file, so this isn't an exception: the run comes back
        # marked failed, with each file that couldn't be stored.
        for provider in self.api.list_providers():
            self.api.disconnect_provider(provider["id"])
        backup_set = self.make_set()

        result = run_set(self.api, backup_set)

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.failed), 2)
        self.assertEqual(self.set_state(backup_set)["last_result"], "failed")
        failure = next(e for e in self.api.list_activity(50) if e["action"] == "backup_set_failed")
        self.assertIn("2 file(s) could not be backed up", failure["description"])

    def test_the_server_being_unreachable_is_reported_and_the_failure_is_recorded_if_it_can_be(self):
        backup_set = self.make_set()
        real = self.api.finish_job
        self.api.finish_job = lambda job_id: (_ for _ in ()).throw(ApiError("boom"))
        try:
            with self.assertRaises(ApiError):
                run_set(self.api, backup_set)
        finally:
            self.api.finish_job = real
        self.assertEqual(self.set_state(backup_set)["last_result"], "failed")

    def test_the_run_is_reported_to_the_activity_log(self):
        run_set(self.api, self.make_set("Everything"))
        actions = [entry["action"] for entry in self.api.list_activity(50)]
        self.assertIn("backup_set_completed", actions)

    def test_a_set_name_with_odd_characters_still_makes_a_valid_backup_name(self):
        backup_set = self.make_set(name="Mum & Dad's photos (2026)")
        run_set(self.api, backup_set)
        name = self.api.list_jobs()[0]["name"]
        self.assertTrue(re.match(r"^Mum & Dad's photos \(2026\) \d{4}-", name))
