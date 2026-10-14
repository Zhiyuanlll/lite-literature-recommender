# Quick Start — v2.2.1

Download Lite_Recommender_v2.2.1.zip from Releases. Install Python 3.10+. Run the platform launcher under scripts, preferably with a separate workspace. Open http://127.0.0.1:8765 and keep the process running. Basic mode uses only the Python standard library; no ChatGPT account, subscription or server is required.

Create and enable your research topics. Configure core and related counts per topic (defaults 5+15) and optionally enable weekly scheduling in Settings.

The three actions are Next-week preview (new papers, read-only), Reselect this week's recommendations (replace an unsatisfactory list, scoreable, no implicit negative feedback), and Accelerate after rating (requires saved ratings for every paper in the selected topics' current list; new papers can be rated for future weeks).

All displayed papers, including previews, are excluded from later recommendations in that topic. Preview uses already published papers; it does not predict future publications. Retrieval expands automatically; no manual page button or fixed 200-candidate setting is exposed. Insufficient eligible papers are reported rather than invented.

Save all feedback saves edited ratings/reasons/notes/status/tags, including drafts hidden by topic filters. Unedited papers are skipped and failed saves retain their drafts. PDF UI is frozen; export BibTeX, JSON or Markdown. Optional local CPU semantic ranking is documented separately.
