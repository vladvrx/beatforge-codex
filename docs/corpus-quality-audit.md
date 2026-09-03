# Corpus quality audit

Completed 2026-08-21 against every map referenced by BeatSaver's 919 curated
and verified playlists.

- Unique maps examined: **19,487**
- Accepted at a BeatSaver community score of at least 85%: **13,065**
- Rejected below 85%: **6,370**
- Rejected automapper entries: **52**
- Rejected unrated entries: **0**
- Lowest score remaining in `catalog.json`: **85%**

The audit checked that all 919 playlists completed, every accepted catalog
entry has `rating >= 0.85`, no rejected ID remains in the catalog, and
`membership.json` contains exactly the same 13,065 IDs. Operational corpus
files remain gitignored because the complete downloaded dataset is large; the
quality gate and reproducible audit logic live in `tools/corpus/fetch_beatsaver.py`.
