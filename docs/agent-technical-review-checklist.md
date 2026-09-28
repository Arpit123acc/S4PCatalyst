# Technical Review Checklist — AI Agents Going to Release

**Purpose.** One pass/fail checklist every agent clears before release sign-off.
**Audience.** Tech leads building agents; reviewers signing them off.
**Time to run.** About an hour per agent, once the evidence below is attached.

Every item is a question with a **pass criterion**. Items marked **BLOCKER** stop a
release. **MUST-FIX** can ship with a named owner and a date. **ADVISORY** is recorded,
not enforced.

Each item exists because it failed somewhere real. Where a defect motivated an item,
it is cited — a checklist nobody believes gets filled in without being run.

---

## A. Grounding — does the agent only assert what it can verify

This is the category where AI agents fail differently from ordinary software, and it
is where review effort should concentrate.

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| A1 | Does every **entity name** the agent emits (product, API, app, service, table, person, price) come from a catalogue, not from model memory? | Name a source for each class of entity. "The model knows it" is a fail. | **BLOCKER** |
| A2 | Is there a **verification step between generation and output**? | A deterministic pass that checks emitted names against the catalogue and labels each `verified` / `not_found` / `unverifiable`. | **BLOCKER** |
| A3 | **Negative control:** feed the agent a fabricated entity. Is it flagged? | A made-up name must come back `not_found`. If everything passes, the guard is not a guard. | **BLOCKER** |
| A4 | **Positive control:** feed it a known-real entity. Does it pass? | Must return `verified`. A matcher too strict to pass real inputs is as useless as one too lenient. | **BLOCKER** |
| A5 | Does the output distinguish **"verified against a source"** from **"plausible but unchecked"**? | Two visibly different states in the UI and in exports. One undifferentiated output is a fail. | MUST-FIX |
| A6 | If the agent spawns a sub-model or CLI, does that child inherit the **verification tooling and the project rules**? | Check the working directory and environment the child runs in. A child launched in a temp dir inherits no config and answers from memory alone. | **BLOCKER** |

> *Observed:* an agent's prompt required each question to "name a specific Fiori app or
> BTP service", while the CLI producing them ran in the system temp directory — no tool
> access, no project rules. Every app name was unverifiable, and nothing said so.

---

## B. Data provenance and freshness

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| B1 | For each reference dataset: **where does it come from**? | A named source per dataset. | **BLOCKER** |
| B2 | Is it **live** or **materialised** (a copy on disk)? | Stated explicitly. Both are acceptable; an unstated choice is not. | MUST-FIX |
| B3 | If materialised — **what job refreshes it, and is that job actually wired up**? | Open the scheduler config and confirm the dataset is named in it. | **BLOCKER** |
| B4 | How many **copies** sit between source and output? | Each hop is a staleness site and a sync obligation. More than one needs justification. | MUST-FIX |
| B5 | Does the output **state its data vintage**? | A build date or version visible to the user, not just in a log. | MUST-FIX |
| B6 | How fast does the **underlying source** actually change? | Refresh cadence must be faster than the source. Monthly refresh of a twice-yearly source is fine; daily refresh of an hourly source is not. | ADVISORY |

> *Observed:* a dataset was assumed to be on the monthly refresh. It was not named in the
> job at all, and had not been rebuilt in weeks. Nothing surfaced that — the data was
> present and well-formed, just old.

---

## C. Failure modes — the silent ones

The dominant defect shape in agent systems is **failing by returning less**, which is
indistinguishable from a correct empty result.

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| C1 | Does every empty result distinguish **"genuinely none"** from **"lookup unavailable"**? | Different messages, both explicit. | **BLOCKER** |
| C2 | Does every **fallback announce itself** in the output? | The user can tell which source answered. A silent fallback to stale or generic data is a fail. | **BLOCKER** |
| C3 | Is **coverage disclosed** rather than implied? | If the dataset covers 537 of 675 items, the response says so. Absence must never read as "none exists". | MUST-FIX |
| C4 | Are there **name collisions** between components — two things registered under one identifier? | Enumerate registered names and assert uniqueness. Where a collision is resolved automatically, it must log loudly. | MUST-FIX |
| C5 | Can a **swallowed exception** skip a retry or degradation path that was written for it? | Trace each `catch`. A throw that jumps past its own fallback is a common and invisible failure. | MUST-FIX |
| C6 | Does the agent ever present a **confident explanation for an empty result** that it has not verified? | Prose like "none exists for this item" must be backed by a coverage check, not assumed. | **BLOCKER** |

> *Observed:* two components registered a tool under the same name; the loader kept the
> first and dropped the second with no signal. The surviving tool covered 1 of 675
> records and returned a fluent note explaining the absence. It returned HTTP 200 every
> time. The wording is what stopped anyone from investigating.

---

## D. Reproducibility

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| D1 | Does every run have a **run id**, recorded with its inputs? | Yes, and it appears in exports. | MUST-FIX |
| D2 | Can a past output be **traced to the data version** that produced it? | Dataset version or build date captured per run. | MUST-FIX |
| D3 | Same input, same data version — is the output **stable**? | If not, the variance is bounded, expected, and documented. | ADVISORY |
| D4 | Is there a **rollback path** for a bad data publish? | Previous version retained; restoring it is a documented step. | MUST-FIX |

---

## E. Security

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| E1 | Are all **secrets read from environment only** — none committed, none defaulted in code? | Grep the repo and its history. | **BLOCKER** |
| E2 | Has **repository visibility** been checked against what the code assumes? | A public repo must contain no key, no internal hostname, no credentialed default. | **BLOCKER** |
| E3 | What **address does each service bind**, and what authenticates it? | Loopback, or authenticated. Wildcard bind with no auth is a blocker regardless of network position. | **BLOCKER** |
| E4 | Does the agent hold **tools or permissions broader than its job** — file read, shell, arbitrary network? | Each one justified, or removed. | MUST-FIX |
| E5 | Are **permission-skipping flags** in use, and is that deliberate? | Flags that disable confirmation prompts are declared and justified. | MUST-FIX |
| E6 | Is there an **audit log — and does anything read it**? | A log nobody reads is not a control. Name what consumes it and how an anomaly surfaces. | MUST-FIX |

> *Observed:* a warning was written to an audit file on 20 consecutive restarts. Nothing
> read the file. The condition it warned about persisted for weeks.

---

## F. Human-in-the-loop

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| F1 | Where are the **approval gates**, and what exactly does each approve? | Enumerated, with the artifact under review named. | **BLOCKER** |
| F2 | Is **silence ever treated as approval**? | Never. Timeouts must fail closed, not proceed. | **BLOCKER** |
| F3 | Are approval decisions **recorded** with who, when, and what was shown? | Persisted, not just logged. | MUST-FIX |
| F4 | Can a gate be **skipped by a flag or an alternate code path**? | Enumerate every entry point. One rule enforced in one path and skipped in another is the most common governance failure. | **BLOCKER** |

---

## G. Integration contract

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| G1 | Does the agent **describe itself** at a documented endpoint — inputs, outputs, status? | A discovery endpoint the orchestrator reads, rather than a hardcoded registry entry. | MUST-FIX |
| G2 | Is the **response shape versioned**, and are changes additive? | A consumer written today must not break on next month's field rename. | MUST-FIX |
| G3 | What happens when a **dependency is down**? | Visible error, or an announced degraded mode. Never a silent fall back to stale data. | **BLOCKER** |
| G4 | Can the agent still be **developed locally** without production infrastructure? | A documented dev mode. If not, say so — it constrains who can contribute. | ADVISORY |
| G5 | Are URLs between components **environment-independent**? | No hardcoded load balancer names or host-specific paths. | MUST-FIX |

---

## H. Output quality

| # | Check | Pass criterion | Sev |
|---|---|---|---|
| H1 | Has someone **measured** output quality on a real sample, not inspected a demo run? | A count: how many outputs, how many distinct, how many verified. | MUST-FIX |
| H2 | Is there **hidden repetition** — the same value emitted across many outputs because of a coarse lookup? | Frequency-count the emitted values. A single value dominating is a design smell. | MUST-FIX |
| H3 | Is any **reference data in the code unreachable** — written but never read? | Dead entries in a lookup table mean the logic is narrower than it appears. | MUST-FIX |
| H4 | Does the quality metric reward the **right thing**? | A metric that improves when the agent returns less, or stays flat across all settings, is not measuring anything. | ADVISORY |

> *Observed:* a lookup table held three values per category; the code read only the
> first. Two-thirds of the table was dead, and one value appeared in 268 outputs before
> anyone counted.

---

## Sign-off

A release is signed off when:

1. **Zero BLOCKERs open.**
2. Every **MUST-FIX** has a named owner and a date.
3. The **evidence pack** is attached:

| Evidence | What it is |
|---|---|
| Negative + positive control results | A3 / A4, run output pasted, not described |
| Data provenance table | Every dataset: source, live-or-copy, refresh job, last build |
| Registered-name uniqueness check | C4 output |
| Secret scan | E1 / E2, including git history |
| Bind + auth table | Every listening port |
| Approval gate map | Every gate, every entry point |
| Quality measurement | H1 counts on a real sample |

4. Two named signatures: **building tech lead** and **an independent reviewer** who did
   not build the agent.

---

## Notes for reviewers

**Ask for output, not description.** "We validate app names" is not evidence. The paste
of a fabricated name coming back `not_found` is.

**Be most suspicious of the confident short answer.** Agents that fail loudly get fixed.
The expensive failures here return HTTP 200, well-formed, fluent, and thin.

**Check the second code path.** When a rule is implemented once and a second entry point
re-derives it, the two drift. This has been the single most common defect shape observed.

**A metric that always passes is not a control.** Before trusting a check, break it
deliberately and confirm it goes red.

---

*Version 1.0 — 2026-09-28. Derived from defects found in production agent review.
Send corrections to the platform team; this is intended to be revised as new failure
shapes are found.*
