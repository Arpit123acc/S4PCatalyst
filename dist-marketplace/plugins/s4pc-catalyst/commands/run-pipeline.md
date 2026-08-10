---
description: Run the 12-step clean-core RICEFW delivery pipeline on an FD or requirement
argument-hint: [path to FD file, e.g. input/FD-....md, or a requirement]
---

Run the **s4pc-ricefw-pipeline** skill for: $ARGUMENTS

Follow it exactly — clean core only, all three quality gates, and stop at every human checkpoint
for an explicit decision. Verify every SAP object with the `s4pc` MCP tools and write the run
manifest + deliverables to `output/<OBJECT-ID>/`.
