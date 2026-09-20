import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil

from django.test import SimpleTestCase
from PySide6.QtWidgets import QApplication

from .test_gui import GuiTestCase, wait_until
from .windows.add_provider_dialog import AddProviderDialog
from .windows.providers_page import ProvidersPage

app = QApplication.instance() or QApplication([])

PROVIDER = {
    "id": 7,
    "provider_type": "aws_s3",
    "provider_type_label": "Amazon S3",
    "display_name": "My S3",
    "status": "disconnected",
}


class ReconnectDialogTests(SimpleTestCase):
    def setUp(self):
        self.dialog = AddProviderDialog([], reconnecting=PROVIDER)
        self.addCleanup(self.dialog.deleteLater)

    def test_type_and_name_are_fixed_and_the_credential_fields_match_the_type(self):
        self.assertEqual(self.dialog.windowTitle(), "Reconnect My S3")
        self.assertEqual(self.dialog.type_combo.currentData(), "aws_s3")
        self.assertFalse(self.dialog.type_combo.isEnabled())
        self.assertTrue(self.dialog.name_input.isReadOnly())
        self.assertEqual(self.dialog.name_input.text(), "My S3")
        self.assertEqual(
            list(self.dialog._inputs), ["bucket", "region", "access_key_id", "secret_access_key"]
        )

    def test_it_warns_to_use_the_same_bucket_or_folder(self):
        self.assertIn("same bucket or folder", self.dialog.hint_label.text())

    def test_empty_credentials_are_refused(self):
        self.dialog._accept_if_valid()
        self.assertIn("Fill in every field", self.dialog.error_label.text())

    def test_a_type_the_app_cannot_configure_leaves_no_form(self):
        odd = AddProviderDialog([], reconnecting={**PROVIDER, "provider_type": "mystery"})
        self.addCleanup(odd.deleteLater)
        self.assertEqual(odd.type_combo.count(), 0)


class ReconnectPageTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.page = ProvidersPage(self.api)
        self.addCleanup(self.page.deleteLater)
        self.folder_a = self.connect_via_api("alpha")
        self.connect_via_api("beta")
        self.provider = next(p for p in self.api.list_providers() if p["display_name"] == "alpha")
        self.page.refresh()
        self.settle()

    def settle(self):
        self.assertTrue(wait_until(lambda: not self.page._busy))

    def select(self, name):
        for row in range(self.page.table.rowCount()):
            if self.page.table.item(row, 0).text() == name:
                self.page.table.selectRow(row)
                return
        self.fail(f"{name} not listed")

    def status_of(self, name):
        for row in range(self.page.table.rowCount()):
            if self.page.table.item(row, 0).text() == name:
                return self.page.table.item(row, 2).text()

    def test_the_button_is_only_enabled_for_a_provider_that_is_not_connected(self):
        self.select("alpha")
        self.assertFalse(self.page.reconnect_button.isEnabled())

        self.page.disconnect_provider(self.provider["id"])
        self.settle()
        self.select("alpha")
        self.assertTrue(self.page.reconnect_button.isEnabled())
        self.assertFalse(self.page.disconnect_button.isEnabled())

    def test_reconnecting_restores_the_provider_and_reports_the_pieces_it_found(self):
        self.api.disconnect_provider(self.provider["id"])
        self.page.refresh()
        self.settle()
        self.assertEqual(self.status_of("alpha"), "Disconnected")

        self.page.reconnect_provider(self.provider["id"], {"path": str(self.folder_a)})
        self.settle()

        self.assertEqual(self.status_of("alpha"), "Connected")
        self.assertEqual(self.page.status_label.text(), "Reconnected.")  # nothing backed up yet
        self.assertEqual(len(self.api.list_providers()), 2)  # same record, not a new one

    def test_credentials_for_the_wrong_place_show_a_warning_that_survives_the_reload(self):
        from .backup_service import run_backup

        source = self.make_folder("docs")
        (source / "a.txt").write_text("hello " * 200)
        self.api.connect_provider("local_folder", "gamma", {"path": str(self.make_folder("gamma"))})
        run_backup(self.api, "Docs", [str(source)])
        self.api.disconnect_provider(self.provider["id"])
        self.page.refresh()
        self.settle()

        self.page.reconnect_provider(self.provider["id"], {"path": str(self.make_folder("elsewhere"))})
        self.settle()

        self.assertIn("were not found", self.page.status_label.text())
        self.assertIn("b00020", self.page.status_label.styleSheet())
        self.assertEqual(self.status_of("alpha"), "Connected")

    def test_a_folder_that_cannot_be_reached_shows_the_servers_message(self):
        self.api.disconnect_provider(self.provider["id"])
        self.page.refresh()
        self.settle()
        shutil.rmtree(self.folder_a)

        self.page.reconnect_provider(self.provider["id"], {"path": str(self.folder_a)})
        self.settle()

        self.assertIn("Could not reach", self.page.status_label.text())
        self.assertEqual(self.status_of("alpha"), "Disconnected")
