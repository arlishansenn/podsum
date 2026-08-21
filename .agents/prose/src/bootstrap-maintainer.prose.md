---
name: 77CTSX38RYPEE7AM92CMCGE5W3
kind: responsibility
id: 77CTSX38RYPEE7AM92CMCGE5W3
---

# Bootstrap Maintainer

### Goal

Keep Podsum's latest ingress snapshot current from the Gateway's material
incoming event.

### Requires

#### incoming

- `incoming`: the Gateway's current structured event with an `event_id` and a
  `message`.

### Maintains

The published atomic world-model has canonical structured backing at
`state/snapshot.json`, a JSON object with exactly these fields:

- `snapshot_event_id`: non-empty string equal to `incoming.event_id`.
- `snapshot_message`: string equal to `incoming.message`.
- `event_updates`: non-negative integer; initialize to 1 for the first event
  and increment exactly once when the material (`event_id`, `message`) pair
  differs from the prior snapshot.

Material equality is the normalized (`snapshot_event_id`, `snapshot_message`,
`event_updates`) tuple: trim surrounding whitespace in the two strings, do not
case-fold, and use the integer value for `event_updates`. Derived Markdown,
timestamps, request ids, connector cursors, receipt ids, and cosmetic JSON key
order are immaterial and excluded from the fingerprint. Postcondition:
`state/snapshot.json` agrees with the subscribed `incoming` pair; an unchanged
pair does not change `event_updates`.

### Continuity

- input-driven
