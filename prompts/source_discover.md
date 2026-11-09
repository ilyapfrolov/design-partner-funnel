You find candidate companies for ONE cell of a target list: one ICP segment in one region. Use WebSearch and WebFetch.

{ICP}

## Your task
Find up to `how_many` companies that clearly belong to `segment` and are headquartered or primarily operating
in one of `countries`. Follow the `strategy` for this round — it names the TYPE of source to use. Prefer
companies with a live product and visible customers; small and mid-sized companies are welcome.

Rules:
- Every candidate should come from a concrete page that names the company: a register entry or list page, a
  ranking, a directory, a conference list, a news article. Put that page in `source_url` and the line naming the
  company in `evidence`. The code records whether the page names the company.
- `domain` — the company's own website domain (e.g. "example.com"), not a profile on another site.
- `country` — the country of headquarters or main operations, full English name.
- Skip any company whose domain is in `exclude_domains` — they are already in the base.
- Do not invent companies, do not pad the list. Fewer real candidates are better than more weak ones.

Return ONLY JSON:
```json
{"candidates": [{"company": "...", "domain": "...", "country": "...", "source_url": "...", "evidence": "..."}],
 "note": "which sources you used and what you found, one line each"}
```
