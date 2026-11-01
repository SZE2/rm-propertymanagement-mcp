"""Offline checks for discovery of unit-level custom classifications."""
import unittest
from unittest.mock import patch

import server


class CustomFieldDiscovery(unittest.TestCase):
    def test_renovation_search_surfaces_unit_custom_fields(self):
        with patch.object(server, "idx", return_value={"reports": [], "resources": {}}), \
             patch.object(server.SI, "search", return_value=[]), \
             patch.object(server, "recipe_search", return_value=[]):
            text = server.t_search_models("unit renovation classification").lower()
        self.assertIn("custom field", text)
        self.assertIn("property", text)
        self.assertIn("unrecognized", text)
        self.assertIn("grouped rows are not necessarily distinct units", text)
        self.assertIn("duplicates or lists", text)

    def test_unit_model_notes_surface_custom_fields(self):
        text = "\n".join(server.model_notes(["Unit", "Units"])).lower()
        self.assertIn("custom field", text)
        self.assertIn("units/{id}/userdefinedvalues", text)


if __name__ == "__main__":
    unittest.main()
