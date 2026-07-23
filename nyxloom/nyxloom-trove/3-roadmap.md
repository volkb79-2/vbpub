---
kind: roadmap
schema_version: 1
milestones:
- id: M1
  title: Trustworthy core
  target_product_version: 1
  features:
  - F001
  - F003
  - F004
  - F005
  - F006
  - F013
  status: done
- id: M2
  title: Guided onboarding
  target_product_version: 1
  features:
  - F002
  status: done
- id: M3
  title: Intent<->reality
  target_product_version: 1
  features:
  - F007
  status: active
- id: M4
  title: Smart scheduling + capability-matched routing
  target_product_version: 1
  features:
  - F008
  - F009
  - F014
  status: planned
- id: M5
  title: Self-contained runtime + multi-tenant envs
  target_product_version: 1
  features:
  - F010
  - F011
  status: planned
- id: M6
  title: Human control surface
  target_product_version: 1
  features:
  - F012
  - F015
  status: planned
---

# nyxloom — roadmap

Milestones group the product-definition features by delivery phase. M1-M2 are
`done` (trustworthy core + guided onboarding). M3 (gap-engine) is `active`.
M4-M6 (smart scheduling + capability-matched routing, self-contained runtime +
multi-tenant envs, human control surface) are `planned`. The routing work is
detailed in docs/routing-model-redesign.md (D-R1..R15). The capability catalog
(F014) and routing-UI/scheduled-jobs (F012/F015) bundle is file-disjoint from
F5 (gap-engine) and so parallelizable with M3; only the carver band-prediction
(D-R3, part of F008) couples to the carve path and sequences with F5.

