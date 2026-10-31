# AI Usage, Problems and Debugging

## 1. AI tools used

| Tool | Where | Purpose |
|---|---|---|
| **Claude** (Anthropic) | During development | Coding assistant: project structure, code drafts, test ideas, reviewing edge cases |
| **Google Gemini API** (`gemini-3.5-flash-lite`) | Inside the application | Interprets the user's question into a structured intent (and, for other questions, a SELECT query that is then validated) |

## 2. How I used AI during development

- **Breaking down the problem.** I gave the assignment and CSV to the assistant and agreed on the key design decision first: *the LLM interprets, the database calculates.* LLMs are unreliable at arithmetic and can invent figures, and the assignment requires answers backed by real queries.
- **Generating code in stages:** data loading → SQL analytics → AI layer → API/UI → tests → docs. After each stage I ran the code and checked its output before moving on.
- **Verifying results independently.** I calculated the expected answers separately with pandas (total ₹14,39,200; Monitor ₹4,73,000; South ₹4,11,200; May peak ₹3,18,500) and wrote tests that compare the app's results to them.
- **Hunting for edge cases.** I asked the assistant what could go wrong (unknown names, destructive requests, missing months, malicious SQL), then wrote a test for each one.
- **What I did not delegate:** running everything on my own machine, checking the numbers, deciding the business rules (returned orders excluded, missing price not imputed), and making sure I can explain every file.

## 3. Problems encountered and how I debugged them

| # | Problem | How I found it | Fix |
|---|---|---|---|
| 1 | `python` was not found on Windows. A Microsoft Store placeholder ran instead. | Error message on the first command | Installed Python with *Add to PATH* and disabled the App execution aliases |
| 2 | `pytest` failed: `ModuleNotFoundError: No module named 'app'` | Traceback pointed at the test import | Added `pytest.ini` with `pythonpath = .`, so the project root is importable |
| 3 | Every AI request returned **503** | The server log showed only "LLM failure", so I improved `llm.py` to log the provider's own error message. That revealed `404: models/gemini-2.0-flash is no longer available`. | Switched to a current model and made the model configurable in `.env` |
| 4 | **The API key appeared in plain text in the server log** | Noticed it in the log line for the request URL | The key was a URL query parameter, and the HTTP library logs URLs. Moved it to the `x-goog-api-key` header and **rotated the key**. |
| 5 | "Total revenue for **Tablets**" returned the **overall** total, a confident wrong answer | Manual testing of unusual questions | The unknown word was being ignored. Added detection of unrecognised names after *for/of/in*. Now it refuses and lists the real products. Test added. |
| 6 | "Delete all sales" was answered as a revenue question | Same manual test run | Added an explicit refusal for write-type requests (the database was never at risk, because the connection is read-only) |
| 7 | Dates like `03-01-2026` are ambiguous (3 Jan or 1 March?) | Inspected the raw CSV before writing any code | Parse with the explicit format `%d-%m-%Y`. A test checks that TXN0001 is stored as `2026-01-03`. |
| 8 | Data traps: a returned order, quantity 0, a missing price | Profiled the CSV with pandas (`isna()`, `value_counts()`) | Explicit validation rules, a `rejected_rows` table with reasons, and a revenue view that counts Completed orders only |
| 9 | `with sqlite3.connect(...) as conn` does not actually close the connection | Code review | Used `contextlib.closing()` |

**General debugging approach:** reproduce the problem → read the *actual* error text (improving logging when it wasn't informative enough) → fix → add a test so it can't come back.

## 4. What I would improve with another day

1. **Conversation memory:** follow-up questions like "and in May?" reuse the previous intent.
2. **Charts in the UI:** a line chart for trends, plus CSV export of the supporting data.
3. **Evaluation set for the LLM:** 50+ phrasings of each question type, run against the real model to measure interpretation accuracy and compare models and prompts.
4. **CSV upload endpoint**, with the same validation and a data-quality report per upload.
5. **Agentic multi-step questions** (e.g. "Which region grew most between April and May, and which product drove it?"): let the LLM chain several validated queries, using a framework such as LangChain or Google ADK.
6. **Production hardening:** Docker, a GitHub Actions CI pipeline running the tests, authentication, rate limiting, and Postgres instead of SQLite.
7. **Imputation as an explicit option:** report the missing-price row's *estimated* value separately from the official figure, instead of only excluding it.
