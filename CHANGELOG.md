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
