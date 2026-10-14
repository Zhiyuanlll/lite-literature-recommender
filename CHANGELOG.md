# Changelog

## 2.2.1

- Keep the previous scoreable batch accessible when upgrading from legacy previews; require a rated current batch for every selected direction.
- Three explicit weekly workflows: read-only next-week preview, reselect current week, and rated-batch acceleration. All workflows avoid previously displayed papers, including previews.
- Enforce completed saved ratings before acceleration and refuse preview feedback. Reselect preserves active feedback policy and replaces the week's history slot without implicit negative ratings.
- Per-topic core/related targets default to5+15. Expand retrieval automatically, retain title-only relevant papers, and verify public-homepage abstract metadata. Remove manual pagination from the UI.
- Save all edited feedback, including drafts hidden by filters; preserve failed saves for retry and skip unchanged papers.
- Center the reading workspace at150% scale; freeze/archive the PDF workflow and remove its frontend controls. Preserve optional local semantic ranking, personal data and old CLI compatibility.

# v2.1.1 — Cross-platform compatibility

- Recognize PDF keyword headings when text extraction collapses line breaks.
- Compare resolved model directories across Windows short names and macOS aliases.
- Test early HTTP rejection without relying on OS-specific behavior for unread oversized bodies.
- Includes all v2.1.0 concept, semantic and institution-browser improvements.

# v2.1.0 — Broader recall, inspectable candidates and optional local semantics

- Add core concept groups with alternative expressions; keep every group represented in query plans and distinguish core coverage from adjacent exploration.
- Make method matching a preference bonus rather than a mandatory gate, and use bounded effective feedback in score eligibility.
- Set an independent candidate target of 200 per topic/page, adjustable up to1000; continue source pagination through quota-free previews using the parent batch’s query configuration.
- Retain title-only records in a pending-verification panel rather than dropping them silently. Show per-topic retrieval/filter counts and candidate rejection reasons.
- Label core and exploration recommendations. Allow PDF import and manually confirmed abstracts for pending candidates without rewriting historical snapshots.
- Add optional local Sentence Transformers ranking, CPU inference, explicit model preparation and visible rule-mode fallback. Base installation remains dependency-free; semantic dependencies/models are optional and are not bundled in source releases.
- Preserve per-topic weekly quotas, Elsevier authorized API enrichment/retrieval, institutional-library links, optional external Sci-Hub DOI navigation and local PDF import from v2.0.5. External navigation does not automatically fetch third-party PDFs or abstracts.
- Expand installation, migration, algorithm and optional-model documentation for independent local users.

# v2.0.5 — Per-topic quotas and optional publisher access

- Count weekly quotas independently per topic, default 10 each; show each topic's usage.
- Keep form dialogs open when clicking the backdrop.
- Add optional Elsevier API abstract enrichment (before ranking, up to20 attempts per topic/run) and authorized PDF retrieval with public-link fallback.
- Keep API credentials in service environment; no credential redirects, browser passwords or cookies.
- Add configured institutional-library and optional Sci-Hub external DOI navigation links. External links do not automatically fetch content.
- Import user-supplied PDFs, optionally extract abstract candidates using Poppler, and require manual confirmation before updating future metadata. Historical recommendations stay frozen.

# v2.0.4 — GitHub stable release

- Prepare a clean repository, contributor and security guidance, and reproducible source/wheel release checks.
- Add cross-platform CI and a macOS launcher; report the installed version consistently.
- Make ISO-week checks portable across Windows, macOS and Linux; reject reserved Windows directory names.
- Refuse empty PDF archives when files have disappeared; write valid archives atomically.
- Clarify no-account local use, preview, adjustable budget, PDF export and data-preserving upgrades.

# v2.0.3

- Fake-IP范围识别与经TLS认证的公网DNS回退，仍只连接真实公网地址。
- PDF逐篇失败原因在页面和导出报告中显示；全部失败时不生成空ZIP。

# v2.0.2

- 网页新增公开论文PDF批量下载与ZIP导出，不要求评分，支持真实预览。
- 异步下载、逐篇结果报告、已有PDF复用；不会把网页或摘要替代全文。

# v2.0.1

- 推荐上限默认10，允许用户设置更高正整数。
- 新增真实检索预览：独立批次、不占周配额、不改变正式去重、不更新反馈策略。网页与命令行均可使用。
- 来源候选数随本轮预算增加，单方向最多1000个候选。

# Changelog

## 2.0.0 — 2026-10-14

- Independent, standard-library implementation for Python 3.10+.
- Topic-specific profiles, ratings and reading status; shared paper identity.
- Explainable recommendation scores and bounded preference adjustments.
- Latest-feedback recomputation, feedback revisions and withdrawal.
- Cross-topic recommendation budget, DOI/title identity and delivery history.
- Local browser interface, command line, explicit synthetic demonstration mode.
- Public scholarly source retrieval with visible source errors and evidence level.
- Local weekly scheduling while the service is running; optional OS startup scripts.
- BibTeX/JSON/Markdown export, SQLite backup and legacy import audit.
- Optional, user-selected public HTTPS PDF saving with redirect/IP checks.
- Neutral sample topics and private-data-free source ZIP release tooling.

This version does not claim full-text reading, automated novelty verification,
or completion of a research gap. Candidate questions come from the topic profile
and retain their evidence limitations.
