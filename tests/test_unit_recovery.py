"""Offline error-recovery tests using synthetic, non-portfolio metadata."""
import unittest
from unittest.mock import patch
import server

INDEX = {"resources": {
    "Widgets": {"model": "Widget", "list": True, "subs": {
        "Labels": {"model": "Label", "all": True, "by_id": True},
        "Notes": {"model": "Note", "all": False, "by_id": True}}},
    "Gadgets": {"model": "Gadget", "list": True, "subs": {
        "Labels": {"model": "Label", "all": False, "by_id": True}}},
}, "by_model": {"Widget": ["Widgets"], "Gadget": ["Gadgets"]}}
MODEL = {"fields": {"Name": "string"}, "embeds": ["Widget"], "filters": []}

class RecoveryTests(unittest.TestCase):
    def test_exact_subresource_matches_are_qualified_and_rejected(self):
        with patch.object(server, "idx", return_value=INDEX), patch.object(server.SI, "search", return_value=[]):
            with self.assertRaises(server.ToolError) as err:
                server.find_resource("labels")
            msg = str(err.exception)
            self.assertIn("Widgets/Labels", msg)
            self.assertIn("Gadgets/{id}/Labels", msg)
            self.assertNotIn("Gadgets/Labels", msg)
            self.assertEqual(server.find_resource("Widgets/Labels")["sub"], "Labels")
            self.assertEqual(server.find_resource("Gadgets/7/Labels")["sub"], "Labels")

    def test_unknown_or_partial_name_has_no_invented_qualified_match(self):
        with patch.object(server, "idx", return_value=INDEX), patch.object(server.SI, "search", return_value=[]):
            with self.assertRaises(server.ToolError) as err:
                server.find_resource("Label")
            self.assertNotIn("Widgets/Labels", str(err.exception))

    def test_invalid_root_field_gives_discovery_not_guessed_path(self):
        with patch.object(server.SI, "model_info", return_value=MODEL), patch.object(server, "concepts", return_value={}):
            _, _, _, _, problems = server.validate("WidgetLink", fields="WidgetID")
            self.assertEqual(len(problems), 1)
            self.assertIn("section=embeds", problems[0])
            self.assertNotIn("Widget.WidgetID", problems[0])
            _, _, _, _, problems = server.validate("WidgetLink", fields="Widget.WidgetID")
            self.assertEqual(problems, [])

    def test_invalid_fields_still_prevent_fetch(self):
        resource = {"ctrl": "Widgets", "model": "WidgetLink", "id": None,
                    "sub": None, "path": "/Widgets", "list": True}
        with patch.object(server, "find_resource", return_value=resource), \
             patch.object(server.SI, "model_info", return_value=MODEL), \
             patch.object(server, "fetch_ordered") as fetch:
            with self.assertRaises(server.ToolError):
                server.t_query("Widgets", fields="WidgetID")
            fetch.assert_not_called()

    def test_subresource_suggestions_are_bounded_and_labeled(self):
        index = {"by_model": {}, "resources": {
            "Parent%02d" % n: {"subs": {"Entries": {"all": True}}} for n in range(15)}}
        with patch.object(server, "idx", return_value=index), patch.object(server.SI, "search", return_value=[]):
            with self.assertRaises(server.ToolError) as err:
                server.find_resource("Entries")
            msg = str(err.exception)
            self.assertIn("more matches omitted", msg)
            self.assertEqual(msg.count("/Entries"), 10)

if __name__ == "__main__":
    unittest.main()
