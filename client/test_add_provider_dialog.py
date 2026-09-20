import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from django.test import SimpleTestCase
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from .windows.add_provider_dialog import AddProviderDialog

app = QApplication.instance() or QApplication([])

TYPES = [
    {"value": "local_folder", "label": "Local folder"},
    {"value": "aws_s3", "label": "Amazon S3"},
    {"value": "s3_compatible", "label": "S3-compatible storage"},
    {"value": "mystery", "label": "Not supported by this app"},
]


class AddProviderDialogTests(SimpleTestCase):
    def setUp(self):
        self.dialog = AddProviderDialog(TYPES)
        self.addCleanup(self.dialog.deleteLater)

    def choose(self, provider_type):
        self.dialog.type_combo.setCurrentIndex(self.dialog.type_combo.findData(provider_type))

    def fill(self, **values):
        for key, value in values.items():
            self.dialog._inputs[key].setText(value)

    def test_only_types_this_app_can_configure_are_listed(self):
        listed = [self.dialog.type_combo.itemData(i) for i in range(self.dialog.type_combo.count())]
        self.assertEqual(listed, ["local_folder", "aws_s3", "s3_compatible"])

    def test_switching_type_rebuilds_the_fields_and_the_hint(self):
        self.assertEqual(list(self.dialog._inputs), ["path"])

        self.choose("aws_s3")
        self.assertEqual(
            list(self.dialog._inputs), ["bucket", "region", "access_key_id", "secret_access_key"]
        )
        self.assertIn("Amazon S3", self.dialog.hint_label.text())

        self.choose("s3_compatible")
        self.assertEqual(
            list(self.dialog._inputs),
            ["endpoint_url", "bucket", "region", "access_key_id", "secret_access_key"],
        )
        self.assertIn("Backblaze B2", self.dialog.hint_label.text())

    def test_secret_keys_are_hidden_while_typing(self):
        self.choose("s3_compatible")
        self.assertEqual(
            self.dialog._inputs["secret_access_key"].echoMode(), QLineEdit.EchoMode.Password
        )
        self.assertEqual(self.dialog._inputs["access_key_id"].echoMode(), QLineEdit.EchoMode.Normal)

    def test_an_optional_field_may_be_left_empty_but_required_ones_may_not(self):
        self.choose("s3_compatible")
        self.dialog.name_input.setText("My B2")
        self.fill(
            endpoint_url="https://s3.example.com",
            bucket="my-bucket",
            access_key_id="key-id",
            secret_access_key="secret",
        )  # region left empty
        self.dialog._accept_if_valid()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_a_missing_required_field_or_name_is_refused_with_a_reason(self):
        self.choose("aws_s3")
        self.fill(bucket="b", region="eu-west-1", access_key_id="key-id")  # no secret

        self.dialog.name_input.setText("")
        self.dialog._accept_if_valid()
        self.assertIn("display name", self.dialog.error_label.text())

        self.dialog.name_input.setText("AWS")
        self.dialog._accept_if_valid()
        self.assertIn("Fill in every field", self.dialog.error_label.text())
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_values_returns_the_type_name_and_credentials_as_entered(self):
        self.choose("aws_s3")
        self.dialog.name_input.setText("  My AWS  ")
        self.fill(bucket=" b ", region="eu-west-1", access_key_id="k", secret_access_key="s")

        self.assertEqual(
            self.dialog.values(),
            (
                "aws_s3",
                "My AWS",
                {"bucket": "b", "region": "eu-west-1", "access_key_id": "k", "secret_access_key": "s"},
            ),
        )
