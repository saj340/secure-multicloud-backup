import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from django.test import SimpleTestCase

from .backup_service import load_backups, restore_version, run_backup
from .test_gui import GuiTestCase, wait_until
from .windows.dashboard_page import (
    CURRENT,
    DONE,
    LOCKED,
    SETUP_STEPS,
    DashboardPage,
    setup_complete,
    setup_states,
)
from .windows.main_window import MainWindow


def counts(connected=0, backup_done=False, restore_done=False):
    return {"connected": connected, "backup_done": backup_done, "restore_done": restore_done}


class SetupRulesTests(SimpleTestCase):
    def test_a_brand_new_account_starts_at_the_first_step(self):
        self.assertEqual(setup_states(counts()), [CURRENT, LOCKED, LOCKED])

    def test_one_provider_is_not_enough_for_the_first_step(self):
        self.assertEqual(setup_states(counts(connected=1)), [CURRENT, LOCKED, LOCKED])

    def test_two_providers_finish_step_one_and_unlock_the_backup(self):
        self.assertEqual(setup_states(counts(connected=2)), [DONE, CURRENT, LOCKED])

    def test_a_finished_backup_unlocks_the_restore(self):
        self.assertEqual(setup_states(counts(2, backup_done=True)), [DONE, DONE, CURRENT])

    def test_setup_is_complete_only_after_both_a_backup_and_a_restore(self):
        self.assertFalse(setup_complete(counts(2)))
        self.assertFalse(setup_complete(counts(2, backup_done=True)))
        self.assertFalse(setup_complete(counts(2, restore_done=True)))
        self.assertTrue(setup_complete(counts(2, backup_done=True, restore_done=True)))

    def test_a_provider_outage_after_setup_does_not_bring_the_checklist_back(self):
        after_outage = counts(connected=1, backup_done=True, restore_done=True)
        self.assertTrue(setup_complete(after_outage))

    def test_an_outage_during_setup_sends_you_back_to_the_provider_step(self):
        self.assertEqual(setup_states(counts(connected=1, backup_done=True)), [CURRENT, DONE, LOCKED])

    def test_every_step_names_a_real_screen(self):
        screens = [screen for _title, screen, _button in SETUP_STEPS]
        self.assertEqual(screens, ["Cloud Providers", "New Backup", "Recover Files"])


class ChecklistTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.visited = []
        self.page = DashboardPage(self.api, on_navigate=self.visited.append)
        self.addCleanup(self.page.deleteLater)

    def reload(self):
        self.page.refresh()
        self.assertTrue(wait_until(lambda: self.page.providers_value.text() != "–"))
        self.assertTrue(wait_until(lambda: not self.page_busy()))

    def page_busy(self):
        from . import workers

        return bool(workers._active)

    def states(self):
        return [row.state for row in self.page.step_rows]

    def make_backup(self):
        document = self.tmp / "doc.txt"
        document.write_text("hello " * 50)
        return run_backup(self.api, "docs", [document])

    def test_a_new_account_sees_the_checklist_and_only_the_first_button_works(self):
        self.reload()

        self.assertFalse(self.page.setup_frame.isHidden())
        self.assertEqual(self.states(), [CURRENT, LOCKED, LOCKED])
        self.assertEqual([row.button.isEnabled() for row in self.page.step_rows], [True, False, False])
        self.assertEqual(self.page.status_label.text(), "")

    def test_the_button_takes_you_to_the_screen_that_does_the_step(self):
        self.reload()
        self.page.step_rows[0].button.click()
        self.assertEqual(self.visited, ["Cloud Providers"])

    def test_the_checklist_moves_forward_as_each_step_is_really_done(self):
        self.connect_via_api("alpha")
        self.reload()
        self.assertEqual(self.states(), [CURRENT, LOCKED, LOCKED])  # one provider isn't enough

        self.connect_via_api("beta")
        self.reload()
        self.assertEqual(self.states(), [DONE, CURRENT, LOCKED])
        self.assertTrue(self.page.step_rows[1].button.isEnabled())
        self.assertTrue(self.page.step_rows[0].button.isHidden())  # nothing left to do there

        self.assertEqual(self.make_backup().status, "completed")
        self.reload()
        self.assertEqual(self.states(), [DONE, DONE, CURRENT])

        version = load_backups(self.api)[0]["files"][0]["versions"][0]
        restore_version(self.api, version["id"], self.tmp / "back.txt")
        self.reload()
        self.assertTrue(self.page.setup_frame.isHidden())
        self.assertIn("Ready", self.page.status_label.text())

    def test_a_backup_that_was_created_but_never_ran_does_not_count(self):
        self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.api.create_job("never ran", [str(self.tmp / "nothing.txt")])

        self.reload()

        self.assertEqual(self.page.jobs_value.text(), "1")  # the card counts it...
        self.assertEqual(self.states(), [DONE, CURRENT, LOCKED])  # ...the checklist doesn't

    def test_a_server_error_leaves_the_checklist_alone_and_says_what_went_wrong(self):
        self.page._failed("Could not reach the server.")
        self.assertEqual(self.page.status_label.text(), "Could not reach the server.")


class MainWindowChecklistTests(GuiTestCase):
    def test_the_buttons_really_switch_screens(self):
        window = MainWindow(self.api)
        self.addCleanup(window.deleteLater)
        window.set_user(self.api.me())
        self.assertTrue(wait_until(lambda: window.dashboard.providers_value.text() != "–"))

        window.dashboard.step_rows[0].button.click()

        self.assertEqual(window.sidebar.currentItem().text(), "Cloud Providers")
        self.assertIs(window.pages.currentWidget(), window.providers)

    def test_show_screen_works_for_every_step_the_checklist_offers(self):
        window = MainWindow(self.api)
        self.addCleanup(window.deleteLater)
        window.set_user(self.api.me())
        for _title, screen, _button in SETUP_STEPS:
            window.show_screen(screen)
            self.assertEqual(window.sidebar.currentItem().text(), screen)
