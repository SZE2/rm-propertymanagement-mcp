#!/usr/bin/env python3
"""Synthetic, offline date/null filter regressions. No downloaded spec or credentials needed."""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rm
import server


MODELS = {
    "Sample": {
        "fields": {"SampleID": "int", "EndDate": "DateTime?", "CreatedAt": "DateTime",
                   "TextDate": "String", "Label": "String", "OptionalID": "int?",
                   "Child": "ChildModel", "Children": "List<ChildModel>"},
        "filters": ["SampleID", "EndDate", "CreatedAt", "TextDate", "Label", "OptionalID",
                    "Child.ClosedAt", "Children.ClosedAt", "Child.Grandchild.ClosedAt",
                    "FilterOnlyDate"],
        "embeds": ["Child", "Children", "Child.Grandchild"],
    },
    "Child": {
        "fields": {"ClosedAt": "DateTime?", "Grandchild": "ChildModel"},
        "filters": [], "embeds": ["Grandchild"],
    },
}
INDEX = {"resources": {"Samples": {"model": "Sample", "list": True, "by_id": True}},
         "by_model": {"Sample": ["Samples"]}, "reports": []}


def model_info(name):
    return MODELS.get(name.removesuffix("Model")) if name else None


class DateNullFilters(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(server.SI, "model_info", side_effect=model_info))
        self.enterContext(mock.patch.object(server.SI, "load_index", return_value=INDEX))
        self.enterContext(mock.patch.object(server, "all_scope_label", return_value="All samples"))
        # A bug in the test must never fall through to a real API request.
        self.enterContext(mock.patch.object(rm, "get", side_effect=AssertionError("unexpected network call")))
        out = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {"RM_OUT": out}))

    def test_date_null_is_rejected_before_query_or_count_api_calls(self):
        for tool in (server.t_query, server.t_count):
            with self.subTest(tool=tool.__name__), mock.patch.object(server, "api", return_value=(204, {}, None)) as api:
                with self.assertRaises(server.ToolError) as err:
                    tool("Samples", filters="EndDate,eq,null")
                api.assert_not_called()
                text = str(err.exception)
                for hint in ("EndDate", "DateTime", "null", "YYYY-MM-DD", "where='EndDate,eq,'", "CAPPED"):
                    self.assertIn(hint, text)

    def test_all_existing_operators_reject_null_on_date_fields(self):
        for op in sorted(server.OPS):
            val = "(2026-01-01,null)" if op in ("in", "ni", "bt") else "null"
            with self.subTest(op=op):
                *_, problems = server.validate("Sample", filters="EndDate,%s,%s" % (op, val))
                self.assertTrue(problems)
                self.assertIn("null", " ".join(problems))

    def test_case_whitespace_and_quoted_nulls(self):
        for val in ("null", "NULL", " Null ", "'null'", '"null"'):
            for field in ("enddate", "CreatedAt", "Child.ClosedAt", "Children.ClosedAt", "Child.Grandchild.ClosedAt"):
                with self.subTest(value=val, field=field):
                    *_, problems = server.validate("Sample", filters="%s, NE ,%s" % (field, val))
                    self.assertTrue(problems)

    def test_null_in_lists_and_ranges(self):
        for op in ("in", "ni", "bt"):
            for val in ("(null,2026-01-01)", '(2026-01-01;"NULL")', "('null',2026-01-01)"):
                with self.subTest(op=op, value=val):
                    *_, problems = server.validate("Sample", filters="EndDate,%s,%s" % (op, val))
                    self.assertTrue(problems)

    def test_bare_date_lists_with_null_never_reach_api(self):
        for op in ("in", "ni", "bt"):
            for val in ("2026-01-01,null", "null,2026-01-01", '2026-01-01,"NULL"'):
                for tool in (server.t_query, server.t_count):
                    with self.subTest(op=op, value=val, tool=tool.__name__), mock.patch.object(server, "api") as api:
                        with self.assertRaises(server.ToolError) as err:
                            tool("Samples", filters="EndDate,%s,%s" % (op, val))
                        api.assert_not_called()
                        self.assertIn("literal null", str(err.exception))

    def test_non_date_null_filters_are_not_blanket_rejected(self):
        for field in ("OptionalID", "Label", "TextDate", "FilterOnlyDate"):
            for val in ("null", '"null"', "(null,1)"):
                op = "in" if val.startswith("(") else "eq"
                flt = "%s,%s,%s" % (field, op, val)
                with self.subTest(filter=flt):
                    result = server.validate("Sample", filters=flt)
                    self.assertEqual(result[2], flt)
                    self.assertEqual(result[4], [])

    def test_real_dates_and_normalization_are_preserved(self):
        cases = {
            "enddate,GE,2026-01-01": "EndDate,ge,2026-01-01",
            "CreatedAt,lt,2026-02-01T12:30:00": "CreatedAt,lt,2026-02-01T12:30:00",
            "EndDate,bt,2026-01-01;2026-02-01": "EndDate,bt,(2026-01-01,2026-02-01)",
            "EndDate,in,('2026-01-01';'2026-02-01')": "EndDate,in,('2026-01-01','2026-02-01')",
            "Child.ClosedAt,ge,2026-01-01": "Child.ClosedAt,ge,2026-01-01",
            "EndDate,ge,today;EndDate,le,today+30": "EndDate,ge,2026-01-01;EndDate,le,2026-01-31",
        }
        with mock.patch.object(server, "_today", return_value=datetime.date(2026, 1, 1)):
            for flt, expected in cases.items():
                with self.subTest(filter=flt):
                    result = server.validate("Sample", filters=flt)
                    self.assertEqual(result[2], expected)
                    self.assertEqual(result[4], [])

    def test_missing_model_or_nested_metadata_does_not_guess_from_names(self):
        for model in (None, "Unknown"):
            result = server.validate(model, filters="EndDate,eq,null")
            self.assertEqual(result[2], "EndDate,eq,null")
            self.assertEqual(result[4], [])
        with mock.patch.object(server.SI, "model_info", return_value={
                "fields": {"Child": "MissingModel"}, "filters": ["Child.EndDate"], "embeds": ["Child"]}):
            result = server.validate("Sample", filters="Child.EndDate,eq,null")
            self.assertEqual(result[4], [])

    def test_valid_count_still_sends_filter_unchanged(self):
        with mock.patch.object(server, "api", return_value=(204, {}, None)) as api:
            server.t_count("Samples", filters="EndDate,ge,2026-01-01;OptionalID,eq,null")
        self.assertEqual(api.call_args.args[1]["filters"], "EndDate,ge,2026-01-01;OptionalID,eq,null")

    def test_discovery_explains_date_null_limit(self):
        tools = {t["name"]: t for t in server.tool_list()}
        for name in ("query", "count"):
            description = tools[name]["inputSchema"]["properties"]["filters"]["description"]
            self.assertIn("DateTime", description)
            self.assertIn("null", description)
            self.assertIn("where", description)

    def test_bad_name_and_operator_checks_are_preserved(self):
        for flt, expected in (("Missing,eq,null", "not filterable"), ("EndDate,isnull,null", "invalid")):
            with self.subTest(filter=flt):
                *_, problems = server.validate("Sample", filters=flt)
                self.assertIn(expected, " ".join(problems))

    def test_advised_local_blank_filter_matches_missing_dates(self):
        rows = [{"EndDate": None}, {"EndDate": ""}, {"EndDate": "2026-01-01"}]
        missing, _, _ = server.post_process(rows, where="EndDate,eq,")
        present, _, _ = server.post_process(rows, where="EndDate,ne,")
        self.assertEqual(missing, rows[:2])
        self.assertEqual(present, rows[2:])


class FieldTypes(unittest.TestCase):
    def test_types_are_loaded_from_synthetic_spec_files(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            models = Path(root, "models")
            models.mkdir()
            for name, mi in MODELS.items():
                Path(models, name + "Model.json").write_text(json.dumps({
                    "fields": mi["fields"], "filters": mi["filters"],
                    "embeds": [{"Name": e} for e in mi["embeds"]],
                }), encoding="utf-8")
            with mock.patch.object(server.SI, "SPEC", root):
                server.SI.model_info.cache_clear()
                try:
                    for field, typ in (("EndDate", "DateTime?"), ("CreatedAt", "DateTime"),
                                       ("Children.ClosedAt", "DateTime?"), ("Child.Grandchild.ClosedAt", "DateTime?"),
                                       ("TextDate", "String"), ("FilterOnlyDate", None),
                                       ("Child.Missing", None), ("EndDate.Missing", None)):
                        with self.subTest(field=field):
                            self.assertEqual(server.SI.field_type("SampleModel", field), typ)
                    *_, problems = server.validate("Sample", filters="Children.ClosedAt,eq,null")
                    self.assertTrue(problems)
                finally:
                    server.SI.model_info.cache_clear()

    def test_direct_dotted_metadata_and_missing_type(self):
        with mock.patch.object(server.SI, "model_info", return_value={
                "fields": {"Child.EndDate": "DateTime?", "Unknown": None}}):
            self.assertEqual(server.SI.field_type("Sample", "Child.EndDate"), "DateTime?")
            self.assertIsNone(server.SI.field_type("Sample", "Unknown.Nested"))
        self.assertIsNone(server.SI.field_type(None, "EndDate"))


if __name__ == "__main__":
    unittest.main()
