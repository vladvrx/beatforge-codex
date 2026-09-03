"""Download every map from every curated+verified BeatSaver playlist."""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "corpus"
API = "https://api.beatsaver.com"
CDN_MIN_INTERVAL = 0.12
API_MIN_INTERVAL = 0.35
USER_AGENT = "Beatforge/0.1 (corpus research; curated playlist study)"
DEFAULT_MIN_SCORE = 0.85

sys.path.insert(0, str(Path(__file__).resolve().parent))

KEEP_NAMES = {"info.dat"}
SKIP_SUFFIXES = {
    ".ogg",
    ".egg",
    ".mp3",
    ".wav",
    ".flac",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".exr",
}


def _now() -> float:
    return time.monotonic()


class RateClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=20.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self._next_api = 0.0
        self._next_cdn = 0.0

    def close(self) -> None:
        self._client.close()

    def _sleep_until(self, deadline: float) -> None:
        wait = deadline - _now()
        if wait > 0:
            time.sleep(wait)

    def get_json(self, url: str, retries: int = 8) -> Any:
        last_err: Exception | None = None
        for attempt in range(retries):
            self._sleep_until(self._next_api)
            try:
                r = self._client.get(url)
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(min(30, 1.5 ** attempt))
                continue
            self._next_api = _now() + API_MIN_INTERVAL
            if r.status_code == 429:
                retry = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(90, max(retry, 1.0)))
                continue
            if r.status_code >= 500:
                time.sleep(min(30, 1.5 ** attempt))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET JSON failed {url}: {last_err}")

    def get_bytes(self, url: str, retries: int = 6) -> bytes | None:
        last_err: Exception | None = None
        for attempt in range(retries):
            self._sleep_until(self._next_cdn)
            try:
                r = self._client.get(url)
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(min(30, 1.5 ** attempt))
                continue
            self._next_cdn = _now() + CDN_MIN_INTERVAL
            if r.status_code == 429:
                retry = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(90, max(retry, 1.0)))
                continue
            if r.status_code in (404, 410):
                return None
            if r.status_code >= 500:
                time.sleep(min(30, 1.5 ** attempt))
                continue
            r.raise_for_status()
            return r.content
        print(f"  download failed {url}: {last_err}", file=sys.stderr)
        return None


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    import os
    import threading

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def enumerate_playlists(client: RateClient) -> list[dict[str, Any]]:
    playlists: list[dict[str, Any]] = []
    page = 0
    total_pages = None
    while True:
        url = f"{API}/playlists/search/{page}?order=Curated&verified=true"
        data = client.get_json(url)
        if not data:
            break
        docs = data.get("docs") or []
        info = data.get("info") or {}
        total_pages = info.get("pages")
        if not docs:
            break
        for p in docs:
            playlists.append(
                {
                    "id": p.get("playlistId"),
                    "name": p.get("name"),
                    "owner": (p.get("owner") or {}).get("name"),
                    "curator": (p.get("curator") or {}).get("name"),
                    "description": p.get("description") or "",
                    "totalMaps": (p.get("stats") or {}).get("totalMaps"),
                    "curatedAt": p.get("curatedAt"),
                    "tags": p.get("tags") or [],
                }
            )
        print(
            f"  playlists page {page + 1}/{total_pages or '?'} "
            f"(+{len(docs)}, total {len(playlists)})"
        )
        page += 1
        if total_pages is not None and page >= int(total_pages):
            break
    return playlists


def enumerate_playlist_maps(client: RateClient, playlist_id: int) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    page = 0
    while True:
        url = f"{API}/playlists/id/{playlist_id}/{page}"
        data = client.get_json(url)
        if not data:
            break
        batch = data.get("maps") or []
        if not batch:
            break
        maps.extend(batch)
        page += 1
        if len(batch) < 20:
            break
    return maps


def _rating_score(map_data: dict[str, Any]) -> float | None:
    """Return BeatSaver's normalized community score, or None when unrated."""

    raw = (map_data.get("stats") or {}).get("score")
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if score > 1.0:
        score /= 100.0
    return score if 0.0 <= score <= 1.0 else None


def slim_map(
    entry: dict[str, Any],
    playlist: dict[str, Any],
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any] | None:
    m = entry.get("map") or entry
    if not m or m.get("automapper"):
        return None
    score = _rating_score(m)
    if score is None or score < min_score:
        return None
    versions = m.get("versions") or []
    published = [v for v in versions if v.get("state") == "Published"] or versions
    if not published:
        return None
    ver = published[-1]
    mid = str(m.get("id") or "")
    if not mid:
        return None
    dl = ver.get("downloadURL")
    if not dl:
        return None
    diffs = []
    for d in ver.get("diffs") or []:
        diffs.append(
            {
                "characteristic": d.get("characteristic"),
                "difficulty": d.get("difficulty"),
                "nps": d.get("nps"),
                "notes": d.get("notes"),
                "njs": d.get("njs"),
            }
        )
    return {
        "id": mid,
        "hash": ver.get("hash"),
        "name": m.get("name"),
        "uploader": (m.get("uploader") or {}).get("name"),
        "bpm": (m.get("metadata") or {}).get("bpm"),
        "duration": (m.get("metadata") or {}).get("duration"),
        "tags": m.get("tags") or [],
        "automapper": bool(m.get("automapper")),
        "declaredAi": m.get("declaredAi"),
        "rating": round(score, 5),
        "upvotes": int((m.get("stats") or {}).get("upvotes") or 0),
        "downvotes": int((m.get("stats") or {}).get("downvotes") or 0),
        "downloadURL": dl,
        "diffs": diffs,
        "playlists": [
            {
                "id": playlist["id"],
                "name": playlist.get("name"),
                "curator": playlist.get("curator"),
                "owner": playlist.get("owner"),
            }
        ],
    }


def merge_catalog_entry(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    seen = {(p.get("id"), p.get("name")) for p in existing.get("playlists") or []}
    playlists = list(existing.get("playlists") or [])
    for p in incoming.get("playlists") or []:
        key = (p.get("id"), p.get("name"))
        if key not in seen:
            playlists.append(p)
            seen.add(key)
    tags = list(dict.fromkeys((existing.get("tags") or []) + (incoming.get("tags") or [])))
    merged = dict(incoming)
    merged["playlists"] = playlists
    merged["tags"] = tags
    return merged


def build_catalog(
    client: RateClient,
    force: bool,
    min_score: float = DEFAULT_MIN_SCORE,
) -> tuple[dict[str, Any], set[str]]:
    cat_path = CORPUS / "catalog.json"
    pl_path = CORPUS / "playlists.json"
    progress_path = CORPUS / "catalog_progress.json"
    quality_progress_path = CORPUS / "quality_progress.json"
    quality_audit_path = CORPUS / "quality_audit.json"
    old_catalog: dict[str, Any] = load_json(cat_path, {}) if cat_path.is_file() else {}
    done_playlists = (
        set() if force else set(load_json(progress_path, {}).get("done", []))
    )
    quality_progress = (
        {}
        if force
        else load_json(quality_progress_path, {})
    )
    seen_maps = set(quality_progress.get("seen") or [])
    below_score = set(quality_progress.get("below_score") or [])
    unrated = set(quality_progress.get("unrated") or [])
    automappers = set(quality_progress.get("automappers") or [])

    def save_quality_progress() -> None:
        save_json(
            quality_progress_path,
            {
                "seen": sorted(seen_maps),
                "below_score": sorted(below_score),
                "unrated": sorted(unrated),
                "automappers": sorted(automappers),
            },
        )

    if pl_path.is_file() and not force:
        playlists = load_json(pl_path, [])
    else:
        print("Enumerating curated+verified playlists…")
        playlists = enumerate_playlists(client)
        save_json(pl_path, playlists)
        print(f"Found {len(playlists)} playlists")

    legacy_catalog = bool(old_catalog) and any(
        meta.get("rating") is None for meta in old_catalog.values()
    )
    if legacy_catalog and not force:
        print("Cached catalog has no ratings; refreshing it before quality filtering")
        force = True
        done_playlists = set()
    catalog: dict[str, Any] = {} if force else dict(old_catalog)
    if playlists and len(done_playlists) >= len(playlists) and catalog and not force:
        print(f"Loaded catalog: {len(catalog)} maps, {len(playlists)} playlists")
        eligible = {
            mid: meta
            for mid, meta in catalog.items()
            if (_rating_score({"stats": {"score": meta.get("rating")}}) or -1.0)
            >= min_score
        }
        removed = set(catalog) - set(eligible)
        if removed:
            save_json(cat_path, eligible)
        return eligible, removed

    if not playlists:
        print("Enumerating curated+verified playlists…")
        playlists = enumerate_playlists(client)
        save_json(pl_path, playlists)
        print(f"Found {len(playlists)} playlists")

    print(
        f"Building catalog: {len(done_playlists)}/{len(playlists)} playlists done, "
        f"{len(catalog)} maps so far"
    )

    for i, pl in enumerate(playlists):
        pid = pl["id"]
        if pid in done_playlists and not force:
            continue
        print(
            f"[{i + 1}/{len(playlists)}] playlist {pid} {pl.get('name')!r}",
            flush=True,
        )
        try:
            entries = enumerate_playlist_maps(client, int(pid))
        except Exception as e:
            print(f"  failed to list maps: {e}", file=sys.stderr)
            for mid, meta in old_catalog.items():
                memberships = meta.get("playlists") or []
                if any(item.get("id") == pid for item in memberships):
                    catalog.setdefault(mid, meta)
            continue
        added = 0
        for entry in entries:
            raw_map = entry.get("map") or entry
            raw_id = str(raw_map.get("id") or "") if raw_map else ""
            if raw_id:
                seen_maps.add(raw_id)
                if raw_map.get("automapper"):
                    automappers.add(raw_id)
                else:
                    raw_score = _rating_score(raw_map)
                    if raw_score is None:
                        unrated.add(raw_id)
                    elif raw_score < min_score:
                        below_score.add(raw_id)
                    else:
                        below_score.discard(raw_id)
                        unrated.discard(raw_id)
            slim = slim_map(entry, pl, min_score=min_score)
            if not slim:
                continue
            mid = slim["id"]
            if mid in catalog:
                catalog[mid] = merge_catalog_entry(catalog[mid], slim)
            else:
                catalog[mid] = slim
                added += 1
        done_playlists.add(pid)
        save_json(progress_path, {"done": sorted(done_playlists)})
        if i % 5 == 0:
            save_json(cat_path, catalog)
            save_quality_progress()
        print(f"  maps on playlist: {len(entries)}  new unique: {added}  catalog: {len(catalog)}")

    save_json(cat_path, catalog)
    save_json(progress_path, {"done": sorted(done_playlists)})
    below_score.difference_update(catalog)
    unrated.difference_update(catalog)
    automappers.difference_update(catalog)
    save_quality_progress()
    complete = len(done_playlists) >= len(playlists)
    removed = set(old_catalog) - set(catalog) if complete else set()
    audit = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "BeatSaver curated and verified playlists",
        "minimum_score": min_score,
        "complete": complete,
        "playlists_total": len(playlists),
        "playlists_processed": len(done_playlists),
        "unique_maps_examined": len(seen_maps),
        "accepted": len(catalog),
        "rejected_below_score": len(below_score),
        "rejected_unrated": len(unrated),
        "rejected_automappers": len(automappers),
        "below_score_ids": sorted(below_score),
        "unrated_ids": sorted(unrated),
        "automapper_ids": sorted(automappers),
    }
    save_json(quality_audit_path, audit)
    state = "complete" if complete else "checkpointed"
    print(
        f"Catalog {state}: {len(catalog)} maps rated >= {min_score:.0%}; "
        f"{len(below_score)} below score, {len(unrated)} unrated, "
        f"{len(automappers)} automappers"
    )
    return catalog, removed


def prune_removed_maps(removed: set[str]) -> dict[str, int]:
    """Delete exact corpus artifacts for map ids removed by the rating gate."""

    maps_dir = (CORPUS / "maps").resolve()
    fp_dir = (CORPUS / "fingerprints").resolve()
    removed_maps = 0
    removed_fingerprints = 0
    for mid in sorted(removed):
        if not mid or Path(mid).name != mid:
            continue
        map_dir = (maps_dir / mid).resolve()
        if map_dir.parent == maps_dir and map_dir.is_dir():
            shutil.rmtree(map_dir)
            removed_maps += 1
        fp = (fp_dir / f"{mid}.json").resolve()
        if fp.parent == fp_dir and fp.is_file():
            fp.unlink()
            removed_fingerprints += 1

    state_path = CORPUS / "download_state.json"
    if state_path.is_file():
        state = load_json(state_path, {})
        for key in ("done", "failed", "remaining"):
            state[key] = [mid for mid in state.get(key) or [] if mid not in removed]
        save_json(state_path, state)

    for derived in (
        "nn_index.json",
        "style_family_medians.json",
        "style_recommendations.json",
    ):
        path = CORPUS / derived
        if removed and path.is_file():
            path.unlink()

    return {
        "catalog_entries": len(removed),
        "map_directories": removed_maps,
        "fingerprints": removed_fingerprints,
    }


def extract_map_zip(blob: bytes, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return False
    info_name = None
    for name in zf.namelist():
        base = name.replace("\\", "/").split("/")[-1]
        if base.lower() == "info.dat" and not name.startswith("__MACOSX"):
            info_name = name.replace("\\", "/")
            break
    if info_name is None:
        return False
    if "/" in info_name:
        prefix = info_name.rsplit("/", 1)[0] + "/"
    else:
        prefix = ""
    wrote = False
    for name in zf.namelist():
        norm = name.replace("\\", "/")
        if norm.endswith("/") or norm.startswith("__MACOSX"):
            continue
        if prefix and not norm.startswith(prefix):
            continue
        rest = norm[len(prefix) :]
        if "/" in rest:
            continue
        lower = rest.lower()
        suffix = Path(lower).suffix
        if suffix in SKIP_SUFFIXES:
            continue
        if lower != "info.dat" and suffix != ".dat":
            continue
        target = dest / rest
        try:
            target.write_bytes(zf.read(name))
            wrote = True
        except Exception:
            continue
    return wrote


def _cdn_get_bytes(url: str, retries: int = 6) -> bytes | None:
    headers = {"User-Agent": USER_AGENT}
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90.0, connect=20.0),
                headers=headers,
                follow_redirects=True,
            ) as c:
                r = c.get(url)
        except httpx.HTTPError as e:
            last_err = e
            time.sleep(min(30, 1.5 ** attempt))
            continue
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(90, max(retry, 1.0)))
            continue
        if r.status_code in (404, 410):
            return None
        if r.status_code >= 500:
            time.sleep(min(30, 1.5 ** attempt))
            continue
        r.raise_for_status()
        return r.content
    print(f"  download failed {url}: {last_err}", file=sys.stderr)
    return None


def _fingerprint_extracted(mid: str, dest: Path, meta: dict[str, Any], fp_dir: Path) -> None:
    from fingerprint import fingerprint_map
    from parse_maps import parse_map_folder

    (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        parsed = parse_map_folder(dest, map_id=mid)
        if parsed:
            rows = fingerprint_map(parsed, meta)
            save_json(fp_dir / f"{mid}.json", rows)
    except Exception as e:
        print(f"  fingerprint failed {mid}: {e}", file=sys.stderr)


def download_maps(client: RateClient, catalog: dict[str, Any], workers: int = 6) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    maps_dir = CORPUS / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    state_path = CORPUS / "download_state.json"
    state = load_json(state_path, {"done": [], "failed": [], "remaining": []})
    done = set(state.get("done") or [])
    failed = set(state.get("failed") or [])

    remaining = [mid for mid in catalog if mid not in done]
    maps_dir.mkdir(parents=True, exist_ok=True)
    for dest in maps_dir.iterdir():
        if not dest.is_dir():
            continue
        if any(p.is_file() and p.name.lower() == "info.dat" for p in dest.rglob("*")):
            done.add(dest.name)
            failed.discard(dest.name)
    remaining = [mid for mid in catalog if mid not in done]
    save_json(
        state_path,
        {"done": sorted(done), "failed": sorted(failed), "remaining": remaining},
    )
    print(f"Downloads: {len(done)} done, {len(remaining)} remaining, {len(failed)} failed")

    fp_dir = CORPUS / "fingerprints"
    fp_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    processed = [0]

    def persist() -> None:
        left = [m for m in remaining if m not in done and m not in failed]
        save_json(
            state_path,
            {"done": sorted(done), "failed": sorted(failed), "remaining": left},
        )

    def work(mid: str) -> tuple[str, bool]:
        meta = catalog[mid]
        dest = maps_dir / mid
        info_ok = dest.is_dir() and any(
            p.is_file() and p.name.lower() == "info.dat" for p in dest.rglob("*")
        )
        if not info_ok:
            url = meta.get("downloadURL")
            blob = _cdn_get_bytes(url) if url else None
            if not blob or not extract_map_zip(blob, dest):
                return mid, False
        _fingerprint_extracted(mid, dest, meta, fp_dir)
        return mid, True

    need_net: list[str] = []
    for mid in remaining:
        dest = maps_dir / mid
        if dest.is_dir() and any(
            p.is_file() and p.name.lower() == "info.dat" for p in dest.rglob("*")
        ):
            print(f"fingerprint on disk {mid}")
            _fingerprint_extracted(mid, dest, catalog[mid], fp_dir)
            done.add(mid)
            failed.discard(mid)
        else:
            need_net.append(mid)
    persist()
    print(f"Need zip download: {len(need_net)}")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(work, mid): mid for mid in need_net}
        for fut in as_completed(futs):
            mid = futs[fut]
            ok = False
            try:
                _mid, ok = fut.result()
            except Exception as e:
                print(f"  worker error {mid}: {e}", file=sys.stderr)
            with lock:
                processed[0] += 1
                n = processed[0]
                if ok:
                    done.add(mid)
                    failed.discard(mid)
                else:
                    failed.add(mid)
                name = (catalog.get(mid) or {}).get("name")
                print(f"[{n}/{len(need_net)}] {'ok' if ok else 'FAIL'} {mid} {name!r}")
                if n % 10 == 0 or n == len(need_net):
                    persist()
    persist()
    _ = client


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Fetch ALL curated+verified BeatSaver playlists")
    p.add_argument("--refresh-catalog", action="store_true")
    p.add_argument(
        "--catalog-only",
        action="store_true",
        help="Enumerate playlists/maps but do not download zips",
    )
    p.add_argument("--workers", type=int, default=6, help="Parallel zip downloads")
    p.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help="Minimum BeatSaver community rating as a fraction (default: 0.85)",
    )
    args = p.parse_args(argv)
    if not 0.0 <= args.min_score <= 1.0:
        p.error("--min-score must be between 0 and 1")
    CORPUS.mkdir(parents=True, exist_ok=True)
    client = RateClient()
    try:
        catalog, removed = build_catalog(
            client,
            force=args.refresh_catalog,
            min_score=args.min_score,
        )
        pruned = prune_removed_maps(removed)
        if removed:
            print(
                "Pruned low-rated corpus data: "
                f"{pruned['catalog_entries']} catalog entries, "
                f"{pruned['map_directories']} map folders, "
                f"{pruned['fingerprints']} fingerprints"
            )
        save_json(CORPUS / "membership.json", {
            mid: meta.get("playlists") or [] for mid, meta in catalog.items()
        })
        if not args.catalog_only:
            download_maps(client, catalog, workers=args.workers)
    finally:
        client.close()
    print("Fetch complete (or checkpointed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
