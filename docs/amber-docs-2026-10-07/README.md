# October 7 design snapshot

These four imported documents are preserved as a historical design snapshot. **They are not the
current architecture, contract, roadmap, or implementation inventory.** Their filenames and
internal claims of authority reflect the source bundle; the repository's canonical documents
take precedence. Consolidation and assessment were completed on 2026-10-07.

| Imported document | Current location for its useful material |
|---|---|
| [Goals and architecture](amber-goals-and-architecture.md) | [Canonical goals](../00-goals-and-architecture.md) and [conditional terminology/detection design](../05-terminology-and-mention-detection.md) |
| [Schemas and tools](amber-v1-schemas-and-tools.md) | [Binding v1 contract](../02-v1-schemas-and-tools.md); proposed terminology fields remain in the conditional design |
| [Roadmap](amber-roadmap.md) | [Canonical roadmap](../04-roadmap.md), preserving the task-first M1–M3 sequence |
| [Clinical IE research](clinical-ie-state-of-the-art.md) | [Canonical survey](../01-state-of-the-art.md), retaining study/supervision qualifications |

The bundle restores older assumptions: an empty scaffold, vocabulary/detector work before a
clinical baseline, policy enforcement in M3, mandatory agents and fuzzy alignment, null-valued
claims for `no_claim`, and automatic concept upgrades. Those statements are superseded. Patient
aggregation remains deferred; provider/destination policy precedes source-bearing calls; explicit
outcomes, final-claim IDs, and complete support closure remain required.

The [review](../notes/2026-10-07-documentation-review.md) records accepted direction, revisions,
deferred scope, and verification limits. Follow the [documentation index](../README.md) for
implementation work. Preserve these files as history rather than maintaining a second live spec.
