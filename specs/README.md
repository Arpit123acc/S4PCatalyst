# Specs

Spec-driven delivery for S/4HANA Cloud Public Edition. In this toolkit a "spec" has three parts,
which map directly onto the artifacts the RICEFW pipeline already produces:

| Spec part | This toolkit's artifact | Where it lives |
|---|---|---|
| **requirements** | Functional Design (business context, numbered requirements, acceptance criteria) | `input/FD-*.md` → `output/<RUN-ID>/01-discovery.md`, `04-fd-analysis.md` |
| **design** | Extensibility decision + Technical Design (mode per capability, released objects + verdicts) | `output/<RUN-ID>/02-solution-proposal.md`, `03-release-verdicts.md`, `05-technical-design.md` |
| **tasks** | The 12 RICEFW pipeline steps, tracked in the run manifest | `output/<RUN-ID>/run.json` (Workflow Explorer) |

## How to use
- **Automated (recommended):** drop an FD in `input/` and run the pipeline (`/run-pipeline
  input/your-fd.md`). The pipeline generates the requirements → design → tasks artifacts into
  `output/<RUN-ID>/` and tracks task status live in the Workflow Explorer.
- **Manual authoring:** copy `_template/` to `specs/<feature>/` and fill in `requirements.md`,
  `design.md`, and `tasks.md` before (or instead of) running the pipeline.

The templates in `_template/` mirror the pipeline's structure so hand-written specs and generated
ones stay consistent.
