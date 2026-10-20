# Known issues (beta)

This is beta software, unofficial, and read-only. Answers can be wrong: check important numbers in Rent Manager.
To report a problem, ask your Claude to file a GitHub issue (it shows you the text first). See `CLAUDE.md`.

## Privacy and storage (fix planned)
- **Results are saved to disk by default.** Each query, report or recipe writes its full result (JSON and CSV) to the `out/` folder on your computer. It never leaves your computer and is not uploaded, but it holds real tenant and financial data and is never cleaned automatically. Delete old files yourself. Planned change: store nothing by default and save only when you ask.
- **Downloaded files and attachments (`download_file`, `find_attachments`).** Attachments (lease PDFs and others) contain tenant data. PDFs and other binary files are saved to `out/files/` on your computer for you to open; text files are returned as text and not saved. Downloaded files in `out/files/` and old result/PDF files in `out/` that this program created are deleted after 14 days (`RM_OUT_KEEP_DAYS`, `0` = never delete; checked at start and once a day). Nothing else is ever deleted. The signed download link (`mode='link'`) works for anyone who has it: treat it like a password.
- Scanned PDFs have no text layer: the text extraction says so and a person has to read them. Text extraction needs `pdfplumber` or `pypdf` installed (optional).
- **The call log is on by default** (`local/mcp_calls.jsonl`, tool arguments only, can include names). It stays on your computer. Turn it off with `RM_MCP_LOG=off`. Planned change: off by default.
- PII masking (`RM_MASK_PII=1`) hides phone, email and SSN-type fields only. It does not hide names or PDFs.

## Row cap and Rent Manager memory limit
- **Replies are size-capped; the rest is paged.** A reply holds about 9,000 characters. Longer tables show their first rows and start with a `MORE:` line (only 10 rows when the result has over 1,000); `read_result` pages the rest (it reads the saved result in `out/`, a snapshot of that call, not a new query). It needs result saving, which is on by default; a result deleted after `RM_OUT_KEEP_DAYS` days must be run again. `run_recipe` bundles (several recipes in one call) are still cut at 27,000 characters as a whole; if a bundle reply ends with `[clipped ...]`, run the missing part as its own recipe.
- **Large pulls are capped.** A raw-row pull (`query`, and recipes that read rows) returns at most `RM_MAX_ROWS` rows (default 5000). An explicit `max_records` raises it up to `RM_MAX_ROWS_HARD` (default 20000); nothing goes beyond that. When more rows exist the answer starts with one `CAPPED: showing the newest N of M rows ...` line (or `oldest` / `first`) that says how to get the rest. Where the API can sort by a date, the newest rows are kept; where it cannot (for example `Charges`) the first rows in the API's own order are shown and the line says so. Totals, sums and counts computed from a capped pull cover only the rows shown: narrow by date, property or tenant, or use the built-in report named in the line.
- **Rent Manager can run out of memory.** Some requests (an unfiltered `Charges`, `Payments` or `History`, or a very long ledger) make Rent Manager itself fail with HTTP 500 `System.OutOfMemoryException`. The program then retries with smaller pages (1000, 500, 250, 100, 50) and keeps what it read; rows read before a final failure are returned flagged `PARTIAL`, otherwise you get a plain "ran out of memory (its limit, not a bug here)" error with a built-in report that covers the need. Sizing an unfiltered huge table can take 10 to 20 seconds.
- No built-in report takes a tenant filter. For one or several tenants use the `tenant_ledger` recipe (`tenant` or a list, `since`, `until`; newest 1000 rows per tenant by default, `max_rows` up to 20000; the running balance is the ledger's own). For a whole property use report 30 (ChargeDetail) and keep the rows of the tenants you want. Reports 284/851 (statements) and 28 do not return JSON.

## Setup
- ChatGPT desktop / Codex connection settings are included (0.8.0-beta). The development installation was registered with approval, and the OpenAI backend passed discovery and live vacancy, delinquency and PDF checks from the saved configuration on Windows. The live desktop conversation also discovered all 15 tools and successfully called `find_recipe`; see [the setup guide](docs/CHATGPT.md). Broader conversational accuracy evaluations remain release work. ChatGPT web passed discovery and a recipe lookup through Secure MCP Tunnel; scoped live web queries and file delivery remain unverified. A development checkout needs setup before live calls, even if another copy is configured for Claude.
- The developer unit tests need `spec/` (created by `setup.py`); on a fresh clone before setup about 17 of them fail with `spec/index.json missing`. Users do not need to run them.
- First setup takes about 10 minutes (it downloads the API structure from your Rent Manager). A bundled starter copy is planned so it works immediately.
- Tested on Windows only (one install, one Rent Manager location). macOS, Linux and companies with several locations are untested.
- The macOS SSL fix and file-permission hardening on macOS/Linux are written but have not been run on a real Mac.

## Accuracy limits
- Vacancy counts reserved and held units as vacant and flags them. A past "as of" date uses a stricter definition and the answer says so; do not compare it with today's number.
- "Rent" means the recurring charges on the lease, including subsidy charge types. The rent classifier uses name patterns plus an editable file (`local/charge_types.json`); check it matches how your company names charge types.
- Rent is shown as of today. A holdover charge after a rent charge ends is shown as that charge, not the rent at move-out.
- Detecting stale charges of tenants who moved out checks up to 25 tenants per run, then skips with a note.
- Attachment search: lease PDFs can be in tenant history notes, in lease-renewal attachments (often on an EARLIER renewal) or in custom fields; check each source. Owner and service-issue history had no files in the test data, so those routes are unproven. Only one Rent Manager location has been tested live (the `location` argument is covered by offline tests).
- The delinquency report's own "Percent Delinquent" is misleading when prospects are included. Dollar amounts are not affected.
- Profit and loss uses cash basis by default; accrual is selectable.

## Speed
- Portfolio-wide aged receivables and the lender package take roughly 20 to 40 seconds on a large portfolio. Single answers usually take under 2 seconds.
- Payments cannot be filtered by property in the API, so payment totals are portfolio-wide.
- Counting very large tables without a filter can take 15 or more seconds.

## Not built yet
- Lease audit and blended rent roll workflows (the pieces exist: recurring charges with `summary='rent'`, `find_attachments`, `download_file`).
- Normal Claude mobile/cloud chats and claude.ai have no remote setup here (why: [docs/WEB-CHAT.md](docs/WEB-CHAT.md)). ChatGPT web has a separate private tunnel guide; mobile and Work Cloud execution remain untested.
- The free-form Rent Manager Help text is not included.
- Haiku end-to-end evals of the four persona question sets have not been run.
