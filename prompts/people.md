Find real people at ONE company. Use WebSearch and WebFetch. The input `mode` says whom to find.

## mode = "executor"
People who run payment operations day to day, BELOW the head of function: payment operations managers and leads, reconciliation / settlement / treasury operations, merchant or card operations, finance operations, operations managers. Not heads, directors, VPs or C-level; not sales, marketing, engineering.

## mode = "owner"
The person who owns payment / money operations at the top: in a small company the founder, CEO or COO; in a larger one the Head / Director / VP of Operations, Payments or Finance Operations. One or two people, the most relevant first.

## Strategy (round number is in the input; follow that round)
- Round 1: the company's team / about / leadership pages, conference and webinar speakers, podcast guests, public LinkedIn profiles found via web search (search like: "<company>" "payment operations" site:linkedin.com/in).
- Round 2: job postings that name a hiring manager or team; press quotes; partner case studies that quote the company's people.

Rules:
- Only people who CURRENTLY work at this company.
- `source_url` — a page where the name and the role appear together. `evidence` — the verbatim line from that page with the name and role.
- `linkedin_url` — only a profile URL you actually saw (`https://www.linkedin.com/in/...`). Never construct one.
- Never guess emails.
- Exclude anyone in `already_known`.

Return ONLY JSON:
```json
{"candidates": [{"name": "...", "title": "...", "source_url": "...", "linkedin_url": "...", "evidence": "..."}], "note": "what you tried"}
```
