---
name: report-parameters-and-basis
description: "Use when running a built-in Rent Manager report (balance sheet, income statement, trial balance, cash flow, general ledger, rent roll) or when the basis, period or other report options matter."
---

# Built-in reports, their parameters and Cash vs Accrual

## When to use
- The user names a standard report ("balance sheet", "income statement", "trial balance", "general ledger").
- The answer depends on basis (cash or accrual), an as-of date, a period, or an include/exclude toggle.

## Steps
1. Standard report by name: list_reports(search="<words>") and pick the one matching report. Recipes are only
   shortcuts; a report with no recipe is still available through run_report.
2. Read that one report's options with report_info(id="<id>"), or from the parameter list that the first
   run_report attempt returns. Do not guess parameter keys.
3. Decide every meaning-changing option on purpose:
   - Basis: 1 = Accrual, 2 = Cash. The default is usually Cash. If the user said accrual, send Basis,1.
   - Dates: AsOfDate / period. State the date you used; the default is today.
   - Include-inactive or similar toggles: keep the default unless the user asked otherwise.
4. Run it: run_report(id="<id>", parameters="Key,Value;Key,Value", property="<name or id>").
   One call covers all properties when property is omitted.
5. Read the "Parameters used" line at the top of the result. It lists the values actually sent and marks defaults.
6. If the reply says MORE, page with read_result(result_id="<id from the MORE line>", offset=<n>). Do not raise show.

## Check before answering
- Your answer states the basis, the as-of date or period, and the property scope, copied from "Parameters used".
- Numbers are quoted exactly as returned; no arithmetic of your own.
- If the user asked for accrual, the Parameters used line says Accrual.

## Pitfalls
- A recipe name that is really a report (for example balance_sheet) returns NOT RUN with the run_report call to use. Follow it.
- NOT RUN means nothing was sent to Rent Manager: fix the call as the reply says, do not wait and retry.
- Some reports exist only as documents. NOT RUN will point to report_pdf; use the PDF for the user, not for numbers.
- Comparing Cash and Accrual: run the same report twice, once with Basis,1 and once with Basis,2, and compare
  line by line. Mostly receivables, payables and income lines differ.
