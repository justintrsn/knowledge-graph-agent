# Ship Delay Operations Assistant

An assistant that answers questions about ship delays, such as *"Why is MV Ocean Star's current voyage delayed?"*, from a knowledge graph, and cites the evidence behind every claim.

## Quick start

Prerequisites:

- Docker with Compose 2.24 or later
- [uv](https://docs.astral.sh/uv/), for running tests and the notebook outside containers

```bash
uv sync                                          # install dependencies
uv run jupyter lab notebooks/data_profile.ipynb  # explore the raw data
docker compose up -d neo4j                       # start the graph database
uv run pytest                                    # run all tests
```

- **Tests without Neo4j:** the tests that need the database are skipped, not failed.
- **Graph tests wipe the graph.** They clear it and reload it from the CSVs.
- **The Neo4j browser** is at <http://localhost:7474> (user `neo4j`, password `localdevpassword`).
- **Settings:** `.env` is optional. Copy `.env.example` to set an API key or password.

## Status

| Part | State |
|---|---|
| Loading the tables into the graph, and its checks | Built |
| Data profile notebook | Built |
| Extracting facts from messages | Designed, not built |
| Question-answering agent and evaluation | Designed, not built |
| `kg` command line and one-command `docker compose up` | Not built |

The sections below describe the full design. Parts not built yet are described as planned.

## How it works

```text
 7 CSV tables ──── code ─────────────┐
                                     ├──► Neo4j graph ──► agent (Claude) ──► answer with
 60 messages ──── Claude Haiku ──────┘    (every node        read-only           cited evidence
                  extracts facts,          citable)          queries
                  code links them
```

1. **Load:** code turns every table row into a node, and links rows that refer to each other.
2. **Extract:** Claude Haiku reads each message and pulls out facts with exact quotes. Code decides what each fact is about and whether it is verified.
3. **Check:** load-time checks confirm the graph matches the files. Any failure stops the run.
4. **Answer:** an agent queries the graph (read-only) and answers, labelling each claim as confirmed, reported or inferred, with the evidence IDs behind it.

## Data

All input is in `data/`. The data is a snapshot taken **26 Oct 2026, 12:00 UTC**. That moment is "now" for every question, whatever today's date is.

- **Seven tables (CSV), 1,475 rows in total:** vessels (40), ports (15), voyages (121), port calls (231), incidents (90), daily port congestion (578) and bookings (400). Every column is described in [`data/DATA_README.md`](data/DATA_README.md).
- **60 messages:** emails, noon reports and crew messages written by people. 15 core messages are always used, and 45 extra ones are optional. They are deliberately messy: estimates, contradictions, corrections, vessels named only by abbreviation or IMO number, and at least one message that tries to give instructions to an AI.

[`notebooks/data_profile.ipynb`](notebooks/data_profile.ipynb) profiles every table so you can check the data yourself: distinct values, blanks and cross-table references. It found:

- **No duplicate IDs**, and no table points at a row that doesn't exist.
- **Blanks in only two places:** `attributed_delay_hours` on 38 incidents (the delay isn't known), and some port call times (the event hasn't happened yet, or doesn't apply). A blank is kept as "unknown", never turned into 0.
- **Booking statuses are free text:** `Confirmed` 331, `Rolled to next sailing` 38, `Cancelled` 30, and one `Rolled to VG-1107`. Only that last one says which voyage the cargo moved to.

## Knowledge graph

### Why a graph

The questions follow chains of links, and a graph stores those links directly. Each question becomes a walk along them, instead of a pile of table joins:

- *"Which customers are affected by this delay?"* goes from voyage → bookings → customers.
- *"Has this fault happened before on the same class of ship?"* goes from incident → voyage → vessel → other vessels of that class.

The second reason is **evidence**. Every node has:

- an `evidence_id`, such as `INC-201` or `VG-1042`
- a `source_ref` saying exactly where it came from, such as `incidents.csv:2`

An answer can only cite IDs that exist, so every claim traces back to a row or a message.

**Neo4j** was chosen because its community edition is free. It also gives a hard safety boundary: the agent's queries run in a read-only session that the database itself enforces.

### What's in it

Each table row becomes a node. Each column that points at another table becomes a link:

```text
Voyage   -OPERATED_BY-> Vessel           Incident           -ON_VOYAGE-> Voyage
Voyage   -FROM / TO->   Port             Booking            -ON_VOYAGE-> Voyage
PortCall -ON_VOYAGE->   Voyage           Customer           -HOLDS->     Booking
PortCall -AT_PORT->     Port             CongestionSnapshot -AT_PORT->   Port
Booking  -ROLLED_TO->   Voyage   (only when the status names the voyage)
```

The rule for what becomes a node is **something with its own identity that sources refer to**. Vessels, voyages and incidents are nodes. A vessel's class and technical manager are just properties of the vessel, because questions only compare them ("same class?").

### Design choices

- **Customers** have no table of their own, so a `Customer` node is made for each distinct name in the bookings.
- **Congestion links only to its port**, never to a vessel. It is a port-wide daily average, so it can't be mistaken for a fact about one ship.
- **Times are stored in UTC**, with the original local text kept beside them. This matters for VG-1042, whose ETA moved across the night Rotterdam's clocks went back. Subtracting the local times gives 54 hours, but the real delay is **55 hours**.
- **Every load starts from an empty graph** and rebuilds everything from the files. The graph can never drift from the data, and loading twice gives the same result.

## Messages and the LLM

### Who decides what

| Step | Done by |
|---|---|
| Turning table rows into nodes | Code |
| Reading a message and pulling out facts (a one-sentence statement plus an exact quote) | Claude Haiku |
| Deciding which vessel, voyage or port a fact is about | Code |
| Deciding whether a fact is verified | Code, using fixed rules |
| Checking each quote really appears in the message | Code. A fact whose quote doesn't appear is rejected. |

When code links a fact, it tries the most certain method first: IMO number, then voyage number, then the exact vessel name. Anything it can't match is recorded as unresolved, never guessed. Whole-name matching keeps "Ocean Star" from matching "Ocean Starlight".

Extraction results are saved in `cache/extractions/` and committed, so the graph rebuilds without an API key. A new message needs one extraction run with a key before it appears in the graph.

### Verification and statuses

Each incident in the table has a status:

| Status | Meaning |
|---|---|
| **Confirmed** | It happened. |
| **Reported** | Someone said it happened. Not verified. |
| **Under review** | Being checked. Not verified. |
| **Dismissed** | Checked and found not to have happened. Not counted. |

A fact from a message starts as **unverified**. It becomes confirmed only when an **independent** source reports the same thing and no figures disagree. "Independent" is strict:

- The master and the crew of one ship count as **one** source. Two people on the same ship repeating a story doesn't make it more certain.
- An incident row that came from that ship's crew or master counts as the same source too.
- A sender who can't be identified, such as a bare "Port agent", can never confirm anything.

### Corrections

When a source corrects its own earlier report, the correction wins, and the original stays visible beside it.

**Example:** INC-094 on MV Silver Tern is a confirmed "Engine cooling fault" from the same technical manager as Ocean Star. In the table it looks like an earlier case of Ocean Star's fault. A later message from that technical manager corrects the component: an auxiliary engine cooler, not the main engine pump. So an answer about Ocean Star's fault history excludes it, and says why. The table row is never edited. The correction is linked to it, so a reader can see both.

## Prompt-injection safety

Messages are untrusted text, and one of them contains instructions addressed to an AI. The defences are layered, so no single one has to be perfect:

1. **The reading AI has no tools.** Haiku can only return facts in a fixed format, so an injected "ignore your instructions and…" has nothing to act on.
2. **Suspicious text is flagged twice:** once by a pattern match in code (phrases like "ignore … instructions" or "AI assistant"), and once by Haiku itself. Either flag marks the message, and any fact quoted from the flagged text is dropped.
3. **Quotes must be exact.** Every fact quotes its message word for word, so the AI can't invent content.
4. **The answering agent can only read.** Its database session is read-only, enforced by Neo4j rather than by the AI's good behaviour. A blocklist (no `CREATE`, `DELETE`, `CALL` and so on), a time limit and a row limit sit on top.

## Checks and tests

After loading, [`graph/verify.py`](src/knowledge_graph_agent/graph/verify.py) checks the graph and returns a pass or fail for each named check. Any failure stops the run. It will run as `kg verify` once the command line is built. It checks that:

- there is one node per table row
- every node has an `evidence_id` and a `source_ref`
- there are no broken links
- each file's fingerprint (sha256) still matches what was loaded
- every recorded delay equals the real elapsed hours in UTC
- look-alike vessels stay separate

Message checks (exact quotes, all 15 core messages loaded) are reported as skipped until message extraction is built.

Tests:

- `tests/unit` needs nothing running.
- `tests/graph` needs Neo4j, and checks specific cases such as VG-1042's times and that `Sulamar Gestão Naval` keeps its accented letters.

## Repository layout

```text
data/                     input tables and messages (see DATA_README.md)
notebooks/                data profile
src/knowledge_graph_agent/
  config.py               settings from config.yaml and .env
  timeutil.py             local time to UTC
  graph/                  database connection, table loading, checks
  extract/                message extraction (planned)
  agent/                  question answering (planned)
  evaluate/               evaluation (planned)
tests/                    unit/ (no services) and graph/ (needs Neo4j)
config.yaml               snapshot time, models, limits
```

## Further reading

The full design is in `specs/001-delay-ops-assistant/`, mainly `data-model.md` and `research.md`. That folder is kept out of git, so this README summarizes it.
