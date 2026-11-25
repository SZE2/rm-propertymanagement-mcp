---
name: unit-custom-fields
description: "Use when a question depends on a unit classification that is not a standard field (renovated, remodeled, finish level, program or any custom unit label). Finds the custom field, scopes it to the property and counts units, not rows."
---

# Unit custom fields (user-defined values)

## When to use
- "How many renovated units are at <property>?"
- "Which units are marked <label>?", "Break the units down by <custom category>."
- Any unit label the user names that is not a standard unit field.

## Steps
1. Find the field. The name and values differ per installation, so look, do not guess:
   - describe_model(model="Units", section="embeds", contains="UserDefined") shows the UserDefinedValues embed.
   - describe_model(model="Units", section="subs", contains="UserDefined") shows Units/{id}/UserDefinedValues.
   - contains="renov|remodel" (a | b) matches either word in field names.
2. Resolve the property (resolve(term="<property>")) and scope the units to it.
3. Look at one or two units first to learn the field name and its values:
   query(resource="Units/<UnitID>/UserDefinedValues").
4. Count units per value in one call:
   query(resource="Units", filters="PropertyID,eq,<id>", fields="UnitID,Name,UserDefinedValues.Name,UserDefinedValues.Value",
   where="UserDefinedValues.Name,eq,<field name>", group_by="UserDefinedValues.Value", count_distinct="UnitID").
   The embed gives one line per unit and value, so rows are not units; count_distinct=UnitID counts units.
5. Count all units of the property the same way (without where) and compare: units missing from the grouped
   result have no value for that field.

## Check before answering
- Every observed value is reported, including values you did not expect and blank or missing ones.
- The unit counts per value add up to the property's unit total, or the gap is explained (no value, several values).
- The property scope and the field name you used are stated.
- If the reply says CAPPED, PARTIAL or MORE, the count is not complete: page with read_result or say so.

## Pitfalls
- A grouped count without count_distinct counts rows, not units.
- Do not assume a fixed category list (for example only "yes" and "no"). Show what is actually stored.
- One unit can carry several values or the same field twice. Say how you counted those.
- Values are free text in many installations: different spelling or case can mean the same category.
  List them as found and point out likely duplicates instead of merging them silently.
- Never claim a complete count when data is missing or the meaning of a value is uncertain.
