# Write-up: Ship Delay Operations Assistant

## Design

**Pipeline.** Code loads the 7 tables into Neo4j. Claude Haiku extracts facts from the 15 core messages, and code links them to the graph. Load-time checks must all pass before anything is answered. Then Claude Sonnet answers by calling read-only tools, and code validates every citation in the answer before it is trusted.

**Schema: node or property?** A node is something with its own identity that sources refer to: vessels, voyages, port calls, incidents, bookings, customers, congestion snapshots, messages and the facts they state. A vessel's class and technical manager stay as properties, because questions only compare them ("same class?"). Every node has a stable, readable `evidence_id` taken from the data (`VG-1042`, `INC-201`, `MSG:noon_2026-10-06_ocean_star#2`) and a `source_ref` (`incidents.csv:2`). It also carries a shared `Evidence` label, so one constraint keeps IDs unique and one query finds any cited ID. Congestion links only to its port, never to a vessel, so a port-wide average can't pass as a fact about a ship.

**Time.** Times are stored as UTC `datetime` values, with the original local text kept beside them. Delays are elapsed UTC hours: VG-1042's ETA moved across Rotterdam's clock change, so subtracting the local times gives 54 h, but the real delay is 55 h. "Now" is the snapshot time (26 Oct 2026 12:00 UTC), fixed in config. The system prompt says how to read "last month", "next week" and "current", and every answer states how it read them. Dates at a port are compared as port-local dates. Message times keep their precision (exact, day, vague).

**Messages.** The LLM only reads and quotes. Code does everything that decides truth:

- **Quotes:** each fact must quote its message exactly, or it is rejected.
- **Linking:** code matches names to vessels, most certain method first: IMO, then voyage number, then the exact name or a known variant. A name it can't match is recorded as unresolved, never guessed.
- **Verification:** a fact is confirmed only by an independent source. A ship's master and crew count as one source.
- **Corrections and conflicts:** a correction links to the row it corrects, and the row is never edited (Silver Tern's INC-094). Conflicting figures are linked to each other (the 8 h and 20 h weather estimates).

**Answers.** An answer is a structured object with fixed sections: confirmed facts, unverified reports and inferences are separate lists, and each claim lists its evidence IDs and the values it cites. Ten validator checks then run on it. Every cited ID must exist and have been returned by a tool in this run. Every cited value and quote must match its source. Nothing unverified may sit under "Confirmed", and nothing from a suspicious message may be repeated as fact.

**Injection.** The defences are layered, so no single one has to be perfect:

- The extracting model has no tools.
- Suspicious text is flagged both by a regex and by the model, and facts quoted from it are dropped.
- Message content reaches the agent marked untrusted.
- Every query, including the Cypher Claude writes itself, runs in a read-only session that Neo4j enforces. A keyword and path-length guard, a timeout and a row limit sit on top.

## Trade-offs

- **Fixed tools first, written Cypher as a last resort.** Typed tools return the evidence fields and the derived values (elapsed hours, feed gaps) that validation relies on, and they keep answers repeatable. The cost is coverage: questions 2 and 4 had no tool, and the agent fell back on its own Cypher.
- **Code-validated answers.** A strict answer schema plus a validator catches citation errors that a prompt alone can't prevent. Making the tool schemas strict as well hit the API's grammar-size limit, so tool inputs are checked in code instead.
- **Rebuild from scratch on every load.** The graph can never drift from the files, and the data is small enough for this to take seconds. It wouldn't scale to large data as is.
- **Committed extraction cache and runs.** Anyone can rebuild the graph and read every transcript with no API key. The cost is a larger repository.
- **Scoring with code where possible.** An LLM judge is used only for items a pattern can't check. It costs money and is less repeatable, so its verdicts are saved and a rescore reuses them.
- **Neo4j rather than an in-memory library.** Its read-only sessions give a database-enforced safety boundary for written queries. The cost is a second container.

## Evaluation and known failures

There are 12 questions, each with an expected answer and a rubric of 79 items in total. Each item carries a failure type. A question passes only if every item passes, and each question is asked once, with no retries until it passes. The delivered run, `20261103T224913Z-6e5df1`, scores **10/12**:

- **Q2, fault history: fails 6 of 8 items.** With no fault-history tool, the agent:
  - counts Silver Tern's INC-094 as a prior case, although the vessel's technical manager corrected the component in a later message
  - doesn't list the lookalike vessels as exclusions
  - never finds the class bulletin TB-26-14
  - misquotes a bulletin line, which the validator catches
- **Q4, affected customers: fails 1 of 5 items.** The agent wrote its own Cypher and got every booking total right. It didn't say that the customs query might hold some containers.
- **Variance.** A rerun can differ. In one partial run, Q3 searched only Sep–Oct and missed Blue Horizon's future Hamburg voyage; the other run found it. Each question is asked once, so the score doesn't measure this.

**A failure type that led to a fix.** In the first full run, `20261103T180023Z-412019` (6/12), three answers failed `citation_invalid` (Q3, Q10 and Q12). The breakdown showed the answers were right, and the validator was wrong. It only accepted values stored on graph nodes, so it rejected values a tool had worked out: a feed's last date, the dates missing, a linked message's suspicious quote. When a tool was called twice, it also kept only the second call's values. The general fix was to record every value each tool returns, keyed by evidence ID and field, and to accept a cited value that matches any of them. Offline re-validation of all 12 saved answers then passed. In the delivered run, Q3, Q10 and Q12 pass, and `citation_invalid` went from 9/12 to 11/12. The one failure left is a real misquote. Earlier, single-question runs had also led to two general prompt rules: an inference such as "about 35 h at anchor" must not sit under Confirmed, and a class-wide notice applies to every vessel in that class.

Two rubric items were loosened after review, and both are recorded in each run's saved `questions.yaml`. `Q3.antwerp` now also accepts "0 h delay" as well as the berthing date. `Q12.premise` accepts `answerable` as well as `false_premise`, because "was it entirely customs?" can fairly be answered with "no".

## Next steps

1. **A fault-history tool (User Story 4):** filters by class, manager and incident type, with corrections and dismissed incidents returned. This should fix Q2.
2. **A bookings tool (User Story 5):** per-customer totals computed in Cypher, with rolled and cancelled bookings listed separately. This makes Q4 repeatable and lets it mention the customs hold.
3. **Origin-port checks in `arrivals_at_port`:** flag a stale origin feed for a vessel that hasn't sailed yet (the Santos case), instead of relying on the prompt.
4. **The 45 extra messages:** run `kg extract --include-extra` (name matching and checks are ready), then re-ask Q5 and Q10.
5. **Variance:** ask each question several times and report a pass rate, not a single result.
6. **Fuller entity resolution:** companies and technical managers, with confidence levels.

## How AI tools were used

- **Claude Code (Claude Opus)** was used throughout:
  - turning the brief into a specification, plan and task list with Spec Kit
  - writing the code, tests and documentation
  - diagnosing failures from saved transcripts

  I directed it task by task. I reviewed and ran each change, made the design decisions (what counts as confirmed, the evidence ID scheme, the cost limits), checked answers against the data, and decided which rubric changes were fair.
- **Claude Haiku** extracts facts from messages; the results are cached.
- **Claude Sonnet** is the answering agent and the evaluation judge.

## Hours spent

TODO: hours.
