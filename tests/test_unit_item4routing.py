"""v3 item 4: catalog shape + offline routing (find_recipe first rank) for visible recipes. No network."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

PHRASES = {
    "aged_receivables": ["show me the AR aging buckets by tenant", "receivables 30 60 90 days"],
    "charges_by_type": ["what did we bill last month by charge type", "total fees charged in March"],
    "collections": ["how much cash did we collect last month", "rent collected by charge type"],
    "delinquency": ["who is behind on rent", "list tenants with past due balances"],
    "delinquency_by_property": ["delinquency summary by property", "total owed per property"],
    "gl_activity": ["general ledger activity for the quarter", "gl detail by account"],
    "lease_expirations": ["which leases are expiring soon", "renewals due next quarter"],
    "lender_package": ["I need a lender package for a refinance", "underwriting due diligence bundle"],
    "move_ins": ["who moved in this month", "list new tenants and move-ins"],
    "move_outs": ["who moved out last month", "turnover: tenants who vacated"],
    "occupancy_summary": ["what is our occupancy rate", "percent vacant per property"],
    "profit_and_loss": ["profit and loss detail by account", "income statement for last month"],
    "profit_and_loss_summary": ["what was NOI last month", "net operating income and total expenses"],
    "property_snapshot": ["KPI snapshot of the property", "give me a scorecard overview"],
    "rent_roll": ["show the rent roll", "scheduled rent for every unit"],
    "tenant_leases": ["when did this tenant move in", "month to month lease history of a tenant"],
    "tenant_ledger": ["tenant ledger with running balance", "payment history for a tenant"],
    "tenant_open_charges": ["what does this tenant owe right now", "unpaid charges for a tenant"],
    "trial_balance": ["trial balance as of today", "debits and credits per GL account balances"],
    "vacancy_report": ["vacancy report with days vacant", "market rent lost on vacant units"],
    "vacant_units": ["which units are vacant", "list empty available units"],
}

class T(unittest.TestCase):
    def visible(self):
        return [r for r in server.load_recipes() if r.get("catalog") is not False]

    def test_phrasings_cover_all_visible(self):
        self.assertEqual({r["name"] for r in self.visible()}, set(PHRASES))

    def test_routing_first_rank(self):
        total = ok = 0
        miss = []
        for name, qs in PHRASES.items():
            for q in qs:
                total += 1
                hits = server.recipe_search(q)
                if hits and hits[0][1]["name"] == name: ok += 1
                else: miss.append((q, hits[0][1]["name"] if hits else None))
        self.assertGreaterEqual(ok / total, 0.9, "first-rank %d/%d, misses: %s" % (ok, total, miss))

    def test_use_field(self):
        for r in self.visible(): self.assertTrue(0 < len(r.get("use", "")) <= 48, r["name"])

    def test_hidden_not_in_catalog_but_findable(self):
        cat = server.recipe_catalog(5000)
        for n in ("vacancy_by_property", "payments_received", "owners"):
            self.assertNotIn(n + "(", cat)
            self.assertIn(n, {r["name"] for r in server.load_recipes()})
        self.assertEqual(server.recipe_search("vacancy by property count")[0][1]["name"], "vacancy_by_property")

    def test_catalog_uses_untruncated_use(self):
        cat = server.recipe_catalog(1500)
        for r in self.visible(): self.assertIn(r["use"], cat)

    def test_description_size(self):
        self.assertLessEqual(len(server.tool_desc("run_recipe", "")), 1900)

    def test_no_shared_keyword_phrase(self):
        seen = {}
        for r in self.visible():
            for k in r.get("keywords", []):
                key = k.lower().replace(" ", "")
                self.assertIn(seen.get(key, r["name"]), (r["name"],), "%s shared by %s and %s" % (k, seen.get(key), r["name"]))
                seen[key] = r["name"]

    def test_payments_received_headline(self):
        r = next(r for r in server.load_recipes() if r["name"] == "payments_received")
        self.assertTrue(r.get("headline"))

if __name__ == "__main__": unittest.main()
