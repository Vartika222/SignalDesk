# Phase 2 question drafts (20 dev + 50 held-out + 10 can't-answer)

Written before searching for answers, like `drafts.md`, and committed first. Drafted by Claude from
what each form normally contains, not from the data. The held-out set is scored **once**, in notebook
03, after every retrieval and prompt choice is frozen on dev.

Mix per company: guidance and results (8-K), MD&A drivers (10-Q), risk factors and business (10-K),
and figures that usually live in tables. Phrasing varies on purpose: some questions use the filing's
own words, some don't, and some say "latest" while others give an as-of date.

## Dev additions (dev-31 … dev-50)

| id | ticker | question |
|---|---|---|
| dev-31 | AAPL | How did Apple's Services revenue change in its latest fiscal year? |
| dev-32 | AAPL | What does Apple say about competition in its markets? |
| dev-33 | MSFT | How did Microsoft's Intelligent Cloud segment revenue change in the latest quarter? |
| dev-34 | MSFT | What does Microsoft say about its capital expenditures on datacenters? |
| dev-35 | NVDA | What was NVIDIA's total revenue in its most recent quarter? |
| dev-36 | NVDA | What does NVIDIA say about its dependence on third-party foundries? |
| dev-37 | JPM | What return on tangible common equity did JPMorgan report? |
| dev-38 | JPM | How did JPMorgan's investment banking fees change? |
| dev-39 | WMT | What adjusted EPS guidance did Walmart give for the full fiscal year? |
| dev-40 | WMT | How much did Walmart's global advertising business grow? |
| dev-41 | XOM | What does ExxonMobil say about the effect of Middle East disruptions on its operations? |
| dev-42 | XOM | How much cash did ExxonMobil return to shareholders in the latest quarter? |
| dev-43 | PFE | What adjusted diluted EPS guidance does Pfizer give for 2026? |
| dev-44 | PFE | What does Pfizer say about patent expirations affecting future revenue? |
| dev-45 | CAT | How did Caterpillar's sales and revenues change in the latest quarter? |
| dev-46 | CAT | What does Caterpillar expect for full-year 2026 sales? |
| dev-47 | NKE | How did Nike's revenue in Greater China change in the latest quarter? |
| dev-48 | NKE | What gross margin did Nike report in its most recent quarter? |
| dev-49 | DAL | What free cash flow did Delta report or expect? |
| dev-50 | DAL | How did Delta's premium revenue change? |

## Held-out (ho-01 … ho-50)

| id | ticker | question |
|---|---|---|
| ho-01 | AAPL | What were Apple's total net sales for fiscal 2026? |
| ho-02 | AAPL | What does Apple say about risks from international trade and tariffs? |
| ho-03 | AAPL | How did Mac net sales change in fiscal 2026? |
| ho-04 | AAPL | What diluted earnings per share did Apple report for the September quarter? |
| ho-05 | AAPL | What does Apple say about its reliance on single-source component suppliers? |
| ho-06 | MSFT | How did Azure revenue grow in the latest quarter? |
| ho-07 | MSFT | What operating income did Microsoft report in its latest quarter? |
| ho-08 | MSFT | What does Microsoft say about cybersecurity threats to its business? |
| ho-09 | MSFT | How did Microsoft's More Personal Computing revenue change? |
| ho-10 | MSFT | How did revenue in Microsoft's Productivity and Business Processes segment change? |
| ho-11 | NVDA | What was NVIDIA's Data Center revenue in the second quarter of fiscal 2027? |
| ho-12 | NVDA | How did NVIDIA's Gaming revenue change in the latest quarter? |
| ho-13 | NVDA | What does NVIDIA say about supply constraints or long manufacturing lead times? |
| ho-14 | NVDA | How much did NVIDIA return to shareholders through buybacks and dividends? |
| ho-15 | NVDA | What does NVIDIA say about China's antitrust investigation? |
| ho-16 | JPM | What net revenue did JPMorgan report for the third quarter of 2026? |
| ho-17 | JPM | How did JPMorgan's Consumer & Community Banking net income change? |
| ho-18 | JPM | What does JPMorgan say about its liquidity or high-quality liquid assets? |
| ho-19 | JPM | What net interest income did JPMorgan report? |
| ho-20 | JPM | What were JPMorgan's net charge-offs in the latest quarter? |
| ho-21 | WMT | What total revenue did Walmart report for the latest quarter? |
| ho-22 | WMT | How did Sam's Club U.S. comparable sales change? |
| ho-23 | WMT | How did Walmart International net sales change? |
| ho-24 | WMT | What operating income growth did Walmart report? |
| ho-25 | WMT | What does Walmart say about its inventory levels? |
| ho-26 | XOM | What earnings per share did ExxonMobil report for the second quarter of 2026? |
| ho-27 | XOM | What does ExxonMobil say about production in Guyana? |
| ho-28 | XOM | What cash flow from operating activities did ExxonMobil report? |
| ho-29 | XOM | How did ExxonMobil's Energy Products earnings change? |
| ho-30 | XOM | What capital expenditures did ExxonMobil report? |
| ho-31 | PFE | What revenue did Pfizer report for the third quarter of 2026? |
| ho-32 | PFE | How did Paxlovid revenue change? |
| ho-33 | PFE | How did revenue from the Vyndaqel family change in the latest quarter? |
| ho-34 | PFE | What does Pfizer say about cost-realignment or cost-saving programs? |
| ho-35 | PFE | What does Pfizer say about the 340B drug pricing program? |
| ho-36 | CAT | What profit per share did Caterpillar report in the third quarter of 2026? |
| ho-37 | CAT | How did sales in Caterpillar's Energy & Transportation segment change? |
| ho-38 | CAT | What operating profit margin did Caterpillar report? |
| ho-39 | CAT | What does Caterpillar say about price realization? |
| ho-40 | CAT | How much ME&T free cash flow did Caterpillar generate? |
| ho-41 | NKE | How did NIKE Brand wholesale revenue change in the latest quarter? |
| ho-42 | NKE | What does Nike say about inventory? |
| ho-43 | NKE | What diluted EPS did Nike report for its most recent quarter? |
| ho-44 | NKE | How did Converse revenue change? |
| ho-45 | NKE | What does Nike say about tariffs affecting its costs? |
| ho-46 | DAL | What total revenue did Delta report for the September quarter? |
| ho-47 | DAL | What earnings per share outlook did Delta give for the December quarter? |
| ho-48 | DAL | How did Delta's transatlantic revenue perform? |
| ho-49 | DAL | What does Delta say about fuel price exposure in its quarterly results? |
| ho-50 | DAL | What does Delta say about its loyalty program or American Express remuneration? |

## Can't answer (held-out refusal set, cna-01 … cna-10)

No gold passage exists. A correct system refuses.

| id | ticker | question |
|---|---|---|
| cna-01 | AAPL | What will Apple's share price be at the end of next year? |
| cna-02 | NVDA | What did NVIDIA's CEO say about Blackwell demand on the earnings call? |
| cna-03 | MSFT | Who is Microsoft's largest individual retail shareholder? |
| cna-04 | JPM | What is JPMorgan's internal bonus pool for traders this year? |
| cna-05 | WMT | How many stores will Walmart open on Mars? |
| cna-06 | XOM | What oil price does ExxonMobil's CEO personally expect in 2030? |
| cna-07 | PFE | Which drug will Pfizer acquire next month? |
| cna-08 | CAT | What is Caterpillar's stock ticker on the Tokyo Stock Exchange? |
| cna-09 | NKE | What did Nike's quarterly report say about Apple's iPhone sales? |
| cna-10 | DAL | Should I buy Delta stock today? |
