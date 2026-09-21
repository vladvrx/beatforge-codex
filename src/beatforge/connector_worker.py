"""Outbound-only local generation worker. Never installs maps or launches the game."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from beatforge.connector_jobs import GenerationRequest
from beatforge.connector_export import prepare_artifacts
from beatforge.premium import PipelineCancelled, run_premium_pipeline

log = logging.getLogger(__name__)


def transient_http(error: httpx.HTTPError) -> bool:
    return isinstance(error, httpx.TransportError) or (
        isinstance(error, httpx.HTTPStatusError) and
        (error.response.status_code in {408, 425, 429} or error.response.status_code >= 500))


def validate_gateway(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in {'', '/'} or not parsed.hostname or
            (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}))):
        raise ValueError('Use an HTTPS gateway origin, or HTTP on localhost for development')
    return url.rstrip('/')


def run_once(client: httpx.Client, root: Path) -> bool:
    response = client.post('/api/worker/claim')
    response.raise_for_status()
    job = response.json()['job']
    if not job:
        return False
    job_id = job['id']
    if not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job identifier')
    revision = job['request'].get('revision')
    request = GenerationRequest.model_validate({**{key: value for key, value in job['request'].items() if key != 'revision'}, 'idempotencyKey': 'worker'})
    if revision:
        from beatforge.connector_jobs import SectionRevision
        if not re.fullmatch('[a-f0-9]{32}', revision.get('parentJob', '')):
            raise ValueError('Invalid parent job identifier')
        SectionRevision.model_validate({**{key: value for key, value in revision.items() if key != 'parentJob'}, 'idempotencyKey': 'worker'})
    lease_headers = {'X-BeatForge-Lease': job['leaseToken']}
    # Each lease has a distinct output directory, including recovered attempts.
    folder = root.resolve() / job_id / hashlib.sha256(job['leaseToken'].encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    cancelled, finished = threading.Event(), threading.Event()

    def heartbeat():
        while not finished.wait(15):
            try:
                reply = client.post(f'/api/worker/jobs/{job_id}/heartbeat', headers=lease_headers)
                reply.raise_for_status()
            except httpx.HTTPError:
                # Stop expensive computation if lease ownership cannot be confirmed.
                cancelled.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if revision:
            from beatforge.connector_revision import run_revision
            result = run_revision(root, folder/'map', revision, cancelled.is_set)
        else:
            suffix = Path(job['upload']['filename']).suffix.lower()
            if suffix not in {'.wav', '.ogg', '.mp3', '.flac', '.m4a', '.mp4'}:
                raise ValueError('Unsupported audio type')
            audio = folder / ('input' + suffix)
            digest, size = hashlib.sha256(), 0
            with client.stream('GET', f'/api/worker/jobs/{job_id}/audio', headers=lease_headers) as reply:
                reply.raise_for_status()
                with audio.open('wb') as output:
                    for chunk in reply.iter_bytes():
                        size += len(chunk)
                        if cancelled.is_set() or size > min(job['upload']['size'], 64*1024*1024):
                            raise PipelineCancelled('Download stopped')
                        output.write(chunk)
                        digest.update(chunk)
            if size != job['upload']['size'] or digest.hexdigest() != job['upload']['sha256']:
                raise ValueError('Audio integrity check failed')
            result = run_premium_pipeline(audio=audio, output=folder/'map', title=request.title,
                                          artist=request.artist, mapper=request.mapper, seed=request.seed,
                                          anchors=None, palette=None, progress=lambda *args, **kwargs: None,
                                          allow_unconfirmed=request.allowUnconfirmed,
                                          difficulties=request.difficulties, mapping_plan=request.mappingPlan,
                                          cancel_requested=cancelled.is_set)
        (folder/'pipeline-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        status = result['status']
        if status == 'invalid' and not (folder/'map/_beatforge/qa_report.json').is_file():
            status = 'error'
        if status == 'playtest_candidate':
            outputs = prepare_artifacts(folder/'map', folder/'publish', request.mappingPlan)
            for name, path in outputs.items():
                if cancelled.is_set():
                    raise PipelineCancelled('Lease lost before artifact upload')
                with path.open('rb') as content:
                    reply = client.put(f'/api/worker/jobs/{job_id}/artifacts/{name}',
                                       headers={**lease_headers, 'Content-Length': str(path.stat().st_size),
                                                'Content-Type': 'application/octet-stream'}, content=content)
                    reply.raise_for_status()
        message = {'playtest_candidate': 'Map and review artifacts are ready. Human playtesting is still required.',
                   'needs_anchors': 'Timing anchors are required before generation can continue.',
                   'needs_palette': 'The mapping pipeline requires palette approval.',
                   'corpus_incomplete': 'The worker needs its official mapping corpus configured.',
                   'invalid': 'The generated map did not pass structural validation.',
                   'error': 'The worker could not generate this map. Inspect its local output.'}[status]
        reply = client.post(f'/api/worker/jobs/{job_id}/finish', headers=lease_headers,
                            json={'status': status, 'message': message, 'humanPlaytestRequired': True})
        reply.raise_for_status()
    except PipelineCancelled:
        # A lost/cancelled lease must not publish a late result.
        pass
    except Exception as error:
        if isinstance(error, httpx.HTTPError) and transient_http(error):
            # A timed-out finish might already have committed. Do not replace a
            # successful result with an error, or consume retryable work. The
            # gateway either retains completion or reclaims the expired lease.
            raise
        if not cancelled.is_set():
            reply = client.post(f'/api/worker/jobs/{job_id}/finish', headers=lease_headers,
                                json={'status': 'error', 'message': 'Worker failed. Check local setup and audio format.',
                                      'humanPlaytestRequired': True})
            reply.raise_for_status()
        raise
    finally:
        finished.set()
        thread.join(timeout=20)
    return True


def serve(client: httpx.Client, root: Path, *, once: bool = False, sleep=time.sleep):
    """Keep polling after transient outages; terminal auth/configuration errors stop."""
    failures = 0
    while True:
        try:
            worked = run_once(client, root)
            failures = 0
        except httpx.HTTPError as error:
            if once or not transient_http(error):
                raise
            failures += 1
            delay = min(60, 2 ** min(failures, 6))
            # Exception text can contain private URLs. Emit only safe status.
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else 'network'
            log.warning('Gateway unavailable (%s); reconnecting in %s seconds', status, delay)
            sleep(delay)
            continue
        except Exception:
            if once:
                raise
            log.error('Job failed; inspect local worker output. Continuing in 5 seconds.')
            sleep(5)
            continue
        if once:
            return
        if not worked:
            sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--work-dir', type=Path, default=Path('data/worker'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    gateway = validate_gateway(args.gateway)
    token = os.environ['BEATFORGE_WORKER_TOKEN']
    with httpx.Client(base_url=gateway, headers={'Authorization': f'Bearer {token}'}, timeout=20,
                      follow_redirects=False) as client:
        serve(client, args.work_dir, once=args.once)


if __name__ == '__main__':
    main()
