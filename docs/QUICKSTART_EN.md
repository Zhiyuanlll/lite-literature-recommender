# Lite Recommender v2 — Quick Start

A local literature recommendation and feedback application for any research topic.
Python 3.10+ is required. Runtime code uses only the Python standard library; no
package installation, language-model key, or continuously running Codex is needed.

## Start

Extract the source ZIP into a stable folder. On Linux:

```bash
bash scripts/run_linux.sh
```

On macOS, double-click `scripts/run_macos.command`, or run `bash scripts/run_linux.sh` in Terminal.

On Windows, double-click `scripts/run_windows.cmd`. Alternatively, from the
extracted project folder:

```bash
python -m lite_recommender init
python -m lite_recommender serve --open
```

Open `http://127.0.0.1:8765`. The server must keep running. The default personal
workspace is `./workspace` within the project when using the launchers. CLI
commands resolve a relative workspace from the current working directory.

For a separate workspace, put the global option before the command:

```bash
python -m lite_recommender --workspace '/your/research/data' init
python -m lite_recommender --workspace '/your/research/data' serve --open
```

The launchers locate the source folder regardless of the caller's working
directory. Linux: `bash /path/to/project/scripts/run_linux.sh '/your/data'`.
Windows: `scripts\run_windows.cmd -Workspace 'D:\Research\Literature'`.

## Choose your own topic

Ordinary initialization starts with no topics. In the local page, add a topic with
a stable ID, title, group, research questions, English search keywords, method
terms, exclusions and a stage: `survey`, `gap` or `focus`. Enable the topic when
ready for real retrieval.

Optional, disabled examples demonstrate materials mechanics, DDD, medical imaging
and robotics:

```bash
python -m lite_recommender init --examples
```

Try synthetic demonstration mode first. Every demonstration paper is explicitly
marked; demo feedback and budget are separate from real retrieval:

```bash
python -m lite_recommender run --topic medical_imaging --demo --run-id demo-001
```

Real retrieval uses public Crossref, OpenAlex and arXiv metadata. Individual source
failures are visible. Network failure never silently substitutes synthetic papers.
The default weekly global budget is 10 recommendations across all topics and runs.
Set a higher positive integer in Settings if needed. Real search preview uses the
configured sources without consuming the weekly budget or changing formal delivery
history; it may include previously recommended papers. Fewer papers are returned
when eligibility is not met.

## Rate and iterate

Use 1–5 stars for relevance to the selected research question. Reading progress,
rating and reason are independent. Choose explicit keyword/method tags to attribute
feedback. No-time, difficulty and access reasons do not reduce topic interest.

Repeated identical feedback does not accumulate interest. Updates and withdrawals
use the latest effective feedback within the topic/profile version. No rating means
unknown, not negative. At least three rated papers and two attributed examples of
the same tag are required before a bounded adjustment is applied.

The baseline score is rule-based title/abstract matching, not a quality probability
or semantic reading model. Reports distinguish abstract evidence from metadata.
Candidate questions come from your profile and require evidence checking; the
application does not perform full-text review or establish novelty/research gaps.

## Export, back up and verify

```bash
python -m lite_recommender export --run-id RUN_ID --format bib --output weekly.bib
python -m lite_recommender export --run-id RUN_ID --format json --output weekly.json
python -m lite_recommender download --run-id RUN_ID --min-rating 4
python -m lite_recommender backup
python -m lite_recommender verify
```

Use the actual run ID shown in the page. Backups contain personal data and should
not be published. The SQLite database under `_shared/library.sqlite` is the state
source; profile files are portable configuration, and legacy JSONL is not a second
automatically synchronized live database.

In the web interface, select a real or preview batch, then Export → Download paper
PDFs (ZIP). This explicitly selects all papers without requiring ratings. Successful
public PDF files are exported with an itemized report; if none are available, no
empty ZIP is downloaded. Itemized failure reasons stay visible on the page.
The optional CLI download command selects real papers rated at least 4, or marked cited, in that run. It uses public HTTPS links from the snapshot, never account cookies or login
bypass. Downloads are limited to 25 MiB with 15-second network timeouts; failures
are reported independently. Fake-IP DNS in 198.18.0.0/15 uses authenticated
Cloudflare DNS-over-HTTPS to obtain verified public addresses; only the publisher
hostname is sent to the resolver. Private addresses and invalid certificates remain
blocked. No ChatGPT account, API key or language model is used for ranking or feedback.
PDFs are not included in the default backup, and saving
a PDF does not mean the software has read its full text.

Offline verification uses temporary data and synthetic metadata. It does not prove
that external APIs are currently available. Perform a separate real run and review
source warnings and paper links.

## Scheduling

Weekly scheduling starts disabled. Enable it on the local page and keep the server
running. The default timezone is Asia/Shanghai. There is no cloud execution or
automatic email/chat delivery. Optional Linux user-service and Windows logon-task
scripts are included; they preview by default and require an explicit install flag.
They start the server rather than installing a second weekly scheduler.
Failed scheduled runs retry after at least 15 minutes, with at most three total
attempts including the initial execution. Completed/partial runs are retained.

## Share the software

MIT-licensed. Build a clean source ZIP:

```bash
python scripts/build_release.py
```

The allowlist includes code, static assets, documentation, neutral examples and
tests. It excludes personal workspaces, databases, feedback, caches, PDFs, credentials
and backups. Verify the ZIP SHA-256 before extraction, then run
`python scripts/verify_release.py` in the extracted folder.

The interface binds only to localhost and is intended for one local user. Publishing
source for independent local installations is supported; public multi-user hosting
is not part of this release. The software license does not extend to retrieved
article content.

Detailed Chinese documentation is linked from [README](../README.md).
