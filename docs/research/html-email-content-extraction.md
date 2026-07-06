# HTML Email Content Extraction Research

Date: 2026-07-06

## Problem

Podsum currently risks treating the first visible-ish text fragment in an email as `Base Evidence`. For HTML newsletters and alert digests, that first fragment is often preview text, invisible padding, navigation, or legal/footer material rather than the user-valuable content. The fix should not be sender-specific. It should extract candidate content blocks, score them, preserve evidence boundaries, and pass only useful evidence to the IntelBrief layer.

## Source Findings

### MIME body selection

RFC 2046 defines `multipart/alternative` parts as interchangeable versions of the same information; their order is significant and alternatives appear in increasing faithfulness, with the best supported choice generally being the last supported part. Source: RFC 2046 section 5.1.4, `multipart/alternative` ([datatracker.ietf.org/doc/html/rfc2046#section-5.1.4](https://datatracker.ietf.org/doc/html/rfc2046#section-5.1.4)).

Python's `EmailMessage.get_body()` is explicitly designed to return the best candidate body part, with preference choices from `related`, `html`, and `plain`; it only considers candidate body parts marked inline when `Content-Disposition` exists. Source: Python `email.message` docs, `get_body()` ([docs.python.org/3/library/email.message.html#email.message.EmailMessage.get_body](https://docs.python.org/3/library/email.message.html#email.message.EmailMessage.get_body)).

Python's `walk()` traverses all MIME parts depth-first, which is still useful for evidence auditing and attachment/link discovery. Source: Python `email.message` docs, `walk()` ([docs.python.org/3/library/email.message.html#email.message.EmailMessage.walk](https://docs.python.org/3/library/email.message.html#email.message.EmailMessage.walk)).

Implication for Podsum: use `get_body(('html', 'plain'))` or equivalent logic for primary body extraction, but keep `walk()`-level metadata so the EvidencePack can say which part was selected and what was ignored.

### HTML parsing and block extraction

Python `html.parser.HTMLParser` can parse invalid markup and exposes callbacks for start tags, end tags, and data. Source: Python `html.parser` docs ([docs.python.org/3/library/html.parser.html](https://docs.python.org/3/library/html.parser.html)).

Beautiful Soup is a higher-level parser for pulling data from HTML/XML, supports navigation/search/modification of the parse tree, CSS selectors, and tree modification. Source: Beautiful Soup docs ([crummy.com/software/BeautifulSoup/bs4/doc](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)).

Mozilla Readability exposes extracted `title`, processed `content`, tag-stripped `textContent`, character `length`, `excerpt`, author metadata, site name, language, and publication time, and has a `linkDensityModifier` knob to penalize link-heavy nodes. Source: Mozilla Readability README ([github.com/mozilla/readability](https://github.com/mozilla/readability)).

Implication for Podsum: HTML email should be parsed into structural blocks rather than flattened immediately. Candidate block units should be tags such as `h1-h6`, `p`, `li`, table cells/rows where email layouts put text, and linked headline anchors. A block should carry text, links, DOM-ish path/tag, style/hidden signals, and neighboring context.

### Preview text, preheaders, and invisible padding

SendGrid defines a preheader as the short summary text that follows the subject line in inbox views, used by many clients before the user opens the email. Source: SendGrid glossary ([twilio.com/docs/sendgrid/glossary/preheader](https://www.twilio.com/docs/sendgrid/glossary/preheader)).

Mailchimp notes that preheader text is the text next to the subject line and is often the first text found in an email, though not always. Source: Mailchimp preheader guide ([mailchimp.com/resources/email-preheader](https://mailchimp.com/resources/email-preheader/)).

Customer.io documents the common practice of adding repeated zero-width non-joiners and non-breaking spaces after preview text to prevent clients from pulling later body content into the inbox preview. Source: Customer.io custom preheader docs ([docs.customer.io/journeys/channels/email/headers/custom-preheader-text](https://docs.customer.io/journeys/channels/email/headers/custom-preheader-text/)).

Python `unicodedata` exposes Unicode character categories via `unicodedata.category()`, including format characters through the Unicode Character Database. Source: Python `unicodedata` docs ([docs.python.org/3/library/unicodedata.html](https://docs.python.org/3/library/unicodedata.html)).

Implication for Podsum: zero-width and format characters are not evidence by themselves. They should be normalized or counted as a low-signal feature. However, visible preheader copy may still be a useful summary signal, so it should be demoted, not blindly deleted.

### Hidden and non-rendered content

The HTML Standard says an element is rendered if it has associated layout boxes, and the `hidden` attribute normally means the element is not rendered. Source: WHATWG HTML rendering section ([html.spec.whatwg.org/multipage/rendering.html](https://html.spec.whatwg.org/multipage/rendering.html)).

MDN says the `hidden` global attribute indicates the browser should not render the contents of the element; hidden-state content is not currently relevant to the page or is used for reuse by other parts of the page. Source: MDN `hidden` attribute docs ([developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/hidden](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Global_attributes/hidden)).

Implication for Podsum: hidden/display-none/opacity-zero/tiny-font/offscreen content in email HTML should be recorded as a rendering-signal risk and usually demoted. Some email clients handle CSS inconsistently, so the algorithm should not rely on CSS rendering perfectly; it should treat hidden-like style as a feature in scoring, not as the only decision.

## Low-Signal Signals

These are general signals, not sender-specific rules:

- Preview/preheader-like position: first block, short, subject-adjacent, generic summary wording, or followed by zero-width/non-breaking-space padding.
- Invisible/format padding: high count or ratio of Unicode format characters, non-breaking spaces, or whitespace-only text.
- Hidden-like rendering: `hidden`, `display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`, very small line-height, offscreen positioning, or ARIA-hidden.
- Navigation and chrome: view-in-browser, preferences, social links, app download prompts, share links, headers/menus.
- Compliance/footer: unsubscribe, mailing address, privacy policy, terms, copyright, "you received this email".
- Tracking and images: 1x1 pixels, image-only blocks without meaningful alt text, tracking URLs, open pixels.
- Link-only or high-link-density blocks: many URLs/anchors with little surrounding lexical text; Readability treats link density as a meaningful quality signal.
- Very low lexical content: too few word/CJK tokens after stripping URLs, punctuation, formatting characters, and boilerplate.
- Duplicate boilerplate: exact or near-exact block repeated across multiple UIDs, especially when not accompanied by distinct article titles or excerpts.
- Algorithmic artifacts: internal labels such as `skip`, `hard_skip`, `link_triage`, classifier decisions, confidence scores, and policy names must not become user-facing evidence.

## High-Value Signals

Candidate blocks should be promoted when they have one or more of these:

- Headline-like text in `h1-h6`, strong table headings, or anchor text with enough lexical content.
- Paragraph/list item text with topical nouns, named entities, dates, numbers, funding amounts, product names, company/person names, or decision/action language.
- A nearby content link whose anchor text is informative rather than generic.
- Text aligned with sender, subject, or tracked topic keywords but not merely keyword-match boilerplate.
- Multiple neighboring blocks that form an article card: title, source, excerpt, date, CTA link.
- Low link density for paragraph content, or meaningful anchor text for list/card content.
- Attachment/body metadata that explains why body evidence is missing.

## Recommended Evidence Model

Add an internal `email_body_block` style representation before final EvidencePack summarization. It can remain internal at first, but the final EvidencePack should preserve enough traceability:

```json
{
  "type": "email_body_excerpt",
  "uid": "1019",
  "source_part": {
    "content_type": "text/html",
    "part_index": 2,
    "content_disposition": "inline"
  },
  "block_index": 7,
  "tag_path": "html/body/table/tr/td/a",
  "role": "linked_headline",
  "text": "Informative headline or excerpt",
  "nearby_links": [
    {
      "url": "https://example.invalid/article",
      "anchor_text": "Informative headline"
    }
  ],
  "score": {
    "value": 0.82,
    "positive": ["headline_tag", "topic_match", "informative_anchor"],
    "negative": ["newsletter_layout"]
  },
  "boundaries": [
    "HTML email block extraction; not full article text"
  ]
}
```

Dropped or demoted material should be summarized as counts and reason categories, not copied into IntelBrief input:

```json
{
  "dropped_blocks": {
    "preheader_or_preview": 1,
    "hidden_or_padding": 3,
    "footer_or_unsubscribe": 5,
    "link_only": 2
  }
}
```

## Implementation Shape For Podsum

1. Split current `snippet` generation into a module-level pipeline:
   `MIME select -> HTML/plain parse -> block extraction -> block scoring -> evidence selection`.

2. Prefer body candidates via MIME semantics:
   use `get_body(('html', 'plain'))` for primary extraction when available, but keep `walk()` metadata for audit and fallback.

3. Parse HTML structurally:
   if no dependency is desired, extend `HTMLParser` to produce block records; if dependency is acceptable, use Beautiful Soup for robust tree navigation and selectors. Do not use regex-only HTML stripping as the primary extractor.

4. Score blocks instead of deleting strings:
   assign positive/negative features and select top N blocks. If all blocks are low-signal, emit metadata-only evidence plus dropped-block counts.

5. Preserve useful links near selected blocks:
   content links near promoted blocks should be evidence candidates. Links found only in dropped footer/navigation blocks should not become public-link evidence unless another signal promotes them.

6. Keep algorithm labels out of LLM-facing IntelBrief input:
   internal reasons can remain in EvidencePack or sidecar review data, but the IntelBrief prompt should receive user-meaningful boundaries like "only metadata available" or "only newsletter headline/excerpt available", not `skip`, `hard_skip`, or classifier labels.

7. Add regression fixtures:
   one multipart alternative email with HTML article cards, one hidden-preheader email with zero-width padding, one footer-heavy newsletter, one link-only alert, and one plain-text personal/action email. Tests should assert selected evidence blocks and dropped reason counts.

8. Treat LLM preprocessing as a second pass:
   deterministic extraction should reduce noise and preserve traceability first. The LLM preprocessor can then summarize selected blocks into clean facts, but it should not be asked to recover from raw full HTML email noise.

## Design Consequence

The immediate bug is not "Nikkei uses a bad first line"; it is that Podsum conflates "first body text" with "base evidence." The durable fix is a block-level evidence extractor with transparent scoring and compact dropped-block audit data. That gives the downstream IntelBrief agent clean evidence while still allowing Workbench users to inspect why content was omitted.
