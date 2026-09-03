# Official corpus reference

## Privacy boundary

The corpus is a private local SQLite database under the user's application-data cache. It may contain exact symbolic gameplay extracted from owned first-party bundles. Never package, upload, quote in bulk, or redistribute the database, raw bundle bytes, audio, TextAssets, or event sequences. The distributable skill contains only extraction code, schemas, aggregate logic, and tests that locate local fixtures.

## Discovery and joins

`official_corpus.py` discovers candidate level bundles in `BeatmapLevelsData` and `OC_ASSET_FILES`, plus `*_pack_assets_all_*.bundle` pack definitions from the Addressables directory.

Modern official content is split across sources:

- Level bundles contain compressed v4 gameplay/lightshow TextAssets, audio metadata, audio clips, and a level-data MonoBehaviour with Unity object references.
- Pack-definition bundles contain catalog-visible level ID, song title/subtitle, artist, mapper, BPM, duration, preview settings, NJS, spawn offsets, counts, pack ID/name/order, and difficulty previews.

The importer joins pack metadata to a level bundle by level ID, then resolves each gameplay and lightshow reference by Unity path ID. v2, v3, and v4 gameplay normalize into one event representation without mixing characteristics.

## Coverage rules

Every discovered candidate must have one of these states:

- `indexed`: metadata and at least one referenced difficulty resolved.
- `excluded`: known non-playable/internal fixture with a recorded reason.
- `failed`: parse or dependency failure. This blocks premium generation.

`artteamtest`, `metronome`, and `performancetest` are indexed as excluded fixtures. They are not style examples.

Run `report` and inspect bundle, pack-definition, source, format, characteristic, difficulty, and failure coverage. Re-run after game updates or DLC changes. The library fingerprint detects inventory changes; content and pack hashes limit reprocessing to affected song bundles.

## Learned local data

The database retains local events plus derived:

- 16-beat density envelopes and NPS;
- per-hand same-hand gaps, recovery seconds, reach distances, position transitions, direction transitions, and likely parity breaks;
- arc and chain duration/lifecycle summaries;
- bomb-to-note separation and wall occupancy grammar;
- lighting event density;
- Easy-to-Expert+ count/NPS/object transformation ratios by characteristic;
- pack, era, and global profiles;
- an official-distance quality ranker with hard synthetic-negative penalties.

Pack order supplies a recency weight, so recent conventions are preferred while older and genre-specific packs remain retrievable. Retrieval filters characteristic and difficulty, then scores tempo and profile similarity with recency weighting. Exact official sequences are never emitted as templates.

## Leave-one-song-out evaluation

For a held-out song, analyze its audio without querying its difficulty events. Compare the generated candidate only after generation using timing residuals, section density, movement distributions, lifecycle summaries, and difficulty transformations. Record aggregate errors, not copied phrases. Hard safety validation always outranks corpus similarity.
