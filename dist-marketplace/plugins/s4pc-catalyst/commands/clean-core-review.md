---
description: Adversarial clean-core review of ABAP code or a technical design (SHIP/FIX/REDESIGN)
argument-hint: [path to code/design file, or paste the artifact]
---

Run the **s4pc-clean-core-review** skill on: $ARGUMENTS

Extract every SAP object and check it with `check_object_release_state`, lint all ABAP with
`abap_cloud_lint`, and end with a clear SHIP / FIX / REDESIGN verdict and an itemised findings list.
