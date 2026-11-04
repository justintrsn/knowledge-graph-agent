# Write-up: Ship Delay Operations Assistant

## Design

**Pipeline.** Code loads the 7 tables into Neo4j; Claude Haiku extracts facts from the 15 core messages and code links them to the graph. Load-time checks must pass before anything is answered. Claude Sonnet then answers through read-only tools, and code validates every citation.

**Why a graph.** The questions follow chains of links, and a graph stores them directly, so each question is a walk rather than a pile of joins: affected customers are voyage → bookings → customers, and fault history is incident → voyage → vessel → other vessels of that class. The second reason is evidence: every node carries a citable ID and its source, so every claim traces back to a row or a message. Neo4j Community is free, and its read-only sessions give a database-enforced boundary for the agent's queries.

**Schema: node or property?** A node is something with its own identity that sources refer to: vessels, voyages, port calls, incidents, bookings, customers (derived from booking names, as there is no customer table), congestion snapshots, messages and the facts they state. A vessel's class and technical manager stay as properties, because questions only compare them ("same class?"). Every node has a stable, readable `evidence_id` taken from the data (`VG-1042`, `INC-201`, `MSG:noon_2026-10-06_ocean_star#2`) and a `source_ref` (`incidents.csv:2`); the README lists the full scheme. Congestion links only to its port, never to a vessel, so a port-wide average can't pass as a fact about a ship.

**Time.** Times are stored as UTC `datetime` values, with the original local text kept beside them. Delays are elapsed UTC hours: VG-1042's ETA moved across Rotterdam's clock change, so subtracting the local times gives 54 h, but the real delay is 55 h. "Now" is the snapshot time (26 Oct 2026 12:00 UTC), fixed in config. The system prompt says how to read "last month", "next week" and "current", and every answer states how it read them. Dates at a port are compared as port-local dates. Message times keep their precision (exact, day, vague).

**Messages.** The LLM only reads and quotes; code decides truth. Each fact must quote its message exactly, or it is rejected. Names are matched to vessels most certain method first (IMO, voyage number, then exact name or known variant), and an unmatched name is recorded as unresolved, never guessed. A fact is confirmed only by an independent source: a ship's master, its crew and incident rows they filed count as one, and an unidentified sender can never confirm. A correction links to the row it corrects without editing it (Silver Tern's INC-094), and conflicting figures are linked to each other (the 8 h and 20 h weather estimates).

**Answers.** An answer is a structured object with confirmed facts, unverified reports and inferences in separate lists, each claim citing evidence IDs and values. Ten validator checks then run: every cited ID must exist and have been returned by a tool in this run, every cited value and quote must match its source, nothing unverified may sit under "Confirmed", and nothing from a suspicious message may be repeated as fact.

**Injection.** The defences are layered, so no single one has to be perfect. The extracting model has no tools. Suspicious text is flagged by both a regex and the model, and facts quoted from it are dropped. Message content reaches the agent marked untrusted. Every query, including Cypher the agent writes, runs in a read-only session that Neo4j enforces, with a keyword and path-length guard, a timeout and a row limit on top.

## Trade-offs

- **Fixed tools first, written Cypher as a last resort.** The first reason is security: a tool runs a query I wrote, with inputs checked in code, so injected text or a misled model has far less to steer. Tools also return the evidence fields and derived values (elapsed hours, feed gaps) that validation relies on, and keep answers repeatable. But tools can't foresee every question, so the agent may write read-only Cypher as a last resort (questions 2 and 4 needed it). The balance depends on the schema: here it is about a dozen node types with simple relations, so a tool per recurring question is cheap and easy to reason about. My rule is reuse: a query asked many times earns a tool; a one-off is better written from the schema. With a larger schema, a tool per question would not scale, and I would shift towards guarded free-written queries over a documented schema.
- **Code-validated answers.** A strict answer schema plus a validator catches citation errors that a prompt alone can't prevent. Making the tool schemas strict as well hit the API's grammar-size limit, so tool inputs are checked in code instead.
- **Rebuild from scratch on every load.** The graph can never drift from the files and takes seconds at this size, but wouldn't scale as is.
- **Committed extraction cache and runs.** Anyone can rebuild the graph and read every transcript with no API key, at the cost of a larger repository.
- **Scoring with code where possible.** The LLM judge is used only for items a pattern can't check; its verdicts are saved and reused on rescore.

## Evaluation and known failures

There are 12 questions, each with an expected answer and a rubric of 79 items in total. Each item carries a failure type. A question passes only if every item passes, and each question is asked once, with no retries until it passes. The delivered run, `20261103T224913Z-6e5df1`, scores **10/12**:

- **Q2, fault history: fails 6 of 8 items.** `incidents` has no class or manager filter, so the agent filled the gap with its own Cypher. It counts Silver Tern's INC-094 as a prior case, although a later message corrected the component; it doesn't list the lookalike vessels as exclusions; it cites Harbourline's bulletin without naming it TB-26-14, and misquotes a line from it (caught by the validator); and it doesn't cite Ocean Pearl's October report as unverified.
- **Q4, affected customers: fails 1 of 5 items.** Its own Cypher got every booking total right, but it didn't say the customs query might hold some containers.
- **Variance.** Each question is asked once, so the score doesn't capture run-to-run differences: in one partial run Q3 missed Blue Horizon's future Hamburg voyage, which another run found.

**A failure type that led to a fix.** In the first full run, `20261103T180023Z-412019` (6/12), Q3, Q10 and Q12 failed `citation_invalid`. The answers were right; the validator, accepting only values stored on graph nodes, rejected values a tool had computed (a feed's last date, missing dates, a linked message's quote). The general fix records every value each tool returns, keyed by evidence ID and field, and accepts a cited value matching any of them. In the delivered run Q3, Q10 and Q12 pass, and `citation_invalid` rose from 9/12 to 11/12; the one failure left is a real misquote.

Two rubric items (`Q3.antwerp`, `Q12.premise`) were loosened after review to accept other fair answers; each run's saved `questions.yaml` records the change.

## Next steps

1. **A fault-history tool (User Story 4):** class, manager and incident-type filters, returning corrections and dismissed incidents. This should fix Q2.
2. **A bookings tool (User Story 5):** per-customer totals in Cypher, with rolled and cancelled bookings listed apart, so Q4 is repeatable and mentions the customs hold.
3. **The 45 extra messages:** run `kg extract --include-extra` (matching and checks are ready), then re-ask Q5 and Q10.
4. **Variance:** ask each question several times and report a pass rate.

## How AI tools were used

**Claude Code (Claude Opus)** turned the brief into a spec, plan and tasks with Spec Kit, wrote the code, tests and docs, and diagnosed failures from saved transcripts. I directed it task by task, reviewed and ran each change, made the design decisions (what counts as confirmed, the evidence ID scheme, cost limits), checked answers against the data and decided which rubric changes were fair. In the product, **Claude Haiku** extracts message facts (cached), and **Claude Sonnet** answers questions and judges the evaluation.

## Hours spent

About 48 hours:

| Phase | When | Hours |
|---|---|---|
| Domain and data: berth windows and their knock-on effects, duplicates, statistics, what each message means | 31 Oct | ~8 |
| Design and spec: name matching (Ocean Pearl vs. Ocean Starlight), whether an LLM could just read the small CSVs, how far to plan for scale without over-engineering | 31 Oct – 2 Nov | ~16 |
| Build: graph load, extraction, agent, tools | 2–3 Nov | ~12 |
| Evaluation runs and fixes | 3–4 Nov | ~8 |
| README and write-up | 4 Nov | ~4 |

Finding the prompt injection during the data review shaped the design: fixed tools as boundaries, messages tagged by verification status, rules for which source supersedes which, and message content treated as information for the user to weigh, never as instructions. The agent is told not to take messages at face value, and code enforces it.
