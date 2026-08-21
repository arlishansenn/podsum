# Graph Report - .  (2026-07-13)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1059 nodes · 2671 edges · 47 communities (41 shown, 6 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 109 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9900580c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Podsum CLI Tests
- Email Brief Composition
- Transcript Text Cleaning
- Email Workbench Server
- Transcript Cleaner Tests
- Email Run Graph
- Any
- podcast_downloader.py
- Feishu Delivery Bundle
- Email Evidence Extraction
- Podsum CLI Commands
- Object Lifecycle Harness
- RuntimeError
- Email Domain Model
- EmailWorkbench
- podsum_email_summary.py
- Mailparser Cleaning Script
- Markdown EPUB Preprocessing
- clean_text
- Email Summary Prompts
- EPUB Generation
- Markdown to HTML
- Email Fixture Tool
- Email Summary Research
- Link Evidence Triage
- EPUB Delivery Tools
- Project Documentation
- preprocessed_evidence_digest
- Markdown Cleaner CLI
- JavaScript Mail Parser
- Markdown Header Model
- AI Industry Notes
- CLI Argument Helpers
- Minimal EPUB Writer
- Markdown Chapter Parsing
- Feishu File Sender
- Compute Infrastructure
- AI Platform Companies
- EPUB Metadata Models
- Python Runtime Diagnostics
- Model Ecosystem Trends
- Hermes Skill Installer
- Safe Filename Utility
- Semiconductor Companies
- Transformer Pretraining
- Hermes Summarization

## God Nodes (most connected - your core abstractions)
1. `PodsumCliTest` - 77 edges
2. `TranscriptCleanerTest` - 54 edges
3. `WorkbenchConfig` - 37 edges
4. `clean_text()` - 31 edges
5. `EmailEvidencePack` - 30 edges
6. `run_podsum()` - 27 edges
7. `EvidenceNeed` - 20 edges
8. `MarkdownProcessor` - 19 edges
9. `run()` - 19 edges
10. `clean_text()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Workbench` --semantically_similar_to--> `EmailWorkbench`  [INFERRED] [semantically similar]
  plans/podsum-email-two-agent-prd.md → CONTEXT.md
- `PodsumCliTest` --uses--> `LinkClassification`  [INFERRED]
  tests/test_podsum_cli.py → outputs/email/providers.py
- `PodsumCliTest` --uses--> `FakeLinkClassifier`  [INFERRED]
  tests/test_podsum_cli.py → outputs/email/providers.py
- `PodsumCliTest` --uses--> `EmailEvidencePack`  [INFERRED]
  tests/test_podsum_cli.py → outputs/email/schemas.py
- `PodsumCliTest` --uses--> `EmailIntelBrief`  [INFERRED]
  tests/test_podsum_cli.py → outputs/email/schemas.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Model Training and Adaptation Stack** — mse435_transcripts_course_md_mse435_complete_course_transcript_transformer, mse435_transcripts_course_md_mse435_complete_course_transcript_pretraining, mse435_transcripts_course_md_mse435_complete_course_transcript_post_training, mse435_transcripts_course_md_mse435_complete_course_transcript_rlvr, mse435_transcripts_course_md_mse435_complete_course_transcript_evals, mse435_transcripts_course_md_mse435_complete_course_transcript_continual_learning [EXTRACTED 0.90]
- **Email Summary Object Model** — outputs_readme_emailtopicmap, outputs_readme_emailevidencepolicy, outputs_readme_emailevidencepack, outputs_readme_emailintelbrief, outputs_readme_reviewchecklistpanel [EXTRACTED 0.95]
- **Podsum Runtime Pipeline** — outputs_readme_podsum, outputs_readme_mlx_whisper, outputs_readme_hermes, outputs_readme_emailevidencepack, outputs_readme_emailintelbrief, outputs_readme_podsum_py [EXTRACTED 0.90]
- **Email Intelligence Core Objects** — plans_podsum_email_two_agent_prd_emailtopicmap, plans_podsum_email_two_agent_prd_emailevidencepolicy, plans_podsum_email_two_agent_prd_emailevidencepack, plans_podsum_email_two_agent_prd_emailintelbrief, plans_podsum_email_two_agent_prd_evidenceneedqueue [EXTRACTED 0.95]
- **Email Run Graph Flow** — plans_podsum_email_two_agent_prd_emailrungraph, plans_podsum_email_two_agent_prd_emailevidenceagent, plans_podsum_email_two_agent_prd_emailintelbriefagent, plans_podsum_email_two_agent_prd_evidenceneedstore, plans_podsum_email_two_agent_prd_evidencepackstore [EXTRACTED 0.90]
- **Workbench Review Pipeline** — plans_podsum_email_workbench_gui_spec_emailtopicmap, plans_podsum_email_workbench_gui_spec_emailevidencepolicy, plans_podsum_email_workbench_gui_spec_emailevidencepack, plans_podsum_email_workbench_gui_spec_emailintelbrief, plans_podsum_email_workbench_gui_spec_reviewchecklistpanel, plans_podsum_email_workbench_gui_spec_epub_delivery [EXTRACTED 0.89]
- **Podcast Report Flow** — context_podcast_pipeline, context_feed, context_episode, context_transcript, context_interpretation, context_reportbundle, context_deliverytarget [EXTRACTED 0.92]
- **Email Summary Flow** — context_email_summary_flow, context_emailevidencepack, context_emailitem, context_linkevidence, context_emailintelbrief, context_deliverytarget [EXTRACTED 0.90]
- **SPEC Governance Surface** — context_spec, spec, context, spec_inbox, context_spec_inbox, context_upgrade_rule [EXTRACTED 0.86]

## Communities (47 total, 6 thin omitted)

### Community 0 - "Podsum CLI Tests"
Cohesion: 0.06
Nodes (11): CompletedProcess, EmailMessage, get_json(), get_text(), PodsumCliTest, post_json(), Path, run_podsum() (+3 more)

### Community 1 - "Email Brief Composition"
Cohesion: 0.09
Nodes (55): EvidenceNeedEventType, EvidenceNeedStatus, _append_need_references(), _brief_from_markdown(), BriefComposition, _build_markdown(), compose(), compose_and_persist() (+47 more)

### Community 2 - "Transcript Text Cleaning"
Cohesion: 0.07
Nodes (56): blocks_equal(), clean_text(), CleaningResult, CleaningStats, collapse_adjacent_duplicate_blocks(), collapse_adjacent_latin_word_repeats(), collapse_exact_embedded_sentence_blocks(), collapse_intra_sentence_short_gap_repeats() (+48 more)

### Community 3 - "Email Workbench Server"
Cohesion: 0.10
Nodes (49): BaseHTTPRequestHandler, add_args(), artifact_info(), atomic_write_text(), brief_source_coverage(), build_parser(), checklist_payload(), commands_payload() (+41 more)

### Community 5 - "Email Run Graph"
Cohesion: 0.14
Nodes (45): build_email_run_context(), build_email_run_graph(), build_evidence_pack(), build_in_memory_email_run_graph(), _classification_confidence_threshold(), classify_links(), compose_brief(), _composition() (+37 more)

### Community 6 - "Any"
Cohesion: 0.09
Nodes (44): action_reason(), append_review_checklist(), apply_topics(), brief_type_distribution(), build_intel_brief_draft(), classify_brief_items(), delivery_evidence_boundary(), delivery_excerpt() (+36 more)

### Community 7 - "podcast_downloader.py"
Cohesion: 0.13
Nodes (40): Element, build_parser(), child_text(), content_length(), date_prefix(), download_episode(), DownloadError, enclosure_url() (+32 more)

### Community 8 - "Feishu Delivery Bundle"
Cohesion: 0.18
Nodes (35): anchor_for(), build_bundle(), build_message(), build_parser(), cleanup_files(), find_audio_files(), find_markdown_files(), find_report_files() (+27 more)

### Community 9 - "Email Evidence Extraction"
Cohesion: 0.16
Nodes (28): LinkClassificationValue, apply_topics(), build_evidence_pack(), build_evidence_pack_from_messages(), build_evidence_pack_from_messages_with_classifier(), build_evidence_pack_with_classifier(), _classification_confidence_threshold(), classify_evidence_links() (+20 more)

### Community 10 - "Podsum CLI Commands"
Cohesion: 0.22
Nodes (34): add_common_args(), add_run_email_args(), build_parser(), cleanup_if_requested(), download(), download_latest(), email_summary_args_from_podsum(), email_summary_command() (+26 more)

### Community 11 - "Object Lifecycle Harness"
Cohesion: 0.21
Nodes (29): LifecycleStatus, apply_event(), as_dict(), as_list(), clear_object_shape(), deep_copy(), export_session_fixture(), import_session_fixture() (+21 more)

### Community 12 - "RuntimeError"
Cohesion: 0.14
Nodes (29): run_hermes_prompt(), send_hermes_file(), Path, SMTP email delivery adapter., send_smtp_email(), email_html_body(), email_reports_dir(), fetch_link_context() (+21 more)

### Community 13 - "Email Domain Model"
Cohesion: 0.14
Nodes (27): CleanupPolicy, DeliveryTarget, E 区, EmailIntelBrief, Episode, EpisodeRecord, EvidenceNeed, Feed (+19 more)

### Community 14 - "EmailWorkbench"
Cohesion: 0.10
Nodes (28): EmailWorkbench, EmailEvidenceAgent, EmailEvidencePack, EmailEvidencePolicy, EmailIntelBrief, EmailIntelBriefAgent, EmailRunGraph, EmailRunState (+20 more)

### Community 15 - "podsum_email_summary.py"
Cohesion: 0.13
Nodes (25): attachment_shapes(), body_snippet(), body_text_from_part(), classify_email(), config_value(), decode_bytes(), decode_header_value(), extract_message_links() (+17 more)

### Community 16 - "Mailparser Cleaning Script"
Cohesion: 0.18
Nodes (25): buildExcerpt(), canonicalHref(), chooseTitle(), cleanPlainLines(), cleanText(), clickHost(), disposablePhrases, extractHtmlBlocks() (+17 more)

### Community 17 - "Markdown EPUB Preprocessing"
Cohesion: 0.11
Nodes (22): _normalize_ampersand(), preprocess_markdown(), Markdown preprocessing for EPUB reader compatibility.  Cleans Markdown source be, Replace & with 和 throughout. WeChat Reading fails on & even when escaped., Ensure the first H1 is safe for WeChat Reading., Remove timestamp patterns like [00:00:01] or **[00:00:01]**., Remove common oral fillers from text., Apply the same preprocessing to a title string that the markdown body gets. (+14 more)

### Community 18 - "clean_text"
Cohesion: 0.13
Nodes (21): body_block_tokens(), body_snippet_from_parts(), body_text_blocks(), clean_digest_item(), clean_digest_public_sources(), clean_digest_text_list(), clean_digest_topic_relevance(), clean_mailparser_snippet() (+13 more)

### Community 19 - "Email Summary Prompts"
Cohesion: 0.12
Nodes (24): EmailEvidenceDigest, EmailEvidenceDigest Preprocessor Prompt, Podsum Email Link Policy, Email Summary Prompt, Hermes Interpretation Prompt, EmailEvidencePack, EmailEvidencePolicy, EmailIntelBrief (+16 more)

### Community 20 - "EPUB Generation"
Cohesion: 0.13
Nodes (13): EPUBGenerator, Generate EPUB file from chapters.          Args:             chapters: List of C, Generate EPUB3 files from markdown chapters and sections., Create and configure EPUB book object., Add chapters to EPUB., Render chapter to XHTML.          Args:             chapter: Chapter object, Render markdown content to HTML.          Args:             content: Markdown co, Add CSS styling to EPUB. (+5 more)

### Community 21 - "Markdown to HTML"
Cohesion: 0.13
Nodes (12): MarkdownProcessor, Extract YAML front matter if present.          Args:             content: Raw ma, Extract metadata from document headers.          Args:             content: Mark, Build table of contents from chapters and sections.          Returns:, Convert markdown to HTML.          This is a simplified converter for common mar, Render inline markdown elements (bold, italic, links, code).          Args:, Parse markdown table into HTML.          Args:             table_lines: List of, Escape HTML special characters. (+4 more)

### Community 22 - "Email Fixture Tool"
Cohesion: 0.22
Nodes (19): assert_sanitized(), build_parser(), capture(), fetch_raw_messages(), has_attachment(), main(), normalize_args(), placeholder_links() (+11 more)

### Community 23 - "Email Summary Research"
Cohesion: 0.14
Nodes (17): 邮件摘要流程, EmailEvidencePack, EmailItem, LinkEvidence, HTML Email Content Extraction Research, Beautiful Soup docs, Customer.io custom preheader docs, Mailchimp preheader guide (+9 more)

### Community 24 - "Link Evidence Triage"
Cohesion: 0.21
Nodes (16): build_link_triage(), canonical_link_target(), email_snippet_evidence(), enrich_item_links(), enrich_scan_links(), ensure_email_snippet_evidence(), hard_skip_reason_for_url(), has_email_snippet_evidence() (+8 more)

### Community 25 - "EPUB Delivery Tools"
Cohesion: 0.18
Nodes (12): EPUB / Delivery, clean_markdown.py, EPUB, export_epub.py, make-markdown-readable, send-file, Discord, Feishu (+4 more)

### Community 26 - "Project Documentation"
Cohesion: 0.18
Nodes (9): docs/agents/domain.md, docs/agents/issue-tracker.md, docs/agents/triage-labels.md, MS&E435 Complete Course Transcript, BGP, Education AI Company, Healthcare AI Company, Multicast Problem (+1 more)

### Community 27 - "preprocessed_evidence_digest"
Cohesion: 0.31
Nodes (10): compact_evidence_for_llm(), compact_item_for_llm(), compact_topic_ref(), deterministic_evidence_digest(), evidence_boundaries_for_llm(), json_object_from_text(), llm_brief_input(), normalize_evidence_digest() (+2 more)

### Community 28 - "Markdown Cleaner CLI"
Cohesion: 0.31
Nodes (8): bounded_text(), build_parser(), main(), ArgumentParser, Namespace, report_path_for(), run(), summarize_report()

### Community 29 - "JavaScript Mail Parser"
Cohesion: 0.22
Nodes (8): jsdom, mailparser, dependencies, jsdom, mailparser, name, private, type

### Community 30 - "Markdown Header Model"
Cohesion: 0.29
Nodes (6): Enum, Header, HeaderLevel, Markdown processing module for converting markdown to EPUB-compatible structure., Header hierarchy levels., Represents a markdown header.

### Community 31 - "AI Industry Notes"
Cohesion: 0.25
Nodes (8): Applied Compute, Continual Learning, Cursor, DoorDash, Evals, Post-training, RLVR, Windsurf

### Community 32 - "CLI Argument Helpers"
Cohesion: 0.33
Nodes (7): BaseException, add_args(), build_parser(), error_text(), main(), normalize_args(), ArgumentParser

### Community 33 - "Minimal EPUB Writer"
Cohesion: 0.43
Nodes (6): create_epub_from_markdown(), _inline_markdown(), _markdown_to_xhtml(), EPUB file generation module.  This module handles creating proper EPUB3 files fr, Convenience function to create EPUB from markdown content.      Args:         ma, _write_minimal_epub()

### Community 34 - "Markdown Chapter Parsing"
Cohesion: 0.29
Nodes (4): Parse markdown into chapters.          Chapters are delimited by H1 headers. Con, Parse sections from content (H2 and below).          Args:             content:, Represents a section (H2-H6)., Section

### Community 35 - "Feishu File Sender"
Cohesion: 0.52
Nodes (6): _api_base(), main(), Path, _send_file_message(), _tenant_token(), _upload_file()

### Community 36 - "Compute Infrastructure"
Cohesion: 0.33
Nodes (6): Agentic Workload, Cerebras, CPU, GPU, Heterogeneous Compute, Intel

### Community 37 - "AI Platform Companies"
Cohesion: 0.33
Nodes (6): ChatGPT, Codex, Databricks, Inference Compute, OpenAI, Token Factories

### Community 38 - "EPUB Metadata Models"
Cohesion: 0.33
Nodes (4): Initialize EPUB generator.          Args:             metadata: EbookMetadata ob, EbookMetadata, Metadata for the ebook., Initialize the markdown processor.

### Community 39 - "Python Runtime Diagnostics"
Cohesion: 0.67
Nodes (5): platform_venv_python(), podsum_python(), Path, running_inside_virtualenv(), runtime_diagnostics()

### Community 40 - "Model Ecosystem Trends"
Cohesion: 0.50
Nodes (4): Frontier Models, Open Source Models, Open Source Model Catch-up, Proprietary Model Layer

## Ambiguous Edges - Review These
- `README.md` → `MS&E435 Complete Course Transcript`  [AMBIGUOUS]
  mse435_transcripts/course_md/MS&E435_complete_course_transcript.md · relation: semantically_similar_to
- `README.md` → `docs/agents/domain.md`  [AMBIGUOUS]
  README.md · relation: conceptually_related_to
- `Inference Compute` → `Databricks`  [AMBIGUOUS]
  mse435_transcripts/course_md/MS&E435_complete_course_transcript.md · relation: conceptually_related_to
- `EPUB / Delivery` → `send-file`  [AMBIGUOUS]
  plans/podsum-email-workbench-gui-spec.md · relation: conceptually_related_to

## Knowledge Gaps
- **89 isolated node(s):** `{ simpleParser }`, `{ JSDOM }`, `genericTextPatterns`, `disposablePhrases`, `leadingNoisePatterns` (+84 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `README.md` and `MS&E435 Complete Course Transcript`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._
- **What is the exact relationship between `README.md` and `docs/agents/domain.md`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Inference Compute` and `Databricks`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `EPUB / Delivery` and `send-file`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `LinkHTMLParser` connect `clean_text` to `Email Brief Composition`, `Markdown to HTML`, `podsum_email_summary.py`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `MarkdownProcessor` connect `Markdown to HTML` to `Minimal EPUB Writer`, `Markdown Chapter Parsing`, `EPUB Metadata Models`, `clean_text`, `EPUB Generation`, `Markdown Header Model`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `write_epub()` connect `Transcript Text Cleaning` to `Minimal EPUB Writer`, `RuntimeError`?**
  _High betweenness centrality (0.093) - this node is a cross-community bridge._