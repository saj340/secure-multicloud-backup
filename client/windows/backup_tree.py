from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from client.formatting import describe_status, format_size, format_timestamp

from .progress_panel import files_text

COLUMNS = ["Name", "Status", "Size", "Date"]
DATA = Qt.ItemDataRole.UserRole


def _versions_text(count):
    return f"{count} version" if count == 1 else f"{count} versions"


class BackupTree(QTreeWidget):
    """Backup jobs, each holding its files, each holding that file's versions."""

    def __init__(self):
        super().__init__()
        self.setColumnCount(len(COLUMNS))
        self.setHeaderLabels(COLUMNS)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def load(self, jobs):
        """Fill the tree from job details as returned by load_backups."""
        self.clear()
        for job in jobs:
            job_item = self._item(
                [job["name"], "", files_text(len(job["files"])), format_timestamp(job["created_at"])],
                {"kind": "job", "job_id": job["id"]},
                status=job["status"],
            )
            for file in job["files"]:
                file_item = self._item(
                    [Path(file["original_path"]).name, "", _versions_text(len(file["versions"])), ""],
                    {"kind": "file", "job_id": job["id"], "file": file},
                )
                file_item.setToolTip(0, file["original_path"])
                for version in reversed(file["versions"]):  # newest first
                    file_item.addChild(
                        self._item(
                            [
                                f"Version {version['version_number']}",
                                "",
                                format_size(version["original_size"]),
                                format_timestamp(version["backup_time"]),
                            ],
                            {"kind": "version", "job_id": job["id"], "file": file, "version": version},
                            status=version["upload_status"],
                        )
                    )
                job_item.addChild(file_item)
            self.addTopLevelItem(job_item)
        self.expandToDepth(0)

    def selected_data(self):
        items = self.selectedItems()
        return items[0].data(0, DATA) if items else None

    def selected_job_id(self):
        data = self.selected_data()
        return data["job_id"] if data else None

    def selected_recoverable_version(self):
        """The version that Restore would recover, or None.

        A version is recoverable if it was stored completely; selecting a
        file means its newest such version.
        """
        data = self.selected_data()
        if data is None:
            return None
        if data["kind"] == "version":
            version = data["version"]
            return version if version["upload_status"] == "completed" else None
        if data["kind"] == "file":
            completed = [v for v in data["file"]["versions"] if v["upload_status"] == "completed"]
            return max(completed, key=lambda v: v["version_number"]) if completed else None
        return None

    @staticmethod
    def _item(cells, data, status=None):
        item = QTreeWidgetItem(cells)
        item.setData(0, DATA, data)
        if status is not None:
            text, color = describe_status(status)
            item.setText(1, text)
            item.setForeground(1, QBrush(QColor(color)))
        return item
