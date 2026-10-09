# Stage 2: Excel cleaning

The transaction-bot writes everything it finds on a transaction into a staging sheet: every agent on the file, including agents from other brokerages, with values formatted the way the portal shows them. Before anything goes into the deal system, I clean that sheet in Excel. I did this stage by hand, without code, because: I knew how to do it quickly and effectively in excel and the dataset was always small enough to not have to transition to SQL. The agent data also came from a separate system that updated frequently so one of the excel tabs contained all that information that could easily be updated periodically. 

The steps, in order:

## 1. Keep only our brokerage's agents
The scraper deliberately collects every agent on a transaction, so filtering happens here instead of in code. I filter out agents from other brokerages. I did this by adding a filter in the office column then with a sort by function. I did not want this done automatically because this platform works across several different offices, include some agents private offices. Therefore it was easier to exclude the external offices manually rather than account for the various offices under our management. 

## 2. Separate broker numbers from names
Agent entries include a broker number alongside the name. I split them apart with **Text to Columns** so the name can be matched exactly in the deal system.

## 3. Look up each agent's office
A lookup table on a second sheet maps each agent to their office:

```
=XLOOKUP(F2, Sheet2!$B$2:$B$129, Sheet2!$C$2:$C$129)
```

As previously mentioned, the agent data came from a separate system run by the government and related to real estate agent official licence which both updated frequently as our offices expanded but also deviated slightly from records occasionally, often because of French to English translations and accents on certain letter. So one of the excel tabs contained all that information that could easily be updated periodically. 

## 4. Standardize titles
I split and correct the title field so every agent is either a listing (seller-side) advisor or a buyer-side advisor. The deal-bot uses this to put each agent in the right section of the deal form.

## 5. Convert numbers stored as text
The MLS number, sold price and commission (Rétribution) arrive as text. I convert them to real numbers with **Paste Special → Multiply** (by 1), so they can be summed and compared.

## 6. Postal code
The scraper leaves the postal code inside the province text. I split them apart with **Text to Columns** separating then columns every time there was a space. Unfortunately since all this information I am scraping is manually entered by agents there is variation in how, often for postal codes there was a space between the first 3 characters and the second 3, which created 3 columns, the province, the first 3 characters of the postal code, then the next 3, so I proceeded to merge those last two columns giving me a final separated postal code column. When working with hundreds of lines at once, this saved tremendous time.

## 7. Calculate each agent's commission share
For each agent, I calculate their share of the transaction's commission with an `IFERROR` and `SUMIFS` formula:

The deal-bot checks that these shares total 100% for each transaction and skips any transaction where they don't, so a mistake in this step can't reach the deal system.

## 8. Remove accents from a few names
A handful of agent names were spelled with accents in the data but without accents in the deal system's advisor list or vice versa. The deal-bot selects advisors by exact name, so I removed the accents from those names and added some back to others. I kept a comprehensive list.
