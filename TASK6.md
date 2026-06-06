# TASK6.md — Extension Documentation

## Overview
This extension expands the TransitFlow AI assistant's policy knowledge base by adding
five new policy documents to the pgvector database. The original system included only
refund, ticket type, booking rules, and basic travel policies. This extension adds
coverage for five additional real-world passenger scenarios that the original assistant
could not answer.

---

## Files Modified or Added

| File | Type | What changed |
|---|---|---|
| `train-mock-data/lost_property_policy.json` | New file | Lost property reporting, storage, collection for metro and NR |
| `train-mock-data/accessibility_policy.json` | New file | Step-free access, wheelchair, assistance dogs, fare concessions |
| `train-mock-data/engineering_works_policy.json` | New file | Planned works, replacement services, passenger rights |
| `train-mock-data/penalty_fares_policy.json` | New file | Penalty amounts, appeals process, prosecution thresholds |
| `train-mock-data/delay_compensation_policy.json` | New file | Compensation tiers for metro and NR delays, how to claim |
| `skeleton/seed_vectors.py` | Modified | Added loading and embedding of all 5 new policy files |

---

## New Policy Documents

### lost_property_policy.json
- **category:** `conduct`
- **covers:** Reporting lost items, storage locations, retention periods, collection procedures, handling of valuables (cash, passports, electronics) for both metro and national rail

### accessibility_policy.json
- **category:** `conduct`
- **covers:** Step-free access at all stations, wheelchair boarding, mobility scooters, assistance dogs, priority seating, visual/hearing impairment support, fare concessions for disabled passengers and carers

### engineering_works_policy.json
- **category:** `conduct`
- **covers:** Planned works notice periods (72hr metro / 7 days NR), replacement bus services, ticket validity during works, refund options, unplanned disruption procedures, passenger rights

### penalty_fares_policy.json
- **category:** `conduct`
- **covers:** Penalty fare amounts ($50 metro / $100 NR), grounds for appeal, repeat offence escalation, prosecution thresholds, inspector identification rights

### delay_compensation_policy.json
- **category:** `refund`
- **covers:** Compensation tiers for metro (15/30/60+ min) and national rail (30/60/120+ min), how to claim, season ticket holder rules, stranded passenger reimbursement

---

## Functions Modified

### `skeleton/seed_vectors.py` — `build_documents()`
Added loading blocks for all 5 new JSON files. Each block follows the same pattern
as existing teacher-provided files. The delay compensation file is split into 3
smaller sections (metro, national_rail, engineering_works_and_disruption) to avoid
Ollama memory errors during embedding.

---

## How to Test the Extension

After seeding, run this in a Python shell:

```python
from skeleton.llm_provider import llm
from databases.relational.queries import query_policy_vector_search
from skeleton.config import VECTOR_SIMILARITY_THRESHOLD, VECTOR_TOP_K

# Test 1 — delay compensation
results = query_policy_vector_search(llm.embed("my train was 45 minutes late, can I get compensation?"))
print(results[0]["title"])
# Expected: "Delay Compensation — National Rail"

# Test 2 — lost property
results = query_policy_vector_search(llm.embed("I left my bag on the metro, what do I do?"))
print(results[0]["title"])
# Expected: "Lost Property Policy"

# Test 3 — penalty fare
results = query_policy_vector_search(llm.embed("I got a penalty fare but the gate was broken"))
print(results[0]["title"])
# Expected: "Penalty Fares and Fare Evasion Policy"

# Test 4 — accessibility
results = query_policy_vector_search(llm.embed("is there wheelchair access at Central Station?"))
print(results[0]["title"])
# Expected: "Accessibility and Assisted Travel Policy"

# Test 5 — engineering works
results = query_policy_vector_search(llm.embed("my train is cancelled due to engineering works, can I get a refund?"))
print(results[0]["title"])
# Expected: "Engineering Works and Planned Disruption Policy"
```
