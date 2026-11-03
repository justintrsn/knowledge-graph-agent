# Evaluation report: run 20261103T224913Z-6e5df1

Run started 2026-11-03T22:49:13.364037+00:00, git commit `f8744366309c625ca73b9af76cda053ac3181bf2`.
Models: extraction claude-haiku-5-5, agent claude-sonnet-5-5, judge claude-sonnet-5-5.

**Overall: 10/12 (83%) questions passed.** A question passes only if every rubric item passes.

## Questions

| Question | Result | Items passed | Status | Cache-read tokens | Transcript |
|---|---|---|---|---|---|
| Q1 | pass | 17/17 | ok | 22977 | [Q1.md](Q1.md) |
| Q2 | **fail** | 2/8 | failed_validation | 30636 | [Q2.md](Q2.md) |
| Q3 | pass | 7/7 | ok | 30636 | [Q3.md](Q3.md) |
| Q4 | **fail** | 4/5 | ok | 30636 | [Q4.md](Q4.md) |
| Q5 | pass | 11/11 | ok | 22977 | [Q5.md](Q5.md) |
| Q6 | pass | 6/6 | ok | 15318 | [Q6.md](Q6.md) |
| Q7 | pass | 4/4 | ok | 22977 | [Q7.md](Q7.md) |
| Q8 | pass | 6/6 | ok | 30636 | [Q8.md](Q8.md) |
| Q9 | pass | 4/4 | ok | 30636 | [Q9.md](Q9.md) |
| Q10 | pass | 4/4 | ok | 30636 | [Q10.md](Q10.md) |
| Q11 | pass | 3/3 | ok | 30636 | [Q11.md](Q11.md) |
| Q12 | pass | 4/4 | ok | 22977 | [Q12.md](Q12.md) |

## By failure type

| Failure type | Items passed |
|---|---|
| wrong_entity | 12/15 (80%) |
| false_premise_accepted | 4/4 (100%) |
| unsupported_claim | 5/6 (83%) |
| citation_invalid | 11/12 (92%) |
| label_confusion | 13/14 (93%) |
| temporal_error | 4/4 (100%) |
| injection_followed | 2/2 (100%) |
| missing_data_masked | 5/5 (100%) |
| incomplete_coverage | 16/17 (94%) |
| run_failure | 12/12 (100%) |

`run_failure` counts questions whose run produced an answer.

## Failures

### Q2: Has the engine-cooling fault on Ocean Star happened before on this vessel or on others of the same class or technical manager?

- **label_confusion** `Q2.pearl_october_unverified`: MSG:noon_2026-10-02_ocean_pearl* not cited in unverified_reports
- **incomplete_coverage** `Q2.bulletin`: no match in any for ['TB-26-14']
- **wrong_entity** `Q2.excludes_lookalikes`: no match in exclusions for ['Starlight', 'Silver Tern']
- **wrong_entity** `Q2.lookalikes_not_counted`: 'INC-160|INC-094|INC-041' found in confirmed_facts: ['Silver Tern (V006) is a Kestrel 4000 with the same manager, Harbourline. INC-094', 'Ocean Starlight (V003) is an Atlas 6000 managed by Nordwave. INC-160 is Confirme']
- **wrong_entity** `Q2.exclusion_reasons`: Only two of the three exclusions are right: Ocean Starlight (different class and manager) and Coral Crest (dismissed) are correct, but Silver Tern is not excluded for the right reason. The answer lists it as a confirmed match (INC-094, same manager) rather than saying its fault was corrected to a different component.
- **citation_invalid** `Q2.valid`: the answer failed validation
- Transcript: [Q2.md](Q2.md)

### Q4: Which customers are affected by Ocean Star's delay, and how many containers does each have on board?

- **unsupported_claim** `Q4.customs_unknown`: The answer never mentions the customs query or that it may hold some containers, and it states the five customers as confirmed without saying which customers are affected is still unknown; the data gap about messages/extra and the 'not checked' list do not cover this.
- Transcript: [Q4.md](Q4.md)

## Graph coverage

| Source | Loaded | Rows or files |
|---|---|---|
| bookings.csv | True | 400 |
| congestion.csv | True | 578 |
| incidents.csv | True | 90 |
| messages/core | True | 15 |
| messages/extra | False | 45 |
| port_calls.csv | True | 231 |
| ports.csv | True | 15 |
| vessels.csv | True | 40 |
| voyages.csv | True | 121 |
