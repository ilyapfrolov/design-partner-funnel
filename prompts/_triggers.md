## Trigger types (classify strictly)
- `BUILDING` — they launched, announced or put on a roadmap something in our area FOR THEIR CUSTOMERS: reconciliation, payment status / visibility, exception or dispute handling, payment recovery, AI in operations, finance tools for customers (collections, invoicing, payouts, treasury), embedded finance APIs.
- `CHANGE` — they changed pricing, payout timing, rules or a process; launched in a new country or got a new licence; announced a partnership or integration that changes how money flows.
- `SCALE` — dated growth numbers; a new payment method, rail or corridor; a funding round; job postings or leadership hires in operations, reconciliation, finance, payments; an award that comes with numbers.
- `MARKET` — an event on THEIR market, not about the company: a regulator rule, a payment-rail change, a compliance deadline in their country or segment that directly touches their money flows. Only in the market round.

## Every trigger
- `event_date` — `YYYY-MM-DD` of the event itself (`YYYY-MM` if the day is unknown). For a MARKET deadline you may give the future deadline date.
- `url` — the page that states it.
- `quote` — VERBATIM from that page, at most 25 words. Code checks it against the page. A paraphrase is rejected.
- `summary` — what happened, one sentence, with the exact numbers from the source.
- `ops_implication` — what it likely means for their payment operations, one sentence, as a hypothesis.
- `confidence` — `EVENT` if the source states it directly; `INTERPRETATION` if you inferred the type.

NOT triggers: general statements ("a leading provider", "growing fast"), negative customer reviews, incidents, outages, layoffs, lawsuits, anything personal about people outside their work role.
Prefer the most recent events. An event older than 270 days will not be used in a message.
