The company itself has no recent qualifying event. Find MARKET triggers: recent events on its market that directly touch its money flows. Use WebSearch and WebFetch.

{TRIGGERS}

## What to look for (company country and segment are in the input)
- Central bank / regulator rules on payments, e-money, stablecoins, cards, lending, remittances in that country.
- Changes to payment rails (instant payment schemes, limits, fees, settlement windows).
- Compliance deadlines (e-invoicing, reporting, licensing, travel rule) within the next 120 days or announced in the last 270 days.
- Scheme changes (Visa / Mastercard rules) that affect the segment.

Return only events that clearly apply to a company of this segment in this country. Type every trigger as `MARKET`.
In `ops_implication` say concretely how it touches this company's operations.

Return ONLY JSON:
```json
{"triggers": [{"type": "MARKET", "event_date": "...", "url": "...", "quote": "...", "summary": "...", "ops_implication": "...", "confidence": "EVENT"}],
 "process_a": "...", "process_b": "...", "notes": "..."}
```
