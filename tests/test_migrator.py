import unittest
from pathlib import Path

from scripts.migrator import Migrator, safe_error


class MigratorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "fixtures"
        self.migrator = Migrator({"url": "postgresql://user:secret@db.example.test/postgres", "null_marker": r"\N", "connect_timeout": 5}, self.root, self.root / "schema.sql", self.root / "reports")

    def test_streams_quoted_commas_and_newlines(self):
        path = self.root / "quoted.csv"
        plan = self.migrator.analyze_csv(path, [
            {"name": "id", "nullable": False, "default": None, "identity": False, "generated": False, "primary_key": True},
            {"name": "name", "nullable": False, "default": None, "identity": False, "generated": False, "primary_key": False},
        ])
        self.assertEqual(plan["source_row_count"], 2)
        self.assertEqual(list(self.migrator.source_rows(plan)), [["1", "hello, world"], ["2", "two\nlines"]])

    def test_rejects_duplicate_composite_primary_keys(self):
        path = self.root / "duplicate.csv"
        columns = [{"name": name, "nullable": False, "default": None, "identity": False, "generated": False, "primary_key": name in {"a", "b"}} for name in ("a", "b", "value")]
        with self.assertRaisesRegex(RuntimeError, "duplicate primary-key"):
            self.migrator.analyze_csv(path, columns)

    def test_rejects_unexpected_columns_and_wrong_field_count(self):
        columns = [{"name": "id", "nullable": False, "default": None, "identity": False, "generated": False, "primary_key": True}]
        path = self.root / "unexpected.csv"
        with self.assertRaisesRegex(RuntimeError, "unexpected CSV columns"):
            self.migrator.analyze_csv(path, columns)
        with self.assertRaisesRegex(RuntimeError, "header has 1"):
            self.migrator.analyze_csv(self.root / "wrong_fields.csv", columns)

    def test_dependency_order_includes_self_reference_cycle_detection(self):
        mapping = {("public", "parent"): None, ("public", "child"): None}
        fks = {("public", "child"): [{"ref_schema": "public", "ref_table": "parent", "deferrable": False}]}
        self.assertEqual(self.migrator.dependency_order(mapping, fks), [("public", "parent"), ("public", "child")])
        cycle = {
            ("public", "parent"): [{"ref_schema": "public", "ref_table": "child", "deferrable": False}],
            ("public", "child"): [{"ref_schema": "public", "ref_table": "parent", "deferrable": False}],
        }
        with self.assertRaisesRegex(RuntimeError, "dependency cycle"):
            self.migrator.dependency_order(mapping, cycle)

    def test_connection_error_redacts_userinfo(self):
        message = safe_error("failed to connect postgresql://user:secret@db.example.test:5432/postgres", self.migrator.config["url"])
        self.assertNotIn("secret", message)
        malformed = safe_error("failed to resolve host 'Darth777.A1M2o3S4]@aws-1-eu-west-1.pooler.supabase.com': lookup failed", self.migrator.config["url"])
        self.assertNotIn("Darth777", malformed)
        self.assertIn("URL-encode", malformed)


if __name__ == "__main__":
    unittest.main()
