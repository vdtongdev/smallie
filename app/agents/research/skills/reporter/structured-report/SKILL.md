---
name: structured-report
description: A sectioned report, for when the user asks for a report/analysis/overview (báo cáo, phân tích, tổng quan) or the findings cover several subtopics.
---
Use Markdown with this structure. Translate the headings into the user's language.

## <Title: the topic in a few words>
**TL;DR:** 2–3 sentences with the main conclusion.

### Key findings
3–6 bullets, each one fact or conclusion ending with its source link (`([source](URL))`).

### Details
One short subsection (`####`) per subtopic, in paragraphs with reference numbers ([1], [2]). Explain context, causes and implications using only the findings.

### Data
A Markdown table when there are comparable numbers (prices, dates, metrics, papers), with reference numbers in the cells or the header. Leave it out otherwise.

### Caveats
Gaps, conflicting sources, data dates and preprint status. Leave it out if there are none.

### Sources
The numbered list of the sources cited by number, as in CITATIONS: `1. [Title](URL)`, `2. report.pdf, p. 4`, `3. Market database, <date>`. This is the last section.

Keep it to ~400–700 words. Cite every fact. Never add facts that aren't in the findings.
