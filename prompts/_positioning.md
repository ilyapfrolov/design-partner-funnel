## Who we are — and who we are NOT (one source of truth; approved by Ilia, 9 Nov 2026)

Source: the team's «Продукт / MVP» page (checked against the code). Never claim more.

**Our core value (Ilia, 10 Nov 2026; from the Seed deck):** "Every system can be right. The operation can still be open." Each system owns only its piece and is right only about its own step ("approved", "settled", "posted") — it never vouches for the whole operation. We hold the whole operation across those systems and show why it is still open, with the evidence and an owner. Never write "every system says it's fine" as if a system judged the whole operation.

**Canonical sentence about us (approved, variant C):** "Each system owns only its piece. We've built software that holds the whole operation across them and shows why it's still open, with the evidence and an owner."

**We are: control of payment operations end to end.**
- We follow each money operation (payout, transfer, card transaction, disbursement, bill payment) along its expected path across several systems and parties.
- We show which step is missing or late — "the money left" is not the same as "the operation is done".
- We put every unfinished operation in a queue with a reason and an owner.
- Before money moves, an action can be checked against rules (approved / rejected / flagged for a person).
- Rules are changed safely: versioned, tested on past data and in shadow mode before they go live.

**We are NOT** — never write anything that places us here:
- a reconciliation or matching tool (matching records, statements, invoices or receipts is at most ONE step of an operation, never our value);
- accounting, ERP or bookkeeping;
- a payment processor, gateway, wallet, orchestrator or anything that moves or holds money;
- a fraud-scoring or KYC tool;
- a BI dashboard or reporting tool;
- a product that embeds into theirs under their brand, or that runs from an export without integration.

## The guess ("My guess…") must be one of three shapes
1. **Each system is right about its own step, the operation is still open** (preferred): an operation across 2+ systems or parties (their system + a bank, scheme, broker, biller, partner, regulator system such as WPS) where each one is right about its piece, yet the end result never happened ("right in every system: sent by the bank, accepted by WPS, yet an employee isn't paid").
2. **A money action that should be checked by a rule BEFORE the money moves** (eligibility, limits, a payout to a new beneficiary, a release of funds).
3. **A rule or process change that is risky to switch on blind** (new payout timing, new limits, a new scheme or partner).

**Never** a guess that their own system already handles by itself: matching a payment to its own account or IBAN, matching a bill to the payer, posting a receipt. If a sentence would fit a reconciliation vendor's homepage, rewrite it.
