---
name: rent-roll-reconciliation
description: "Use when asked for a rent roll, current rents per unit, or to check a rent roll against Rent Manager. Fixes the unit population first, then rent from recurring charges plus subsidy, then reconciles the counts."
---

# Rent roll and its reconciliation

## When to use
- "Give me the rent roll for <property>" (today, or as of a date or month).
- "What is each unit paying?", "Total rent at <property>".
- "Does this rent roll match Rent Manager?"

## Steps
1. Resolve one exact property: resolve(term="<property>"). If the name matches several properties (for example
   one building split into two property records), say so and include each PropertyID, or ask which one.
2. Fix the unit population before any rent:
   query(resource="Units", filters="PropertyID,eq,<id>", fields="UnitID,Name,UnitType.Name,CurrentUnitStatus.UnitStatusType.Name,CurrentMarketRent.Amount").
   Group by unit type. Decide what counts as a unit: parking, storage and other rentable items are not
   apartments; units on an offline status have nobody living there. Name what you left out and why.
3. Prefer a ready rent roll:
   run_recipe(name="rent_roll", params={"property": "<id>", "start": "<date>", "end": "<date>"}),
   or a built-in rent roll report (list_reports(search="rent roll"), then run_report). The report rows are
   one per unit, or one per tenant and charge type, depending on the report.
4. Rent per tenant comes from recurring charges, not the ledger:
   query(resource="RecurringCharges", filters="UnitID,in,(<unit ids>)", summary="rent", as_of="<date>").
   This splits tenant portion, subsidy portion and total per tenant. Subsidy charge types (rental assistance,
   housing assistance payments and similar) are rent.
5. Reconcile counts against the vacancy side:
   run_recipe(name="occupancy_summary", params={"property": "<id>", "as_of": "<date>"}).
   Occupied + vacant (reserved and held units included) must equal the unit total from step 2, apart from
   the offline units it lists.
6. If the reply says MORE, page with read_result before you count or total anything.

## Check before answering
- The unit total matches step 2, and each unit appears once (or once per tenant, said so).
- Occupied, vacant and reserved counts match occupancy_summary for the same date.
- Totals are copied from the report or recipe totals, never added up by hand.
- State the property, the as-of date or period, and the units left out of the population.

## Pitfalls
- A rent roll can have two rows for one unit: the tenant portion and the subsidy portion. Sum them per unit.
- Rent is not a field on Leases. Ledger rows show what was posted, not the rent in force.
- Do not filter RecurringCharges on ToDate: that drops open-ended charges, which are usually the current rent.
- Keep future rent changes (FromDate after the as-of date) apart from the rent in force.
- A raw unit-type count is not a count of rentable units: old records can sit beside the current units.
  Do not drop units on unit type or a missing market rent alone; show the evidence for each exclusion.
- Vacant units have no rent charge. Show market rent for them only when the user asks, and label it market rent.
