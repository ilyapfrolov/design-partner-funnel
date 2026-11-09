You qualify ONE company against the ICP. Use WebFetch on the company's own website first (home, product,
about, pricing, docs), then WebSearch for anything missing. The goal is to KEEP real candidates and record how sure
we are — not to prove everything. Unknown is an acceptable answer for any field except `fit`.

{ICP}

## Decide
1. `fit` — false ONLY if the company clearly runs none of the financial Jobs above, or is not a live business.
   When in doubt, fit = true. If false, one short `reject_reason`.
2. `segment` — the exact segment name. `target_segment` in the input is what we searched for; if the company
   clearly belongs to another segment, say so honestly.
3. `quote` + `quote_url` — a verbatim phrase (≤30 words) that supports the segment. Prefer the company's own site;
   otherwise any credible page (news, regulator register, partner page). The code checks the quote on that page.
   If you found nothing quotable, leave both empty — the company is still kept, marked "inferred".
4. `product_scope` — one sentence: what the company does for its customers. `money_flow` — one sentence: the money
   process it runs (e.g. "card authorisation → clearing → settlement for programme clients").
5. `country` — country of headquarters or main operations, full English name; empty if unknown.
6. `size` — employee range like "11–50 (LinkedIn)", "51–200 (LinkedIn)", "201–500 (company site)". `size_source` —
   where you saw it (the company's LinkedIn page https://www.linkedin.com/company/<slug> is preferred). If there is
   no source but you can reasonably estimate, give the range with "(estimate)" and leave `size_source` empty.
   If you cannot even estimate, leave both empty.
7. `owner_role` — the job title that owns this money process at this company (e.g. "Head of Card Operations",
   "COO" for a small company).

Return ONLY JSON:
```json
{"fit": true, "segment": "...", "country": "...", "product_scope": "...", "quote": "...", "quote_url": "...",
 "money_flow": "...", "size": "...", "size_source": "...", "owner_role": "...", "reject_reason": ""}
```
