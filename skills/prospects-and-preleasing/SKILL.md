---
name: prospects-and-preleasing
description: "Use when asked whether a vacant unit is preleased, who applied for or reserved a unit, or what happened to an applicant. Separates signed future leases from reservations and open applicants."
---

# Prospects, reservations and preleasing

## When to use
- "Which vacant units are preleased at <property>?"
- "Who applied for unit <x>?", "Was this applicant approved, lost or moved in?"
- "Which units have a reserved move-in?"

## Steps
1. Vacant units first: run_recipe(name="vacant_units", params={"property": "<id>"}). Reserved units are
   flagged there (occupancy_flag / status_flag).
2. Preleased = a lease that starts in the future, one call for all units:
   query(resource="Leases", filters="UnitID,in,(<ids>);MoveInDate,gt,<today as YYYY-MM-DD>",
   fields="LeaseID,UnitID,TenantID,MoveInDate,StartDate,EndDate").
   Only a unit with such a lease is preleased in Rent Manager.
3. Applicants and reservations are Prospects:
   query(resource="Prospects", filters="PropertyID,eq,<id>",
   fields="ProspectID,Name,ProspectStatus,ApplicationDate,MoveInDate,ReservedUnitID,ReservedMoveIn,ProspectLostReasonDescription").
   ProspectStatus is the primary won/lost status: Prospect (open), Customer (became a tenant), Lost, Lost / Rejected.
   A reserved move-in is ReservedUnitID with ReservedMoveIn.
4. Stage dates: add embeds="ProspectStageHistory" (one row per stage with its dates). The current stage is the
   CurrentProspectStage embed.
5. The unit a prospect wanted: ReservedUnit or InterestedUnits embeds (use one embed per call). These are often
   empty; the unit can then sit only on the prospect's application-fee charge
   (query(resource="Prospects", filters="ProspectID,in,(<ids>)", embeds="Charges")).

## Check before answering
- Each unit is put in exactly one group: preleased (future lease), reserved (prospect reservation, no lease yet),
  open applicant, or nothing on record.
- Dates are reported under their own field names (MoveInDate, ReservedMoveIn, StartDate); do not merge them.
- The property scope and today's date are stated.

## Pitfalls
- An approved applicant without a lease in Rent Manager is not preleased here. Say "approved applicant, no lease yet".
- Prospects with no unit on record still exist: check their charges before saying a unit has no applicants.
- Co-tenants give several lease rows with the same unit and move-in date; count the unit once.
- A lease row can be entered long before the real move-in. Report the dates as stored.
- Leasing pipelines or turn trackers kept in other systems are separate sources outside this server.
  Name them as such when they matter; this guide covers Rent Manager only.
- Never print applicants' phone numbers or email addresses; names, units, statuses and dates are enough.
