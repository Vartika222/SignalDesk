# Dev question drafts

Written **before** searching the index or the filings, from what each form type normally
contains. Committed first so the commit timestamp shows the questions came before the answers.
Drafted by Claude; gold quotes are filled in afterwards by reading the filing text directly, not
through the retrieval system under test. Topics whose answers were already seen while debugging
(Nike and NVIDIA outlooks, Pfizer generic impact, Microsoft guidance location, Delta fuel risk,
Caterpillar backlog) are deliberately avoided.

A question may turn out unanswerable from the filings. If so it moves to the "can't answer" list
for Phase 2 refusal testing instead of being rewritten to fit the data.

| id | ticker | likely source | question |
|---|---|---|---|
| dev-01 | AAPL | 10-K risk factors | What risk does Apple describe from relying on outsourcing partners for manufacturing? |
| dev-02 | AAPL | 10-K MD&A | How did iPhone net sales change in Apple's latest fiscal year? |
| dev-03 | AAPL | 8-K | What total quarterly revenue did Apple report in its latest earnings release? |
| dev-04 | MSFT | 8-K | How fast did Microsoft Cloud revenue grow in the latest quarter? |
| dev-05 | MSFT | 10-K risk factors | What risks does Microsoft describe from developing and offering AI? |
| dev-06 | MSFT | 8-K | How much did Microsoft return to shareholders in the latest quarter? |
| dev-07 | NVDA | 10-K / 10-Q risk factors | How do US export controls affect NVIDIA's sales to China? |
| dev-08 | NVDA | 10-K / 10-Q | How concentrated is NVIDIA's revenue among its largest customers? |
| dev-09 | NVDA | 8-K | What gross margin did NVIDIA report for its latest quarter? |
| dev-10 | JPM | 8-K (as of 2026-09-01) | What net income did JPMorgan report for its most recent quarter? |
| dev-11 | JPM | 8-K / 10-Q | What is JPMorgan's CET1 capital ratio? |
| dev-12 | JPM | 8-K | How did JPMorgan's provision for credit losses change? |
| dev-13 | WMT | 8-K | How did Walmart U.S. comparable sales change in the latest quarter? |
| dev-14 | WMT | 8-K | How did Walmart's eCommerce sales grow? |
| dev-15 | WMT | 10-Q / 8-K | What does Walmart say about the effect of tariffs? |
| dev-16 | XOM | 8-K | What earnings did ExxonMobil report for the most recent quarter? |
| dev-17 | XOM | 8-K / 10-Q | What does ExxonMobil say about structural cost savings? |
| dev-18 | XOM | 8-K / 10-Q | What upstream production volume did ExxonMobil report? |
| dev-19 | PFE | 8-K | What full-year 2026 revenue range does Pfizer guide to? |
| dev-20 | PFE | 10-K risk factors | How does Pfizer say the Inflation Reduction Act affects its drug pricing? |
| dev-21 | PFE | 10-Q MD&A | Which products drove the change in Pfizer's revenue in the latest quarter? |
| dev-22 | CAT | 8-K | What adjusted profit per share did Caterpillar report in the latest quarter? |
| dev-23 | CAT | 8-K / 10-Q | How much have tariffs cost Caterpillar? |
| dev-24 | CAT | 10-Q MD&A | What does Caterpillar say about changes in dealer inventory? |
| dev-25 | NKE | 8-K (as of 2026-08-01) | How did Nike's revenue change in its most recent quarter? |
| dev-26 | NKE | 10-K | What does Nike say about its wholesale partners? |
| dev-27 | NKE | 8-K | How did NIKE Direct revenue change in the latest quarter? |
| dev-28 | DAL | 8-K | What revenue outlook did Delta give for the next quarter? |
| dev-29 | DAL | 10-K risk factors | What does Delta say about its unionized workforce and labor agreements? |
| dev-30 | DAL | 8-K | What operating margin did Delta report in the latest quarter? |

## Can't-answer set (for Phase 2 refusals; no gold passage exists)

- What will Apple's share price be at the end of next year?
- What did NVIDIA's CEO say about Blackwell demand on the earnings call? *(calls are not ingested)*
- Who is Microsoft's largest individual retail shareholder?
- What is JPMorgan's internal bonus pool for traders this year?
- How many stores will Walmart open in Mars colonies?
