You qualify ONE company against the ICP definitions. Use WebFetch on the company's own website first (home,
product, about, pricing, docs), and WebSearch only for company size.

{ICP}

## Decide
1. `fit` — true only if the company's OWN site shows it runs one of the ICP money flows. Otherwise false with
   `reject_reason` (one short line).
2. `segment` — the exact segment name from the definitions (e.g. "ICP-3 Cards / issuing / BaaS"). `target_segment`
   in the input is what we searched for; if the company clearly belongs to another segment, say so honestly.
3. `quote` — a verbatim sentence or phrase (≤30 words) from a page on the company's OWN domain that proves the
   segment. `quote_url` — that exact page. The code opens it and checks the quote word for word.
4. `product_scope` — one sentence: what the company does for its customers. `money_flow` — one sentence: the
   concrete money process it runs (e.g. "card authorisation → clearing → settlement for programme clients").
5. `country` — country of headquarters or main operations, full English name.
6. `size` — employee range in the form "11–50 (LinkedIn)", "51–200 (LinkedIn)", "201–500 (LinkedIn)",
   "1,001–5,000 (LinkedIn)" or the same with another source in brackets. `size_source` — the page you took it
   from (the company's LinkedIn page https://www.linkedin.com/company/<slug> is preferred). If you cannot find a
   size with a source, leave both empty — never guess.
7. `owner_role` — the job title that owns this money process at this company (e.g. "Head of Card Operations",
   "COO" for a small company).

Return ONLY JSON:
```json
{"fit": true, "segment": "...", "country": "...", "product_scope": "...", "quote": "...", "quote_url": "...",
 "money_flow": "...", "size": "...", "size_source": "...", "owner_role": "...", "reject_reason": ""}
```
