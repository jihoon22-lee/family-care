from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_repository_safety import MAX_FILE_BYTES, inspect_path


class RepositorySafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def write(self, relative_path: str, content: bytes = b"synthetic") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_rejects_pdf_outside_synthetic_fixtures(self) -> None:
        path = self.write("sample.pdf", b"%PDF-1.4\nsynthetic fixture\n")

        errors = inspect_path(self.root, path)

        self.assertTrue(any("PDF" in error for error in errors))

    def test_allows_pdf_inside_synthetic_fixtures(self) -> None:
        path = self.write(
            "fixtures/synthetic/sample.pdf",
            b"%PDF-1.4\nsynthetic fixture\n",
        )

        self.assertEqual(inspect_path(self.root, path), [])

    def test_rejects_database_dump(self) -> None:
        path = self.write("backup.dump")

        errors = inspect_path(self.root, path)

        self.assertTrue(any("suffix" in error for error in errors))

    def test_rejects_private_key(self) -> None:
        path = self.write("local.pem")

        errors = inspect_path(self.root, path)

        self.assertTrue(any("suffix" in error for error in errors))

    def test_rejects_forbidden_data_directory(self) -> None:
        path = self.write("actual-data/sample.txt")

        errors = inspect_path(self.root, path)

        self.assertTrue(any("directory" in error for error in errors))

    def test_allows_python_modules_in_the_document_api_package_only(self) -> None:
        path = self.write(
            "apps/api/src/familycare_api/documents/generated_contracts.py",
            b'"""Synthetic generated contract."""\n',
        )

        self.assertEqual(inspect_path(self.root, path), [])

    def test_document_api_package_exception_does_not_allow_data_files(self) -> None:
        path = self.write(
            "apps/api/src/familycare_api/documents/sample.json",
            b'{"synthetic": true}\n',
        )

        errors = inspect_path(self.root, path)
        self.assertTrue(any("directory" in error for error in errors))

    def test_document_api_package_exception_does_not_allow_other_source_roots(self) -> None:
        path = self.write("apps/api/documents/model.py")

        errors = inspect_path(self.root, path)
        self.assertTrue(any("directory" in error for error in errors))

    def test_rejects_file_larger_than_two_mib(self) -> None:
        path = self.write("oversized.bin", b"0" * (MAX_FILE_BYTES + 1))

        errors = inspect_path(self.root, path)

        self.assertTrue(any("size" in error for error in errors))

    def test_allows_generated_web_icon(self) -> None:
        path = self.write("apps/web/public/icon.png")

        self.assertEqual(inspect_path(self.root, path), [])


if __name__ == "__main__":
    unittest.main()
