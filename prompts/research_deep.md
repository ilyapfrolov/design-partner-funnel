You do a DEEPER search for outreach triggers for ONE company. A first pass over news, the company site and job boards found too few recent events. Use WebSearch and WebFetch.

{TRIGGERS}

## Mandatory strategies — try every one, in this order
1. Regional and trade press in the company's country and segment (e.g. fintech news sites for that country), last 6 months.
2. The company's partners: announcements by banks, card schemes, processors, platforms that mention this company.
3. Conferences, webinars, podcasts, YouTube talks with the company's people in the last 6 months.
4. Public posts by the company and its leadership (LinkedIn / X via web search), app store release notes.
5. Regulator registers and licence announcements for the company.

Do not return events whose URL is in `already_known_urls`.
Prefer events from the last 60 days; never return events older than 270 days.

Return ONLY JSON:
```json
{"triggers": [{"type": "...", "event_date": "...", "url": "...", "quote": "...", "summary": "...", "ops_implication": "...", "confidence": "EVENT"}],
 "process_a": "...", "process_b": "...", "notes": "strategies tried and what each gave, one line each"}
```
