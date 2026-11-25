---
name: occupancy-and-vacancy-counts
description: "Use when asked how many units are vacant or occupied, for an occupancy or vacancy rate, or which units are vacant. Explains how reserved, held and offline units are counted and how to cross-check."
---

# Occupancy and vacancy counts

## When to use
- "How many vacant units do we have at <property> / per property?"
- "What is the occupancy rate?"
- "Which units are vacant, and since when?"
- Two vacancy numbers disagree and the user wants to know why.

## Steps
1. Counts and rates per property:
   run_recipe(name="occupancy_summary", params={"property": "<name, id or empty for all>", "as_of": "<date>"}).
   It gives units, vacant, occupied, % vacant and % occupied, and lists offline units separately.
2. Which units: run_recipe(name="vacant_units", params={"property": "<id>"}) (list with UnitID, and
   status_flag / occupancy_flag for reserved and held units), or
   run_recipe(name="vacancy_report", params={"property": "<id>"}) for days vacant and market rent.
3. Count per property: run_recipe(name="vacancy_by_property", params={"property": "<ids>"}).
4. When numbers look off, cross-check with vacancy_report: it ends with a reconciliation against vacant_units
   and lists each unit that differs with the reason.
5. Always state the as-of date and the property scope.

## How units are counted
- Vacant means physically vacant: nobody lives in the unit on that date.
- Reserved units (a prospect reserved the unit) and units on a hold status (for example rent ready or under
  construction) are vacant. Today's vacant_units and vacancy_by_property include them and count them in the
  total; the flags show which ones they are.
- Offline units are units on a unit status that is not vacant while nobody lives there (for example down or
  offline). occupancy_summary leaves them out of the unit total and lists them as offline units.
  include_status_held_units=true counts them as occupied again (the older counting); say so if you use it.
- Parking, storage and other rentable items are left out unless include_other_rentables=true.
- Inactive properties are left out unless include_inactive_properties=true.
- Past dates: Rent Manager only answers "vacant as of" with the strict IsVacantAsOf filter. It excludes
  reserved units, and reservations in the past cannot be seen. A past count can therefore be lower than the
  same count taken on that day.

## Check before answering
- Vacant + occupied = units for each property (offline units are listed apart, not hidden).
- The as-of date, the property scope and every non-default switch are stated.
- Reserved and held units are named as such, not silently counted as occupied or dropped.

## Pitfalls
- Units has no IsVacant filter (IsVacant is an embed). Use the recipes, or IsVacantAsOf for a past date.
- The built-in Vacancy report and vacant_units can differ by a few units; the reconciliation at the end of
  vacancy_report explains each one. Do not pick one number silently.
- A unit with a lease that starts in the future is still vacant today; that is preleased, not occupied.
