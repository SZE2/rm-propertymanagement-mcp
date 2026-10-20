#!/usr/bin/env python3
"""Offline unit tests (v10): property resolution by billing name, address, owner name; confidence threshold; old alias file rebuild.
Synthetic data only."""
import os, sys, json, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import server


def P(i, name, short="", billing=(), address="", owners=(), active=True, aliases=()):
    return {"id": i, "name": name, "short": short, "active": active, "aliases": list(aliases),
            "billing": list(billing), "address": address, "owners": list(owners)}


PROPS = [
    P(1, "Oak Court", "OAK", billing=["Zed Holdings LLC"], address="12 Maple Avenue, Springfield, CT 06000", owners=["Quill Family Trust"]),
    P(2, "Birchwood Terrace", "BIRCH", billing=["Amber Plaza Realty LLC"], address="40 Linden Road, Riverton, CT 06001", owners=["Nova Partners"]),
    P(3, "Elm Gardens", "ELM", billing=["Nova Gardens LLC"], address="7 Cedar Lane, Riverton, CT 06001", owners=["Nova Partners"]),
    P(4, "Pine Heights", "PINE", billing=[], address="", owners=["Nova Partners"]),
]


class Match(unittest.TestCase):
    def top(self, term):
        return server.match_detail(term, props=PROPS)

    def test_name_wins(self):
        m = self.top("Oak Court")
        self.assertEqual((m[0][1]["id"], m[0][2]), (1, "name"))

    def test_billing_name_hit(self):
        m = self.top("Plaza")
        self.assertEqual((m[0][1]["id"], m[0][2]), (2, "billing name"))
        self.assertGreaterEqual(m[0][0], server.CONFIDENT)

    def test_address_hit(self):
        m = self.top("Linden Road")
        self.assertEqual((m[0][1]["id"], m[0][2]), (2, "address"))
        self.assertGreaterEqual(m[0][0], server.CONFIDENT)

    def test_owner_hit_returns_all_owner_properties(self):
        m = self.top("Nova Partners")
        self.assertEqual({x[1]["id"] for x in m if x[2] == "owner"}, {2, 3, 4})

    def test_owner_many_properties_not_truncated(self):
        many = [P(100 + i, "Unit Block %d" % i, owners=["Big Owner Co"]) for i in range(12)]
        self.assertEqual(len([x for x in server.match_detail("Big Owner", props=many) if x[2] == "owner"]), 12)

    def test_name_beats_billing_beats_address_beats_owner(self):
        props = [P(1, "Alpha Owner Zone"), P(2, "Z", billing=["Alpha Owner Zone"]),
                 P(3, "Y", address="Alpha Owner Zone"), P(4, "X", owners=["Alpha Owner Zone"])]
        self.assertEqual([x[1]["id"] for x in server.match_detail("Alpha Owner Zone", props=props)][:1], [1])
        order = [x[1]["id"] for x in server.match_detail("alpha owner", props=props)]
        self.assertEqual(order, [1, 2, 3, 4])

    def test_id_match(self):
        m = self.top("3")
        self.assertEqual((m[0][1]["id"], m[0][0], m[0][2]), (3, 100, "id"))

    def test_legal_suffix_alone_does_not_match(self):
        self.assertEqual(self.top("LLC"), [])

    def test_inactive_penalty(self):
        props = [P(1, "Cedar Place", active=False), P(2, "Cedar Place")]
        self.assertEqual(server.match_detail("Cedar Place", props=props)[0][1]["id"], 2)

    def test_old_entries_without_new_fields(self):
        old = [{"id": 1, "name": "Oak Court", "short": "OAK", "active": True, "aliases": ["the tower"]}]
        self.assertEqual(server.match_detail("the tower", props=old)[0][2], "alias")
        self.assertEqual(server.match_properties("Oak", props=old)[0][1]["id"], 1)


class Resolve(unittest.TestCase):
    def ids(self, v):
        with mock.patch.object(server, "properties", return_value=[dict(p) for p in PROPS]), \
             mock.patch.object(server, "_live_properties", return_value=[dict(p) for p in PROPS]):
            return server.resolve_property_ids(v)

    def test_billing_name_resolves(self):
        self.assertEqual(self.ids("Plaza")[0], [2])

    def test_address_resolves(self):
        self.assertEqual(self.ids("Cedar Lane")[0], [3])

    def test_owner_resolves_all_properties(self):
        self.assertEqual(sorted(self.ids("Nova Partners")[0]), [2, 3, 4])

    def test_weak_match_refuses_with_candidates(self):
        with self.assertRaises(server.ToolError):
            self.ids("Springfeld Oakk")

    def test_weak_candidates_present_in_error(self):
        props = [P(1, "Lakeview Towers"), P(2, "Lakeside Manor")]
        with mock.patch.object(server, "properties", return_value=props), mock.patch.object(server, "_live_properties", return_value=props):
            with self.assertRaises(server.ToolError) as cm:
                server.resolve_property_ids("Lakeview Gardens")
        self.assertIn("No confident match", str(cm.exception))
        self.assertIn("ID 1", str(cm.exception))

    def test_numeric_id(self):
        self.assertEqual(self.ids("4")[0], [4])

    def test_resolve_tool_shows_field_and_weak_note(self):
        with mock.patch.object(server, "properties", return_value=[dict(p) for p in PROPS]):
            out = server.t_resolve("Linden")
            self.assertIn("matched field: value", out)
            self.assertIn("address: 40 Linden Road", out)
            self.assertIn("4 active properties", server.t_resolve(""))
        props = [P(1, "Lakeview Towers")]
        with mock.patch.object(server, "properties", return_value=props):
            weak = server.t_resolve("Lakeview Gardens")
            self.assertIn("No confident match", weak); self.assertIn("ask the user", weak); self.assertIn("Lakeview Towers", weak)

    def test_resolve_owner_kind_lists_properties(self):
        with mock.patch.object(server, "properties", return_value=[dict(p) for p in PROPS]), \
             mock.patch.object(server, "api", return_value=(200, {"x-total-results": "1"}, [{"OwnerID": 9, "Name": "Nova Partners"}])):
            out = server.t_resolve("Nova", kind="owner")
        self.assertIn("Properties of these owners", out)
        for pid in ("| 2 |", "| 3 |", "| 4 |"): self.assertIn(pid, out)
        self.assertNotIn("| 1 |", out.split("Properties of these owners")[1])


class IndexFile(unittest.TestCase):
    def test_live_properties_fields(self):
        raw = [{"PropertyID": 5, "Name": "N", "ShortName": "S", "IsActive": True, "BillingName1": " Bill Co ", "BillingName2": "",
                "PrimaryAddress": {"Street": "1 A St", "City": "Town", "State": "CT", "PostalCode": "06000"},
                "BillingAddress": {}, "CurrentOwners": [{"Name": "Own A"}, {"Name": "Own A"}, {"Name": "Own B"}]},
               {"PropertyID": 6, "Name": "M", "IsActive": False, "BillingName1": None, "PrimaryAddress": None,
                "BillingAddress": {"Street": "9 B St", "City": "City"}, "CurrentOwners": None}]
        with mock.patch.object(server, "api", return_value=(200, {}, raw)) as api:
            ps = server._live_properties()
        self.assertEqual(api.call_count, 1)
        self.assertEqual(ps[0]["billing"], ["Bill Co"]); self.assertEqual(ps[0]["address"], "1 A St, Town, CT 06000")
        self.assertEqual(ps[0]["owners"], ["Own A", "Own B"])
        self.assertEqual((ps[1]["address"], ps[1]["owners"], ps[1]["billing"]), ("9 B St, City", [], []))

    def test_old_alias_file_rebuilds_once_and_keeps_nicknames(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "aliases.json")
            with open(fp, "w") as fh:
                json.dump({"properties": [{"id": 1, "name": "Oak", "short": "", "active": True, "aliases": ["the tower"]}]}, fh)
            live = [P(1, "Oak", billing=["Bill LLC"], owners=["Own"])]
            with mock.patch.object(server, "ALIASES", fp), mock.patch.object(server, "LOCAL", td), \
                 mock.patch.object(server, "_INDEX_REBUILT", [False]), \
                 mock.patch.object(server, "_live_properties", return_value=[dict(p) for p in live]) as lp:
                ps = server.properties()
                server.properties()
            self.assertEqual(lp.call_count, 1)
            self.assertEqual(ps[0]["aliases"], ["the tower"]); self.assertEqual(ps[0]["billing"], ["Bill LLC"])

    def test_old_alias_file_still_loads_when_rebuild_fails(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "aliases.json")
            with open(fp, "w") as fh:
                json.dump({"properties": [{"id": 1, "name": "Oak", "short": "", "active": True, "aliases": []}]}, fh)
            with mock.patch.object(server, "ALIASES", fp), mock.patch.object(server, "_INDEX_REBUILT", [False]), \
                 mock.patch.object(server, "_live_properties", side_effect=server.ToolError("down")):
                self.assertEqual(server.properties()[0]["name"], "Oak")


class Caps(unittest.TestCase):
    def test_caps(self):
        self.assertLessEqual(len(server.INSTRUCTIONS), 1500)
        self.assertIn("resolve(term) first", server.INSTRUCTIONS)
        for t in server.tool_list(): self.assertLessEqual(len(t["description"]), 1900, t["name"])


if __name__ == "__main__":
    unittest.main()
