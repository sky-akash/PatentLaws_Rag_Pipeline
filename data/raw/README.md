# Source pages (EPO)

Trimmed copies of the legal texts published by the European Patent Office, kept so the index can be rebuilt
without downloading again and so every answer can be traced to the exact text it was built from.

| Folder | Source | Retrieved | Pages |
|---|---|---|---|
| `epc/` | European Patent Convention, Implementing Regulations, Rules relating to Fees, Protocol on Art. 69 — https://www.epo.org/en/legal/epc/2020/index.html | 2026-10-11 (pages dated 2026-05-21) | 372 |
| `guidelines/` | Guidelines for Examination in the EPO, March 2026 — https://www.epo.org/en/legal/guidelines-epc/2026/index.html | 2026-10-11 | 1,858 |

Each file keeps only the page metadata, heading, legal text and footnotes (see `patentlaw/fetch.py: trim`).
`_toc.json` maps each file to its title in the EPO's table of contents.

© European Patent Office. Reproduced for reference and research with attribution; the authoritative versions
are on epo.org. Rebuild with `python -m patentlaw.fetch epc` / `python -m patentlaw.fetch guidelines`.
