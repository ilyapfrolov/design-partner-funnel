Find real people at ONE company who run payment operations day to day, BELOW the head of function: payment operations managers and leads, reconciliation / settlement / treasury operations, merchant or card operations, finance operations, operations managers. Use WebSearch and WebFetch.

## Strategy (round number is in the input; follow that round)
- Round 1: the company's team / about pages, conference and webinar speakers, podcast guests, public LinkedIn profiles found via web search (search like: "<company>" "payment operations" site:linkedin.com/in).
- Round 2: job postings that name a hiring manager or team; press quotes from operations staff; partner case studies that quote the company's ops people.

Rules:
- Only people who CURRENTLY work at this company in an operations / finance-ops role. Not heads or C-level (they are already known), not sales, marketing, engineering.
- `source_url` — a page where the name and the role appear together. `evidence` — the verbatim line from that page with the name and role.
- `linkedin_url` — only a profile URL you actually saw (`https://www.linkedin.com/in/...`). Never construct one.
- Never guess emails.
- Exclude anyone in `already_known`.

Return ONLY JSON:
```json
{"candidates": [{"name": "...", "title": "...", "source_url": "...", "linkedin_url": "...", "evidence": "..."}], "note": "what you tried"}
```
