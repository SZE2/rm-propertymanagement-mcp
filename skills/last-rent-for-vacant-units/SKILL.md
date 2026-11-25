---
name: last-rent-for-vacant-units
description: "Use when asked for the last rent, last tenant or last lease of a vacant unit, or of every vacant unit at a property. Matches each unit to its latest lease and that lease's recurring rent charge."
---

# Last rent for vacant units

## When to use
- "What was the last rent on the vacant units at <property>?"
- "Who was the last tenant in unit <x>, and what did they pay?"
- "Last rent before move-out" for one unit or a list of units.

## Steps
1. Resolve the property: resolve(term="<property>"). Use the exact PropertyID it returns.
2. Vacant units for that property, in one call:
   run_recipe(name="vacant_units", params={"property": "<id>", "as_of": "<date>"}).
   Keep the full list of UnitIDs and unit names; this list is the answer's row set.
3. Latest lease per unit, in one call for all units:
   query(resource="Leases", filters="UnitID,in,(<ids>)", fields="LeaseID,UnitID,TenantID,MoveInDate,MoveOutDate,StartDate,EndDate",
   orderby="-MoveInDate", first_per="UnitID").
   Keep the lease-to-unit link. Never join on TenantID alone: a tenant may have moved to another unit.
4. Recurring charges for those tenants, in one call:
   query(resource="RecurringCharges", filters="EntityKeyID,in,(<tenant ids>);EntityType,eq,Tenant",
   fields="EntityKeyID,UnitID,ChargeType.Name,Amount,FromDate,ToDate,Frequency").
   Do not filter on ToDate: that drops open-ended charges.
5. For each unit, take the latest regular rent charge that belongs to the same unit and lease period
   (it starts on or before the move-out date). That is the monthly rent. Add subsidy charge types
   (rental assistance and similar) as rent when the user wants the total rent.
6. Check the ledger only for exceptions, and only for that tenant and dates up to move-out:
   run_recipe(name="tenant_ledger", params={"tenant": ["<TenantID>"], "until": "<move-out date>"}).
   Exceptions are: no matching recurring charge; an open-ended charge that looks stale after move-out;
   a charge that ends well before move-out; a transfer from another unit; use-and-occupancy after the lease ended;
   a small or partial final charge.

## Check before answering
- Each vacant unit from step 2 appears exactly once. Compare the counts.
- Each monthly rent is backed by a recurring charge tied to that unit and lease period.
- Unknown stays unknown: no lease or no rent charge means "unknown", never market rent or another unit's rent.
- Keep two columns apart: monthly rent (recurring charge) and last ledger posting (date, type, amount), when they differ.
- State the property, the as-of date, and notes for transfers, use-and-occupancy and units without lease history.

## Pitfalls
- A current-rent summary (query summary="rent") can pick a charge that starts after the move-out. Check the dates.
- Use-and-occupancy is not rent. Report it separately; do not replace the last rent charge with it.
- The last ledger posting can be partial (a prorated final month). It is not the monthly rent.
- The frequency is unknown unless the record says it is monthly. Do not assume.
- Co-tenants can give several lease rows with the same unit and move-in date. If the kept tenant has no rent
  charge, check the other lease rows for that unit and move-in date before calling the rent unknown.
- Large ledgers are capped. Ask only for the dates you need instead of whole histories per tenant.
