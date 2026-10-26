from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from monitoring.models import ActivityLog

from .manager import create_backup_job
from .models import BackupJob, BackupSet

LIST = "/api/backups/sets/"


class SetApiTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="sets@example.com", password="irrelevant-pass-1")
        self.other = User.objects.create_user(email="other@example.com", password="irrelevant-pass-1")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def make(self, name="Documents", sources=("C:/Users/me/Documents",), client=None):
        return (client or self.api).post(LIST, {"name": name, "sources": list(sources)}, format="json")


class AccessTests(SetApiTestCase):
    def test_every_endpoint_needs_a_login(self):
        anonymous = APIClient()
        made = self.make().data["id"]
        for response in (
            anonymous.get(LIST),
            anonymous.post(LIST, {}, format="json"),
            anonymous.get(f"{LIST}{made}/"),
            anonymous.patch(f"{LIST}{made}/", {}, format="json"),
            anonymous.delete(f"{LIST}{made}/"),
            anonymous.post(f"{LIST}{made}/record-run/", {"status": "completed"}, format="json"),
        ):
            self.assertEqual(response.status_code, 401)

    def test_a_set_belongs_to_its_owner_and_nobody_else_can_see_or_change_it(self):
        theirs = APIClient()
        theirs.force_authenticate(self.other)
        made = self.make(client=theirs).data["id"]

        self.assertEqual(self.api.get(LIST).data, [])
        for response in (
            self.api.get(f"{LIST}{made}/"),
            self.api.patch(f"{LIST}{made}/", {"name": "Mine now"}, format="json"),
            self.api.delete(f"{LIST}{made}/"),
            self.api.post(f"{LIST}{made}/record-run/", {"status": "completed"}, format="json"),
        ):
            self.assertEqual(response.status_code, 404)
        stored = BackupSet.objects.get(pk=made)
        self.assertEqual((stored.name, stored.user), ("Documents", self.other))


class CreateAndListTests(SetApiTestCase):
    def test_creating_a_set_returns_it_with_no_run_history(self):
        response = self.make("Photos", ["C:/Pics", "C:/Users/me/camera.jpg"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Photos")
        self.assertEqual(response.data["sources"], ["C:/Pics", "C:/Users/me/camera.jpg"])
        self.assertIsNone(response.data["last_run_at"])
        self.assertEqual(response.data["last_result"], "")

    def test_names_are_trimmed_and_blank_or_repeated_sources_are_tidied_up(self):
        response = self.make("  Work  ", ["C:/Work", "  ", "C:/Work", " C:/Notes ", ""])
        self.assertEqual(response.data["name"], "Work")
        self.assertEqual(response.data["sources"], ["C:/Work", "C:/Notes"])

    def test_the_list_holds_only_my_sets_in_name_order(self):
        self.make("Zebra")
        self.make("Alpha")
        theirs = APIClient()
        theirs.force_authenticate(self.other)
        self.make("Middle", client=theirs)
        self.assertEqual([s["name"] for s in self.api.get(LIST).data], ["Alpha", "Zebra"])

    def test_bad_input_is_refused_and_nothing_is_saved(self):
        bad = [
            {"name": "", "sources": ["C:/x"]},
            {"name": "   ", "sources": ["C:/x"]},
            {"name": "No sources", "sources": []},
            {"name": "Only blanks", "sources": ["", "  "]},
            {"name": "Too many", "sources": [f"C:/f{i}" for i in range(51)]},
            {"name": "Too long", "sources": ["C:/" + "a" * 1001]},
            {"name": "Not a list", "sources": "C:/x"},
            {"sources": ["C:/x"]},
        ]
        for payload in bad:
            self.assertEqual(self.api.post(LIST, payload, format="json").status_code, 400, payload)
        self.assertEqual(BackupSet.objects.count(), 0)

    def test_two_sets_of_mine_cannot_share_a_name_even_in_different_case(self):
        self.make("Documents")
        self.assertEqual(self.make("documents").status_code, 400)
        self.assertEqual(BackupSet.objects.filter(user=self.user).count(), 1)

    def test_a_different_user_may_use_the_same_name(self):
        self.make("Documents")
        theirs = APIClient()
        theirs.force_authenticate(self.other)
        self.assertEqual(self.make("Documents", client=theirs).status_code, 201)


class ChangeAndDeleteTests(SetApiTestCase):
    def test_a_set_can_be_renamed_and_its_sources_changed(self):
        made = self.make("Old name", ["C:/a"]).data["id"]
        response = self.api.patch(
            f"{LIST}{made}/", {"name": "New name", "sources": ["C:/b", "C:/c"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["name"], response.data["sources"]), ("New name", ["C:/b", "C:/c"]))

    def test_saving_a_set_under_its_own_name_is_not_a_clash(self):
        made = self.make("Documents").data["id"]
        response = self.api.patch(f"{LIST}{made}/", {"name": "Documents"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_renaming_onto_another_of_my_sets_is_refused(self):
        self.make("First")
        second = self.make("Second").data["id"]
        self.assertEqual(self.api.patch(f"{LIST}{second}/", {"name": "first"}, format="json").status_code, 400)

    def test_the_run_history_cannot_be_edited_from_outside(self):
        made = self.make().data["id"]
        self.api.patch(f"{LIST}{made}/", {"last_result": "completed", "last_run_at": "2020-01-01T00:00:00Z"}, format="json")
        stored = BackupSet.objects.get(pk=made)
        self.assertEqual(stored.last_result, "")
        self.assertIsNone(stored.last_run_at)

    def test_deleting_a_set_keeps_the_backups_that_were_made_from_it(self):
        made = self.make().data["id"]
        job = create_backup_job(self.user, "Documents 2026-10-26 10:00", ["C:/docs/a.txt"])

        self.assertEqual(self.api.delete(f"{LIST}{made}/").status_code, 204)

        self.assertFalse(BackupSet.objects.filter(pk=made).exists())
        self.assertTrue(BackupJob.objects.filter(pk=job.pk).exists())


class RecordRunTests(SetApiTestCase):
    def test_a_finished_run_is_remembered_and_logged(self):
        made = self.make("Documents").data["id"]

        response = self.api.post(f"{LIST}{made}/record-run/", {"status": "completed"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["last_result"], "completed")
        self.assertIsNotNone(response.data["last_run_at"])
        entry = ActivityLog.objects.get(action="backup_set_completed")
        self.assertEqual((entry.user, entry.level), (self.user, ActivityLog.Level.INFO))
        self.assertIn("Documents", entry.description)

    def test_a_failed_run_is_logged_as_an_error_with_its_reason(self):
        made = self.make("Documents").data["id"]
        self.api.post(
            f"{LIST}{made}/record-run/",
            {"status": "failed", "message": "Folder not found"},
            format="json",
        )
        entry = ActivityLog.objects.get(action="backup_set_failed")
        self.assertEqual(entry.level, ActivityLog.Level.ERROR)
        self.assertIn("Folder not found", entry.description)

    def test_an_unknown_outcome_is_refused(self):
        made = self.make().data["id"]
        response = self.api.post(f"{LIST}{made}/record-run/", {"status": "whatever"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BackupSet.objects.get(pk=made).last_result, "")
