#!/usr/bin/env python3
"""Offline unit test for v2 item 4f: recipes resolve reports by name, wrong id hint is harmless."""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server


class ReportByName(unittest.TestCase):
    def test_wrong_id_right_name(self):
        rid = server.recipe_report({"report": "AgedReceivables", "report_id": 99999})
        self.assertEqual(rid, server.report_by("AgedReceivables")["id"])

    def test_missing_name_errors(self):
        with self.assertRaises(server.ToolError):
            server.recipe_report({"report": "NoSuchReportXyz"})


if __name__ == "__main__":
    unittest.main()
