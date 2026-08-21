---
name: email-ingress-gateway
kind: gateway
---

# Email Ingress Gateway

### Goal

Maintain the latest material email-ingress event as structured truth for Podsum
responsibilities.

### Continuity

- external-driven

### Receives

- `POST /trigger/email-ingress-gateway`: JSON with root-level `event_id` and
  `message` fields. The Reactor stages accepted arrivals in the Gateway's
  upstream ingress producer `email-ingress-gateway::ingress` at `inbox.json`.
  For each wake, use the newest object in that inbox as the payload; never
  replace its fields with the ingress fingerprint.

### Maintains

The published world-model has one subscribed facet, `incoming`. Its canonical
structured backing is `state/incoming.json`, a JSON object with exactly these
fields:

- `event_id`: non-empty string copied from the newest staged trigger payload's
  root-level `event_id`.
- `message`: string copied from that same payload's root-level `message`.

Derived Markdown summaries are optional projections and are excluded from the
fingerprint. Material equality is the normalized pair (`event_id`, `message`):
trim surrounding whitespace in each string; do not case-fold; ignore JSON key
order. No timestamp, request id, connector cursor, receipt id, or ingress
boundary fingerprint may stand in for either field. Postcondition: after a
trigger with both fields, `state/incoming.json` contains that exact normalized
pair.

#### incoming

The structured `state/incoming.json` backing above. Its fingerprint is derived
only from its material `event_id` and `message` fields.

### Emits

- email-bootstrap-responsibility
