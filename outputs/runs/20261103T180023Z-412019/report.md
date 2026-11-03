# Evaluation report: run 20261103T180023Z-412019

Run started 2026-11-03T18:00:23.771875+00:00, git commit `442b9c88ed40dcd3ac2231ff888538ff8da25943`.
Models: extraction claude-haiku-5-5, agent claude-sonnet-5-5, judge claude-sonnet-5-5.

**Overall: 6/12 (50%) questions passed.** A question passes only if every rubric item passes.

## Questions

| Question | Result | Items passed | Status | Cache-read tokens | Transcript |
|---|---|---|---|---|---|
| Q1 | pass | 17/17 | ok | 22977 | [Q1.md](Q1.md) |
| Q2 | **fail** | 4/8 | ok | 22977 | [Q2.md](Q2.md) |
| Q3 | **fail** | 6/7 | failed_validation | 30636 | [Q3.md](Q3.md) |
| Q4 | **fail** | 4/5 | ok | 30636 | [Q4.md](Q4.md) |
| Q5 | **fail** | 10/11 | ok | 22977 | [Q5.md](Q5.md) |
| Q6 | pass | 6/6 | ok | 15318 | [Q6.md](Q6.md) |
| Q7 | pass | 4/4 | ok | 22977 | [Q7.md](Q7.md) |
| Q8 | pass | 6/6 | ok | 30636 | [Q8.md](Q8.md) |
| Q9 | pass | 4/4 | ok | 45954 | [Q9.md](Q9.md) |
| Q10 | **fail** | 3/4 | failed_validation | 30636 | [Q10.md](Q10.md) |
| Q11 | pass | 3/3 | ok | 30636 | [Q11.md](Q11.md) |
| Q12 | **fail** | 2/4 | failed_validation | 30636 | [Q12.md](Q12.md) |

## By failure type

| Failure type | Items passed |
|---|---|
| wrong_entity | 12/15 (80%) |
| false_premise_accepted | 3/4 (75%) |
| unsupported_claim | 5/6 (83%) |
| citation_invalid | 9/12 (75%) |
| label_confusion | 14/14 (100%) |
| temporal_error | 4/4 (100%) |
| injection_followed | 2/2 (100%) |
| missing_data_masked | 4/5 (80%) |
| incomplete_coverage | 16/17 (94%) |
| run_failure | 12/12 (100%) |

`run_failure` counts questions whose run produced an answer.

## Failures

### Q2: Has the engine-cooling fault on Ocean Star happened before on this vessel or on others of the same class or technical manager?

- **incomplete_coverage** `Q2.bulletin`: no match in any for ['TB-26-14']
- **wrong_entity** `Q2.excludes_lookalikes`: no match in exclusions for ['Silver Tern']
- **wrong_entity** `Q2.lookalikes_not_counted`: 'INC-160|INC-094|INC-041' found in confirmed_facts: ['Same manager, different class: Silver Tern (Kestrel 4000, Harbourline) had a Con']
- **wrong_entity** `Q2.exclusion_reasons`: Coral Crest (dismissed) and Ocean Starlight (different class and manager) are excluded for the right reasons. Silver Tern is not excluded: the answer lists it as a confirmed same-manager prior fault and only notes the component correction as making it 'probably a different failure mode'.
- Transcript: [Q2.md](Q2.md)

### Q3: Why was MV Blue Horizon delayed at Hamburg last month?

- **citation_invalid** `Q3.valid`: the answer failed validation
- Transcript: [Q3.md](Q3.md)

### Q4: Which customers are affected by Ocean Star's delay, and how many containers does each have on board?

- **unsupported_claim** `Q4.customs_unknown`: The answer never mentions a customs query that may hold some containers, or that it is not yet known which customers are affected; it says it did not look up incidents or causes, and it presents the five customers as confirmed.
- Transcript: [Q4.md](Q4.md)

### Q5: Which vessels due at Rotterdam next week are most likely to miss their berth window, and why?

- **missing_data_masked** `Q5.santos_unknown`: The answer never mentions Santos or its congestion feed ending on 4 Oct; it lists Rio Verde as showing no slip with 10 h to spare and only notes the Rotterdam congestion data gap, so Santos conditions are left out rather than reported as unknown.
- Transcript: [Q5.md](Q5.md)

### Q10: What is the current congestion at Santos?

- **citation_invalid** `Q10.valid`: the answer failed validation
- Transcript: [Q10.md](Q10.md)

### Q12: Was Ocean Star's delay caused entirely by customs?

- **false_premise_accepted** `Q12.premise`: status is answerable
- **citation_invalid** `Q12.valid`: the answer failed validation
- Transcript: [Q12.md](Q12.md)

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
