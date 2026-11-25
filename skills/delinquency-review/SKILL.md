---
name: delinquency-review
description: "Use when asked who owes the most, for a delinquency list, or what a large tenant balance is made of and what follow-up is on record. Ranks from the delinquency recipe, then explains the top accounts from open charges, ledger, leases and notes."
---

# Delinquency review

## When to use
- "Who are the biggest delinquencies at <property> / in the portfolio?"
- "Why does this tenant owe so much? Is it rent?"
- "What collection or legal action is on record for the top account?"

## Steps
1. Settle the scope first: which properties (resolve(term="<property>"), or all), current or also past tenants,
   and the as-of date. By default rank residential tenants. Find each property's type with
   query(resource="Properties", fields="PropertyID,Name,PropertyType"); association and commercial properties
   go in separate sections, not in the residential ranking.
2. Full list, no hidden small balances:
   run_recipe(name="delinquency", params={"property": "<ids or empty for all>", "balance_over": 0,
   "days_late_over": 0, "tenants": "Current", "as_of": "<date>"}).
   The defaults (over 100, more than 5 days late) leave accounts out; say which thresholds you used.
   Use "tenants": "All" when former tenants matter.
3. Before calling a balance rent, classify it by its open charges:
   run_recipe(name="tenant_open_charges", params={"tenant": "<TenantID>"}).
   Group the open charges by charge type: rent, subsidy, fees, utilities, association dues, legal costs.
4. For the top account (or the one the user named), read the record, one tenant at a time:
   - Status: query(resource="Tenants/<TenantID>", fields="TenantID,Name,Status").
   - Leases: run_recipe(name="tenant_leases", params={"tenant": "<TenantID>"}).
   - Ledger: run_recipe(name="tenant_ledger", params={"tenant": "<TenantID>", "since": "<date>"}).
   - Notes: query(resource="Tenants/<TenantID>/HistoryNotes", orderby="-HistoryDate") for collection calls,
     letters and payment plans.
   - Evictions: query(resource="Tenants/<TenantID>/Evictions"), or the built-in eviction reports via
     list_reports(search="evictions").
5. Report what the records show: charge types and their periods, the recorded follow-up with dates, and its
   current status. If the newest note is old, give its date and say no newer record was found.

## Check before answering
- Scope, as-of date and thresholds are stated.
- Balances are quoted exactly as returned. The ranking comes from the recipe; never total or rank rows by hand.
- Association and commercial balances are in their own sections.
- "Rent" is only claimed for the part backed by rent charge types in the open charges.

## Pitfalls
- A balance is not automatically rent: fees, utilities, legal costs and association dues are common.
- The recorded reason for a charge is not the reason for non-payment. Do not invent one.
- No note in Rent Manager does not mean no action happened elsewhere. Say what is and is not on record.
- Never pull the whole history collection or embed attachments; use the one-tenant sub-resources above.
- A delinquency row can be an entity or a multi-unit account, not one person. Say so when it matters.
- Prospects with a balance are included by default (include_prospects); keep them apart from tenants.
