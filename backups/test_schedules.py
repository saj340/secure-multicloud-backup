from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from monitoring.models import ActivityLog

from .models import BackupSchedule

# 2026-09-01 is a Tuesday, so 2026-08-31 and 2026-09-07 are Mondays.


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


class ScheduleTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="sched@example.com", password="irrelevant-pass-1")

    def make(self, created=utc(2026, 9, 1, 10, 0), **overrides):
        fields = {
            "user": self.user,
            "name": "Docs",
            "source_path": "/docs",
            "frequency": "daily",
            "run_at": time(2, 0),
            "weekday": None,
            "utc_offset_minutes": 0,
            **overrides,
        }
        schedule = BackupSchedule.objects.create(**fields)
        BackupSchedule.objects.filter(pk=schedule.pk).update(created_at=created)
        schedule.refresh_from_db()
        return schedule


class DailyDueTests(ScheduleTestCase):
    def test_a_new_schedule_waits_for_its_first_scheduled_time(self):
        schedule = self.make()  # created Tue 10:00, runs daily at 02:00
        self.assertFalse(schedule.is_due(utc(2026, 9, 1, 10, 30)))
        self.assertFalse(schedule.is_due(utc(2026, 9, 2, 1, 59)))
        self.assertTrue(schedule.is_due(utc(2026, 9, 2, 2, 0, 30)))

    def test_running_it_makes_it_wait_for_the_next_day(self):
        schedule = self.make(last_run_at=utc(2026, 9, 2, 2, 1))
        self.assertFalse(schedule.is_due(utc(2026, 9, 2, 12, 0)))
        self.assertFalse(schedule.is_due(utc(2026, 9, 3, 1, 59)))
        self.assertTrue(schedule.is_due(utc(2026, 9, 3, 2, 0)))

    def test_several_missed_times_still_mean_one_run(self):
        schedule = self.make(last_run_at=utc(2026, 9, 2, 2, 1))
        now = utc(2026, 9, 6, 12, 0)  # the app was closed for four days
        self.assertTrue(schedule.is_due(now))
        self.assertEqual(schedule.latest_occurrence(now), utc(2026, 9, 6, 2, 0))

    def test_a_paused_schedule_is_never_due(self):
        schedule = self.make(is_active=False)
        self.assertFalse(schedule.is_due(utc(2026, 9, 9, 12, 0)))

    def test_the_next_occurrence_is_always_in_the_future_and_a_day_on(self):
        schedule = self.make()
        for now in (utc(2026, 9, 2, 1, 0), utc(2026, 9, 2, 2, 0), utc(2026, 9, 2, 23, 59)):
            with self.subTest(now=now):
                latest, upcoming = schedule.latest_occurrence(now), schedule.next_occurrence(now)
                self.assertLessEqual(latest, now)
                self.assertGreater(upcoming, now)
                self.assertEqual(upcoming - latest, timedelta(days=1))


class TimezoneTests(ScheduleTestCase):
    def test_the_time_means_the_users_local_time(self):
        # 02:00 in UTC+3 is 23:00 UTC the evening before.
        schedule = self.make(utc_offset_minutes=180)
        now = utc(2026, 9, 2, 0, 30)
        self.assertEqual(schedule.latest_occurrence(now), utc(2026, 9, 1, 23, 0))

    def test_a_negative_offset_moves_the_other_way(self):
        # 23:30 in UTC-5, when it is still 22:00 on Sep 1 there: the last one was the day before.
        schedule = self.make(run_at=time(23, 30), utc_offset_minutes=-300)
        now = utc(2026, 9, 2, 3, 0)
        self.assertEqual(schedule.latest_occurrence(now), utc(2026, 9, 1, 4, 30))


class WeeklyDueTests(ScheduleTestCase):
    def make_weekly(self, **overrides):
        return self.make(frequency="weekly", weekday=0, **overrides)  # Mondays at 02:00

    def test_the_latest_occurrence_is_the_most_recent_monday(self):
        schedule = self.make_weekly()
        self.assertEqual(schedule.latest_occurrence(utc(2026, 9, 2, 12, 0)), utc(2026, 8, 31, 2, 0))
        self.assertEqual(schedule.latest_occurrence(utc(2026, 9, 7, 1, 0)), utc(2026, 8, 31, 2, 0))
        self.assertEqual(schedule.latest_occurrence(utc(2026, 9, 7, 2, 0)), utc(2026, 9, 7, 2, 0))

    def test_it_is_due_once_the_next_monday_arrives(self):
        schedule = self.make_weekly(last_run_at=utc(2026, 8, 31, 2, 5))
        self.assertFalse(schedule.is_due(utc(2026, 9, 6, 23, 0)))
        self.assertTrue(schedule.is_due(utc(2026, 9, 7, 2, 0)))

    def test_the_next_occurrence_is_a_week_on(self):
        schedule = self.make_weekly()
        now = utc(2026, 9, 2, 12, 0)
        self.assertEqual(schedule.next_occurrence(now), utc(2026, 9, 7, 2, 0))


class ScheduleApiTests(ScheduleTestCase):
    LIST = "/api/backups/schedules/"

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def payload(self, **overrides):
        return {
            "name": "Documents",
            "source_path": "C:/docs",
            "frequency": "daily",
            "run_at": "02:00",
            "weekday": None,
            "utc_offset_minutes": 120,
            **overrides,
        }

    def create(self, **overrides):
        return self.client.post(self.LIST, self.payload(**overrides), format="json")

    def detail(self, schedule_id):
        return f"{self.LIST}{schedule_id}/"

    def test_endpoints_require_a_login(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(self.LIST).status_code, 401)
        self.assertEqual(anonymous.post(self.LIST, {}, format="json").status_code, 401)

    def test_creating_a_daily_schedule(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["run_at"], "02:00")
        self.assertIsNone(response.data["weekday"])
        self.assertFalse(response.data["is_due"])
        self.assertIsNotNone(response.data["next_run_at"])
        self.assertEqual(BackupSchedule.objects.get(pk=response.data["id"]).user, self.user)

    def test_a_weekly_schedule_needs_a_valid_weekday(self):
        self.assertEqual(self.create(frequency="weekly").status_code, 400)
        self.assertEqual(self.create(frequency="weekly", weekday=7).status_code, 400)
        response = self.create(frequency="weekly", weekday=2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["weekday"], 2)

    def test_a_daily_schedule_ignores_any_weekday(self):
        self.assertIsNone(self.create(weekday=3).data["weekday"])

    def test_invalid_values_are_refused(self):
        cases = {
            "impossible time": {"run_at": "25:00"},
            "blank name": {"name": ""},
            "blank source": {"source_path": ""},
            "impossible offset": {"utc_offset_minutes": 900},
            "unknown frequency": {"frequency": "hourly"},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                self.assertEqual(self.create(**overrides).status_code, 400)
        self.assertEqual(BackupSchedule.objects.count(), 0)

    def test_the_list_only_shows_the_users_own_schedules(self):
        self.create(name="Mine")
        other = User.objects.create_user(email="other@example.com", password="irrelevant-pass-1")
        BackupSchedule.objects.create(user=other, name="Theirs", source_path="/x", frequency="daily")
        self.assertEqual([s["name"] for s in self.client.get(self.LIST).data], ["Mine"])

    def test_other_users_schedules_are_not_found(self):
        other = User.objects.create_user(email="other@example.com", password="irrelevant-pass-1")
        theirs = BackupSchedule.objects.create(user=other, name="Theirs", source_path="/x", frequency="daily")
        url = self.detail(theirs.pk)

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"name": "x"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.post(url + "record-run/", {"status": "completed"}, format="json").status_code, 404)
        self.assertTrue(BackupSchedule.objects.filter(pk=theirs.pk).exists())

    def test_pausing_and_resuming(self):
        schedule_id = self.create().data["id"]
        paused = self.client.patch(self.detail(schedule_id), {"is_active": False}, format="json")
        self.assertFalse(paused.data["is_active"])
        self.assertIsNone(paused.data["next_run_at"])

        resumed = self.client.patch(self.detail(schedule_id), {"is_active": True}, format="json")
        self.assertIsNotNone(resumed.data["next_run_at"])

    def test_changing_frequency_to_weekly_needs_a_weekday(self):
        schedule_id = self.create().data["id"]
        url = self.detail(schedule_id)
        self.assertEqual(self.client.patch(url, {"frequency": "weekly"}, format="json").status_code, 400)
        ok = self.client.patch(url, {"frequency": "weekly", "weekday": 0}, format="json")
        self.assertEqual((ok.status_code, ok.data["weekday"]), (200, 0))

    def test_deleting(self):
        schedule_id = self.create().data["id"]
        self.assertEqual(self.client.delete(self.detail(schedule_id)).status_code, 204)
        self.assertEqual(self.client.get(self.LIST).data, [])

    def test_recording_a_run_moves_the_schedule_on_and_logs_it(self):
        schedule_id = self.create().data["id"]
        BackupSchedule.objects.filter(pk=schedule_id).update(created_at=timezone.now() - timedelta(days=3))
        self.assertTrue(self.client.get(self.detail(schedule_id)).data["is_due"])

        response = self.client.post(
            self.detail(schedule_id) + "record-run/", {"status": "completed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_due"])
        self.assertEqual(response.data["last_result"], "completed")
        self.assertIsNotNone(response.data["last_run_at"])
        self.assertTrue(ActivityLog.objects.filter(action="scheduled_backup_completed").exists())

    def test_a_failed_run_is_recorded_with_its_reason(self):
        schedule_id = self.create().data["id"]
        BackupSchedule.objects.filter(pk=schedule_id).update(created_at=timezone.now() - timedelta(days=3))

        response = self.client.post(
            self.detail(schedule_id) + "record-run/",
            {"status": "failed", "message": "Folder not found"},
            format="json",
        )
        self.assertEqual(response.data["last_result"], "failed")
        self.assertFalse(response.data["is_due"])  # it waits for its next time rather than retrying
        entry = ActivityLog.objects.get(action="scheduled_backup_failed")
        self.assertIn("Folder not found", entry.description)
        self.assertEqual(entry.level, ActivityLog.Level.ERROR)

    def test_only_completed_or_failed_can_be_recorded(self):
        schedule_id = self.create().data["id"]
        response = self.client.post(
            self.detail(schedule_id) + "record-run/", {"status": "banana"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
