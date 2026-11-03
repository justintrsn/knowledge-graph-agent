# Evaluation report: run 20261103T181321Z-c7e84a

Run started 2026-11-03T18:13:21.460630+00:00, git commit `442b9c88ed40dcd3ac2231ff888538ff8da25943`.
Models: extraction claude-haiku-5-5, agent claude-sonnet-5-5, judge claude-sonnet-5-5.

**Overall: 1/2 (50%) questions passed.** A question passes only if every rubric item passes.

## Questions

| Question | Result | Items passed | Status | Cache-read tokens | Transcript |
|---|---|---|---|---|---|
| Q3 | **fail** | 6/7 | ok | 22977 | [Q3.md](Q3.md) |
| Q10 | pass | 4/4 | ok | 30636 | [Q10.md](Q10.md) |

## By failure type

| Failure type | Items passed |
|---|---|
| false_premise_accepted | 3/3 (100%) |
| citation_invalid | 2/2 (100%) |
| temporal_error | 1/1 (100%) |
| missing_data_masked | 3/3 (100%) |
| incomplete_coverage | 1/2 (50%) |
| run_failure | 2/2 (100%) |

`run_failure` counts questions whose run produced an answer.

## Failures

### Q3: Why was MV Blue Horizon delayed at Hamburg last month?

- **incomplete_coverage** `Q3.future_hamburg`: VG-1121 not cited in any
- Transcript: [Q3.md](Q3.md)

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
