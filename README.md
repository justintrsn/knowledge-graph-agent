# Ship Delay Operations Assistant

An assistant that answers questions about ship delays, such as *"Why is MV Ocean Star's current voyage delayed?"*, from a knowledge graph, and cites the evidence behind every claim.

## Quick start

Prerequisites:

- Docker with Compose 2.24 or later
- [uv](https://docs.astral.sh/uv/), for running tests and the notebook outside containers

```bash
uv sync                      # install dependencies
docker compose up -d neo4j   # start the graph database
uv run kg load               # build the graph from the CSVs and cached message facts
uv run kg verify             # check the graph
```

Then open the Neo4j browser at <http://localhost:7474> (user `neo4j`, password `localdevpassword`) to look at the graph. No API key is needed: the message extractions are already cached in `cache/extractions/`.

## Commands

| Command | What it does | Needs |
|---|---|---|
| `uv run kg load` | Clears the graph, loads the 7 tables, then the message facts from the cache. Prints what it loaded. | Neo4j |
| `uv run kg verify` | Runs the load-time checks and prints pass or fail for each. | Neo4j |
| `uv run kg extract` | Sends messages with no valid cached extraction to Claude Haiku and caches the result. Does nothing if the cache is complete. | API key, for any message not cached |
| `uv run kg extract --refresh` | Redoes every message, even if cached. | API key |
| `uv run kg extract --include-extra` | Also processes the 45 messages in `messages/extra`. | API key |
| `uv run pytest` | Runs all tests. Tests that need Neo4j are skipped if it isn't running. **Graph tests wipe and reload the graph.** | Neo4j, for `tests/graph` |
| `uv run jupyter lab notebooks/data_profile.ipynb` | Opens the data profile. | Nothing |

`kg` exit codes: `0` success, `1` a check failed or an extraction is missing with no key, `2` a database or Claude API failure.

`kg load` never calls Claude: it only reads the cache. `kg extract` can, but not while `use_cache: true` is set under `messages` in `config.yaml` (the default). With it, no command calls Claude for extraction, and a missing cache entry is reported instead. Set it to `false` only when you want to pay for a new extraction.

Settings are in `config.yaml` (snapshot time, models, which message collections to load, `use_cache`). `.env` is optional: copy `.env.example` to set `ANTHROPIC_API_KEY` or a different Neo4j password.

### Seeing the graph

Some queries to paste into the Neo4j browser:

```cypher
// How many of each node type
MATCH (n) RETURN labels(n)[1] AS type, count(*) ORDER BY type;

// One voyage and everything linked to it
MATCH (v:Voyage {evidence_id: 'VG-1042'})-[r]-(x) RETURN v, r, x;

// Each message, the facts it states, and what they are about
MATCH (m:Message)-[:STATES]->(f:Fact)-[r]->(x) RETURN m, f, r, x;

// The correction to INC-094, with the original row beside it
MATCH (f:Fact)-[c:CORRECTS]->(i:Incident {evidence_id: 'INC-094'})
RETURN f.statement, c.supersedes, c.rule, i.type, i.status;

// Facts that disagree with each other
MATCH (a:Fact)-[c:CONFLICTS_WITH]->(b:Fact) RETURN a.statement, b.statement, c.values;
```

## Status

| Part | State |
|---|---|
| Loading the tables into the graph, and its checks | Built |
| Extracting facts from messages, and loading them | Built (15 core messages, 62 facts) |
| `kg load`, `kg extract`, `kg verify` | Built |
| Data profile notebook | Built |
| Question-answering agent and evaluation | Designed, not built |
| `kg ask`, `kg run`, and one-command `docker compose up` | Not built |

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
v
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

From the messages, each message becomes a `Message` node, and each fact extracted from it becomes a `Fact` node. Facts link to the table nodes they are about:

```text
Message -STATES->         Fact
Fact    -ABOUT_VESSEL->   Vessel      (how it was matched: IMO, voyage number, name, ...)
Fact    -ABOUT_VOYAGE->   Voyage
Fact    -ABOUT_PORT->     Port
Fact    -REPORTS->        Incident    (same voyage and same incident type)
Fact    -CORRECTS->       Incident    (supersedes: true when the incident's own source corrects it)
Fact    -CONFLICTS_WITH-> Fact        (both report the same incident, with different hours)
```

What the main nodes hold:

| Node | Holds |
|---|---|
| `Message` | `from_raw`, `subject`, `body`, `sent_utc`, `sender_role` (master, crew, agent, …), `origin` (which source it counts as), `suspicious` and the flagged text |
| `Fact` | `kind`, `statement`, the exact `quote`, `incident_category`, hours (`hours_raw`, `hours_low`, `hours_high`), event time (raw and UTC, with its precision), `verification` and the reason for it, `voyage_match`, `unresolved_refs` |
| `SourceFile` | One per CSV and per message folder: row or file count, sha256 fingerprint, and whether it was loaded |

### IDs: `evidence_id` and the `Evidence` label

Every node has an **`evidence_id`**: its own stable, readable ID, taken from the data. Answers cite it, so it stays the same every time the graph is rebuilt (Neo4j's internal IDs don't).

| Node | `evidence_id` | Example |
|---|---|---|
| Vessel, Voyage, Incident, PortCall, Booking | the table's ID column | `V001`, `VG-1042`, `INC-201` |
| Port | port code | `NLRTM` |
| CongestionSnapshot | port and date | `CONG-SGSIN-2026-09-17` |
| Customer | customer name | `CUST-halden-retail` |
| Message | file name | `MSG:noon_2026-10-06_ocean_star` |
| Fact | message plus fact number | `MSG:noon_2026-10-06_ocean_star#2` |
| SourceFile | file or folder path | `FILE:incidents.csv` |

Every node also carries the label **`Evidence`** next to its type (for example `:Incident:Evidence`). It holds no value. It lets one database rule guarantee that no two nodes anywhere share an `evidence_id`, and lets any cited ID be looked up with one query: `MATCH (n:Evidence {evidence_id: 'INC-201'})`. A side effect is that the Neo4j browser colours every node the same.

To see the loaded schema yourself, run `CALL db.schema.visualization()` in the Neo4j browser.

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

`kg verify` ([`graph/verify.py`](src/knowledge_graph_agent/graph/verify.py)) checks the graph and returns a pass or fail for each named check. Any failure stops the run. It checks that:

- there is one node per table row
- every node has an `evidence_id` and a `source_ref`
- there are no broken links, and every fact belongs to a message
- each file's fingerprint (sha256) still matches what was loaded, for the tables and the message folder
- every recorded delay equals the real elapsed hours in UTC
- look-alike vessels stay separate, and no vessel name as written links to two vessels
- every fact's quote appears word for word in its message
- all 15 core messages are loaded

Tests:

- `tests/unit` needs nothing running. It covers time conversion, name matching (`OS bridge`, `M/V OCEAN STAR`, Ocean Star vs Ocean Starlight), the rules (the Kaimon injection, the weather conflict, the Silver Tern correction, unknown origins) and the cached extractions.
- `tests/graph` needs Neo4j. It loads the real graph and checks specific cases, such as VG-1042's times, that `Sulamar Gestão Naval` keeps its accented letters, and that INC-203 is linked to both its table row and the customs message.

## Repository layout

```text
data/                     input tables and messages (see DATA_README.md)
notebooks/                data profile
cache/extractions/        cached Claude output, one file per message (committed)
src/knowledge_graph_agent/
  cli.py                  the kg command
  config.py               settings from config.yaml and .env
  timeutil.py             local time to UTC
  graph/                  database connection, loading tables and facts, checks
  extract/
    messages.py           read messages; who sent each one
    schema.py             what the extraction model must return
    llm.py                the Claude call and the cache
    resolve.py            match names to vessels, voyages, ports
    rules.py              quotes, injection, times, reports, corrections, verification
  agent/                  question answering (planned)
  evaluate/               evaluation (planned)
tests/                    unit/ (no services) and graph/ (needs Neo4j)
config.yaml               snapshot time, models, limits
```

