# Opportunity Scoring Model

> **Implementation:** `core/gads_opportunity.py` — Flask app, not Streamlit.

This document explains how the Sales Opportunity Score is calculated, the rationale behind each component, and the design decisions we made along the way. Keep it updated when the model changes.

---

## The Formula

```
Opportunity Score = Clicks Monthly
                  × log(Unique Product Groups)
                  × Stock Multiplier
                  × Search Volume Multiplier
                  × Value Multiplier
```

Each component answers a distinct commercial question. Together they produce a raw score that is then normalised to a 0–10 priority scale (see Normalisation below).

---

## Components

### 1. Clicks Monthly
**Signal:** Observed demand
**Question:** Is there real traffic behind this keyword right now?

The base of the score. Sourced from the product feed's 28-day click data, scaled to a monthly estimate. This is what actually happened — not a prediction — so it anchors the entire score in reality rather than potential alone.

---

### 2. log(Unique Product Groups)
**Signal:** Catalogue depth
**Question:** Do we have meaningful breadth of product to serve this demand?

We use the natural log so that going from 1 to 10 products counts for more than going from 100 to 1,000. A keyword backed by a single product is fragile; one backed by dozens of product groups is a genuine category. The log dampens the advantage of very large catalogues — having 6,000 products isn't proportionally better than having 600.

---

### 3. Stock Multiplier
**Signal:** Supply viability
**Question:** Can we actually ship when someone clicks?

**Implementation:** Continuous log curve on *average units per product* (Total Quantity ÷ Product Count), capped at 1.0. Anchor = 50 units/product (the point at which a keyword is considered fully stocked).

**Why average units, not total quantity?**
Total Quantity scales with the number of products, not stock depth. A keyword with 10 products × 5 units each (50 total) is not better stocked than one with 2 products × 25 units each (50 total) — but the former has the same raw total. Dividing by Product Count normalises this so the multiplier reflects whether individual products are actually available to ship.

**Why a continuous curve, not bands?**
The previous approach used three discrete bands (`< 500 = 0.5`, `500–2,000 = 0.75`, `> 2,000 = 1.0`). Against this retailer's data, 70% of keywords fell into the bottom band because the median keyword has ~20 avg units/product — well below thresholds calibrated for raw totals. The continuous curve means a keyword improves smoothly as stock grows, rather than jumping in step-function increments at arbitrary thresholds.

| Avg units/product | Stock Multiplier |
|---|---|
| 1 | 0.14 |
| 5 | 0.47 |
| 10 | 0.61 |
| 20 | 0.76 |
| 35 | 0.89 |
| 50 (anchor) | 1.00 |
| 100+ | 1.00 |

---

### 4. Search Volume Multiplier
**Signal:** Demand confidence
**Question:** How well-evidenced is the search demand? Is the click traffic scalable?

**Implementation:** `max(1 − e^(−volume / 2000), 0.1)` — an exponential saturation curve with a floor of 0.1.

The multiplier rewards keywords where Google Ads confirms meaningful search volume, indicating that clicks can scale if bids increase. The curve saturates quickly: at 2,000 searches/month a keyword scores ~0.63; at 5,000 it's ~0.92.

**Why a floor of 0.1 (not 0)?**
Long-tail and colour/material-specific phrases (e.g. "beige wool jackets", "womens blue hats") often return zero search volume from the Google Ads API — not because they have no demand, but because Google doesn't report volume at that level of specificity. These keywords can still drive hundreds of clicks a month. Setting the floor to 0.1 means they receive reduced but non-zero weight, rather than being silently excluded from the ranking entirely.

---

### 5. Value Multiplier
**Signal:** Product value
**Question:** Are the products behind this keyword worth the ad spend?

**Implementation:** Continuous log curve on *Sales per Product Group* (Total Sales Value ÷ Unique Product Groups), capped at 1.0. Anchor = £5,000 per product group.

**Why Sales per Product Group, not Total Sales Value?**
Raw Total Sales Value rewards breadth. "Womens dresses" at £24.9M looks like the highest-priority keyword, but that revenue is spread across 6,369 product groups — each generating ~£3,920 on average. Meanwhile "cashmere coats" generates £118k total but only across 8 groups — £14,775 per group — making it a higher-value category per product line. Normalising by product group count removes the size advantage of broad terms and correctly surfaces high-value niche categories.

This component also soft-penalises genuinely low-ticket keywords. Gloves at £29 per product group receive a ~0.40 multiplier; the priority score reflects that, even if they have decent click volume.

**This is a soft multiplier, not a hard filter.** Low-value keywords are discounted, not excluded — they remain visible in the output for analysts to judge contextually.

| Sales per Product Group | Value Multiplier |
|---|---|
| £29 | 0.40 |
| £312 | 0.67 |
| £851 | 0.79 |
| £2,107 | 0.90 |
| £5,000 (anchor) | 1.00 |
| £14,775+ | 1.00 |

---

## Priority Score (0–10)

The raw Opportunity Score spans several orders of magnitude (the distribution is a power law — a handful of broad category terms vastly outscale the long tail). A linear rescale would compress 90% of keywords into the bottom 2 points.

**Approach:** Log10 normalisation with a cap at the 95th percentile of raw scores for that run, rounded to one decimal place. This is the column analysts should sort and filter by.

```
cap = p95 of non-zero Opportunity Scores in this export
Priority Score = round( min( 10 × log10(raw + 1) / log10(cap + 1), 10.0 ), 1 )
```

One decimal place is intentional: it makes it easy to distinguish an 8.1 from a 5.3 without implying false precision.

**Why cap at p95, not the maximum?**
The top 1% of keywords (broad gender+category terms like "womens dresses") score so far above the rest that anchoring to the max compresses everything useful into the bottom half of the scale. Capping at p95 gives meaningful spread across the middle of the distribution — which is where most prioritisation decisions actually happen.

**The raw `Opportunity Score` column is preserved** for auditing and cross-run comparison. `Priority Score` is relative to a single export run.

---

## Configuration Constants

All tunable values are defined at the top of `core/gads_opportunity.py`:

| Constant | Default | What it controls |
|---|---|---|
| `_STOCK_ANCHOR` | 50 | Avg units/product considered "fully stocked" |
| `_SV_DIVISOR` | 2,000 | Steepness of search volume saturation curve |
| `_SV_FLOOR` | 0.1 | Minimum SV weight for zero-volume keywords |
| `_VALUE_ANCHOR` | 5,000 | Sales/group at which full value weight is earned |

These were calibrated against a fashion retailer feed (~1,300 keywords, median avg qty ~20 units/product, median sales/group ~£2,100). They may need adjusting for different verticals or catalogue sizes.

---

## Known Limitations

- **Total Quantity** is sourced from the Google Merchant Center feed and is assumed to represent shippable stock. If the feed includes reserved, pre-order, or in-transit units, the Stock Multiplier will be optimistic.
- **Sales data** reflects historical performance in the period covered by the feed. New products or recently restocked lines will be underrepresented.
- **Search Volume** from the Google Ads API is rounded and suppressed for very low-volume queries. The 0.1 floor is a pragmatic workaround, not a true volume estimate.
- The model scores *relative opportunity within a given feed run*. Scores are not comparable across different retailers or feed dates without recalibration.
