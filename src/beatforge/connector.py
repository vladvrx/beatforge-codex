"""Isolated MCP/REST gateway. Never mounts the privileged local Studio API."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import anyio
import jwt
from fastapi import FastAPI, Request, Header
from fastapi.openapi.utils import get_openapi
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse, FileResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from beatforge.connector_auth import OAuthSettings, OAuthVerifier, Principal
from beatforge.connector_store import ConnectorStore
from beatforge.connector_uploads import UploadRequest, Uploads
from beatforge.connector_jobs import GenerationRequest, WorkerResult, SectionRevision
from beatforge.connector_artifacts import Artifacts
from beatforge.connector_logging import protect_download_logs
from beatforge.connector_limits import bounded_request
from beatforge.connector_editorial import Editorial, ConnectorFeedback, ConnectorComparison, PresetRequest, SuggestionsRequest
from beatforge.mapping_plan import normalize_mapping_plan


principal: ContextVar[Principal | None] = ContextVar('beatforge_principal', default=None)


def require(scope: str) -> Principal:
    actor = principal.get()
    if actor is None or scope not in actor.scopes:
        raise PermissionError('This connection does not have the required scope')
    return actor


class Operations:
    def __init__(self, store: ConnectorStore):
        self.store = store
        self.artifacts = Artifacts(store, store.path.parent / 'artifacts')
        self.editorial = Editorial(store, self.artifacts)

    def list_jobs(self, limit: int = 20, offset: int = 0) -> dict:
        """List only this account's saved mapping runs, newest first."""
        return self.store.list_jobs(require('read').owner, limit, offset)

    def get_qa(self, job_id: str) -> dict:
        """Read structural findings. These are not human headset evidence."""
        return self.artifacts.read_json(require('read').owner, job_id, 'qa.json')

    def get_preview(self, job_id: str, difficulty: str = 'Hard') -> dict:
        """Read a chart preview and short-lived audio link for the human editor."""
        owner = require('read').owner
        payload = self.editorial.preview(owner, job_id, difficulty)
        audio = self.artifacts.named(owner, job_id, 'audio.ogg')
        link = self.artifacts.link(owner, job_id, audio['id'])
        return {**payload, 'jobId': job_id, 'audioUrl': link['downloadUrl'], 'audioExpiresAt': link['expiresAt']}

    def get_preview_summary(self, job_id: str, difficulty: str = 'Hard') -> dict:
        """Inspect preview identity and counts without sending the full chart through MCP."""
        payload = self.get_preview(job_id, difficulty)
        return {key: payload[key] for key in ('jobId', 'title', 'artist', 'difficulty', 'difficulties',
                'chartHash', 'audioHash', 'bpm', 'duration', 'timingVerified', 'sections')} | {
                    'noteCount': len(payload['chart'].get('colorNotes', [])),
                    'previewPath': f'/api/jobs/{job_id}/preview?difficulty={difficulty}',
                    'humanPlaytestRequired': True}

    def record_feedback(self, feedback: ConnectorFeedback) -> dict:
        """Record only ratings, tags and notes explicitly supplied by a person. Never invent them."""
        return self.editorial.feedback(require('feedback').owner, feedback)

    def record_comparison(self, comparison: ConnectorComparison) -> dict:
        """Save a person's explicit preference between two charts of the same audio and difficulty."""
        return self.editorial.compare(require('feedback').owner, comparison)

    def save_preset(self, preset: PresetRequest) -> dict:
        """Save or replace this account's named musical style."""
        return self.editorial.preset(require('edit').owner, preset)

    def list_presets(self) -> dict:
        return {'presets': self.editorial.records(require('read').owner, 'preset')}

    def feedback_suggestions(self, request: SuggestionsRequest) -> dict:
        """Suggest bounded changes from explicit feedback on distinct charts. Does not apply or train."""
        return self.editorial.suggestions(require('read').owner, request)

    def learning_summary(self) -> dict:
        owner = require('read').owner
        return {'feedbackCount': len(self.editorial.records(owner, 'feedback')),
                'comparisonCount': len(self.editorial.records(owner, 'comparison')),
                'presetCount': len(self.editorial.records(owner, 'preset')),
                'automaticTraining': False, 'humanPlaytestRequired': True}

    def export_learning(self) -> dict:
        """Export this account's explicit evidence and presets for local evaluation."""
        owner = require('read').owner
        return {'schemaVersion': 1,
                'feedback': self.editorial.records(owner, 'feedback'),
                'comparisons': self.editorial.records(owner, 'comparison'),
                'presets': self.editorial.records(owner, 'preset'),
                'acceptedRevisions': [], 'automaticTraining': False}

    def get_artifacts(self, job_id: str) -> dict:
        """Return completed outputs and five-minute download links. Treat links as private."""
        owner = require('read').owner
        items = self.artifacts.published(owner, job_id)
        return {'artifacts': [{**item, 'downloadPath': f"/api/jobs/{job_id}/artifacts/{item['id']}",
                              **self.artifacts.link(owner, job_id, item['id'])} for item in items]}

    def capabilities(self) -> dict:
        online = self.store.worker_online(require('read').owner)
        return {'name': 'BeatForge', 'version': 1, 'editorialControls': True,
                'transport': 'streamable-http', 'generationAvailable': online,
                'generationMessage': 'Worker connected.' if online else 'Your local worker is offline. Jobs wait until it reconnects.',
                'humanPlaytestRequired': True}

    def resolve_plan(self, plan: dict) -> dict:
        require('read')
        return normalize_mapping_plan(plan)

    def get_job(self, job_id: str) -> dict:
        return self.store.get(require('read').owner, job_id)

    def cancel_job(self, job_id: str) -> dict:
        return self.store.cancel(require('edit').owner, job_id)

    def create_upload_session(self, upload: UploadRequest) -> dict:
        """Reserve audio storage. Upload raw bytes to uploadPath with this account's bearer token."""
        result = self.store.reserve_upload(require('generate').owner, upload.filename, upload.size)
        return {**result, 'uploadPath': f"/api/uploads/{result['id']}/content", 'method': 'PUT',
                'contentType': 'application/octet-stream'}

    def get_upload(self, upload_id: str) -> dict:
        return self.store.get_upload(require('read').owner, upload_id)

    def start_generation(self, generation: GenerationRequest) -> dict:
        """Queue a map from an owned upload. The worker may require timing anchors or corpus setup."""
        owner = require('generate').owner
        upload = self.store.get_upload(owner, generation.uploadId)
        if upload['state'] != 'ready':
            raise ValueError('Finish uploading the audio before generation')
        payload = generation.model_dump(exclude={'idempotencyKey'})
        payload['mappingPlan'] = normalize_mapping_plan(generation.mappingPlan)
        payload['difficulties'] = list(dict.fromkeys(generation.difficulties))
        job = self.store.submit(owner, generation.idempotencyKey, payload)
        return {**job, 'workerOnline': self.store.worker_online(owner)}

    def revise_section(self, job_id: str, revision: SectionRevision) -> dict:
        """Create a separate section revision on the original worker; never alter the parent map."""
        owner = require('edit').owner
        parent = self.store.get(owner, job_id)
        if parent['state'] != 'completed' or parent['result']['status'] != 'playtest_candidate':
            raise ValueError('Start from a completed map that passed structural validation')
        preview = self.editorial.preview(owner, job_id, revision.difficulty)
        if preview['chartHash'] != revision.baseHash:
            raise ValueError('The map changed. Reload the preview before revising')
        if preview['chart'].get('bpmEvents'):
            raise ValueError('Section revisions require a constant exported BPM clock')
        if revision.endBeat * 60 / preview['bpm'] > preview['duration'] + 1e-6:
            raise ValueError('The selected range extends beyond the song')
        data = revision.model_dump(exclude={'idempotencyKey'})
        data['mappingPlan'] = normalize_mapping_plan(revision.mappingPlan)
        payload = {**parent['request'], 'revision': {**data, 'parentJob': job_id},
                   'mappingPlan': data['mappingPlan'], 'seed': revision.seed}
        return self.store.submit(owner, revision.idempotencyKey, payload)


class TokenAuth:
    """Authenticate every request before either API adapter is invoked."""
    def __init__(self, app, credentials: dict[str, Principal], verifier: OAuthVerifier | None = None):
        self.app, self.credentials, self.verifier = app, credentials, verifier

    async def __call__(self, scope, receive, send):
        public_paths = {'/health', '/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'}
        if scope['type'] != 'http' or scope.get('path') in public_paths or scope.get('path', '').startswith('/downloads/'):
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        authorization = headers.get(b'authorization', b'').decode('latin-1')
        scheme, _, supplied = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not supplied.isascii() or len(supplied) > 16384:
            supplied = ''
        actor = next((value for key, value in self.credentials.items() if secrets.compare_digest(key, supplied)), None)
        if actor is None and supplied and self.verifier:
            try:
                actor = await anyio.to_thread.run_sync(self.verifier.verify, supplied)
            except jwt.PyJWTError:
                actor = None
        if actor is None:
            challenge = 'Bearer'
            if self.verifier:
                challenge += f' resource_metadata="{self.verifier.settings.metadata_url}"'
            return await JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                      headers={'WWW-Authenticate': challenge, 'Cache-Control': 'no-store'})(scope, receive, send)
        token = principal.set(actor)
        try:
            await bounded_request(self.app, scope, receive, send)
        finally:
            principal.reset(token)


def create_app(*, database: Path, credentials: dict[str, Principal] | None = None,
               hosts: list[str] | None = None, origins: list[str] | None = None,
               oauth: OAuthSettings | None = None):
    protect_download_logs()
    credentials = credentials or {}
    if (not credentials and not oauth) or any(len(key) < 32 or not key.isascii() or not actor.owner for key, actor in credentials.items()):
        raise ValueError('Configure nonempty owners and tokens of at least 32 characters')
    hosts = hosts or ['127.0.0.1:*', 'localhost:*']
    origins = origins or []
    if '*' in hosts or '*' in origins:
        raise ValueError('Configure explicit connector hosts and browser origins')
    verifier = OAuthVerifier(oauth) if oauth else None
    operations = Operations(ConnectorStore(database))
    uploads = Uploads(operations.store, database.parent / 'uploads')
    mcp = FastMCP('BeatForge', instructions='Preserve the user creative brief. Never invent ratings or headset evidence.',
                  stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    mcp.tool(name='get_capabilities', annotations=read)(operations.capabilities)
    mcp.tool(name='resolve_mapping_plan', annotations=read)(operations.resolve_plan)
    mcp.tool(name='get_job', annotations=read)(operations.get_job)
    mcp.tool(name='get_artifacts', annotations=read)(operations.get_artifacts)
    for name, handler in [('list_jobs', operations.list_jobs), ('get_qa', operations.get_qa),
                          ('get_chart_preview', operations.get_preview_summary), ('list_presets', operations.list_presets),
                          ('get_learning_summary', operations.learning_summary)]:
        mcp.tool(name=name, annotations=read)(handler)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mcp.tool(name='record_mapping_feedback', annotations=write)(operations.record_feedback)
    mcp.tool(name='record_comparison', annotations=write)(operations.record_comparison)
    mcp.tool(name='save_preset', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))(operations.save_preset)
    mcp.tool(name='suggest_mapping_adjustments', annotations=read)(operations.feedback_suggestions)
    mcp.tool(name='get_upload', annotations=read)(operations.get_upload)
    mcp.tool(name='create_upload_session', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))(operations.create_upload_session)
    mcp.tool(name='start_generation', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))(operations.start_generation)
    mcp.tool(name='revise_section', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))(operations.revise_section)
    mcp.tool(name='cancel_job', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))(operations.cancel_job)
    transport = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title='BeatForge connector', lifespan=lifespan)

    def connector_schema():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version='0.1.0', routes=app.routes,
                description='Account-scoped BeatForge API. Use a bearer credential from the configured OAuth provider or a private pilot token. Human feedback must be explicitly supplied; generated maps still require playtesting.')
            schema.setdefault('components', {}).setdefault('securitySchemes', {})['BearerAuth'] = {
                'type': 'http', 'scheme': 'bearer', 'description': 'Scoped BeatForge access token. Never put it in a URL.'}
            schema['security'] = [{'BearerAuth': []}]
            for path, operations_schema in schema['paths'].items():
                if path == '/health' or path.startswith('/.well-known/') or path.startswith('/downloads/'):
                    for operation in operations_schema.values():
                        if isinstance(operation, dict):
                            operation['security'] = []
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = connector_schema

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_unavailable(request, error):
        return JSONResponse({'detail': 'Gateway metadata storage is unavailable. Contact the operator.'}, status_code=503)

    @app.exception_handler(PermissionError)
    async def permission_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=403)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({'detail': 'Resource not found'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    if oauth:
        app.get('/.well-known/oauth-protected-resource')(oauth.metadata)
        app.get('/.well-known/oauth-protected-resource/mcp')(oauth.metadata)

    app.get('/api/capabilities')(operations.capabilities)
    app.post('/api/uploads')(operations.create_upload_session)
    app.get('/api/uploads/{upload_id}')(operations.get_upload)

    @app.put('/api/uploads/{upload_id}/content')
    async def receive_upload(upload_id: str, request: Request):
        return await uploads.receive(require('generate').owner, upload_id, request)
    app.post('/api/mapping-plan/resolve')(operations.resolve_plan)
    app.get('/api/jobs/{job_id}')(operations.get_job)
    app.post('/api/jobs/{job_id}/cancel')(operations.cancel_job)
    app.post('/api/jobs')(operations.start_generation)
    app.post('/api/jobs/{job_id}/revise')(operations.revise_section)
    app.get('/api/jobs')(operations.list_jobs)
    app.get('/api/jobs/{job_id}/preview')(operations.get_preview)
    app.get('/api/jobs/{job_id}/qa')(operations.get_qa)
    app.get('/api/learning')(operations.learning_summary)
    app.get('/api/learning/export')(operations.export_learning)
    app.post('/api/learning/suggestions')(operations.feedback_suggestions)
    app.post('/api/feedback')(operations.record_feedback)
    app.post('/api/comparisons')(operations.record_comparison)
    app.post('/api/presets')(operations.save_preset)
    app.get('/api/presets')(operations.list_presets)
    app.get('/api/jobs/{job_id}/artifacts')(operations.get_artifacts)

    @app.get('/downloads/{artifact_id}')
    def signed_download(artifact_id: str, ticket: str):
        path, name, media_type = operations.artifacts.signed_download(artifact_id, ticket)
        return FileResponse(path, filename=name, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                     'Referrer-Policy': 'no-referrer'})

    @app.get('/api/jobs/{job_id}/artifacts/{artifact_id}')
    def download_artifact(job_id: str, artifact_id: str):
        path, name, media_type = operations.artifacts.download(require('read').owner, job_id, artifact_id)
        return FileResponse(path, filename=name, media_type=media_type,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.put('/api/worker/jobs/{job_id}/artifacts/{name}')
    async def upload_artifact(job_id: str, name: str, request: Request, x_beatforge_lease: str = Header()):
        return await operations.artifacts.receive(require('worker').owner, job_id, x_beatforge_lease, name, request)

    @app.post('/api/worker/claim')
    def claim_work():
        owner = require('worker').owner
        operations.store.worker_seen(owner)
        job = operations.store.claim(owner)
        if job:
            job['upload'] = operations.store.get_upload(owner, job['request']['uploadId'])
        return {'job': job}

    @app.post('/api/worker/jobs/{job_id}/heartbeat')
    def heartbeat(job_id: str, x_beatforge_lease: str = Header()):
        owner = require('worker').owner
        operations.store.heartbeat(owner, job_id, x_beatforge_lease)
        operations.store.worker_seen(owner)
        return {'status': 'ok'}

    @app.get('/api/worker/jobs/{job_id}/audio')
    def worker_audio(job_id: str, x_beatforge_lease: str = Header()):
        owner = require('worker').owner
        job = operations.store.check_lease(owner, job_id, x_beatforge_lease)
        path = uploads.path(job['request']['uploadId'])
        if not path.is_file():
            raise KeyError('Upload not found')
        return FileResponse(path, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    @app.post('/api/worker/jobs/{job_id}/finish')
    def finish_work(job_id: str, result: WorkerResult, x_beatforge_lease: str = Header()):
        owner = require('worker').owner
        job = operations.store.check_lease(owner, job_id, x_beatforge_lease)
        manifest = operations.artifacts.manifest(owner, job_id, x_beatforge_lease)
        if result.status == 'playtest_candidate':
            required = {'map.zip', 'audio.ogg', 'qa.json'} | {f'preview-{name}.json' for name in job['request']['difficulties']}
            if not required.issubset({item['name'] for item in manifest}):
                raise ValueError('Upload map, audio, QA and every difficulty preview before completing this job')
        return operations.store.finish(owner, job_id, x_beatforge_lease,
                                       {**result.model_dump(), 'artifacts': manifest}, failed=result.status == 'error')
    app.mount('/', transport)
    app.add_middleware(TokenAuth, credentials=credentials, verifier=verifier)
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=['GET', 'POST', 'PUT', 'DELETE'],
                       allow_headers=['Authorization', 'Content-Type', 'MCP-Protocol-Version', 'Mcp-Session-Id'],
                       expose_headers=['WWW-Authenticate', 'Mcp-Session-Id'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.removesuffix(':*') for host in hosts])
    return app


def from_env():
    """uvicorn beatforge.connector:from_env --factory --host 127.0.0.1 --port 8013"""
    config = json.loads(os.environ.get('BEATFORGE_CONNECTOR_TOKENS', '{}'))
    credentials = {token: Principal(value['owner'], frozenset(value['scopes'])) for token, value in config.items()}
    oauth = None
    if os.environ.get('BEATFORGE_OAUTH_ISSUER'):
        oauth = OAuthSettings(issuer=os.environ['BEATFORGE_OAUTH_ISSUER'],
                              jwks_url=os.environ['BEATFORGE_OAUTH_JWKS_URL'],
                              resource=os.environ['BEATFORGE_OAUTH_RESOURCE'],
                              revoked_subjects=frozenset(json.loads(os.environ.get('BEATFORGE_REVOKED_SUBJECTS', '[]'))))
    return create_app(database=Path(os.environ.get('BEATFORGE_CONNECTOR_DB', 'data/connector/jobs.sqlite3')),
                      credentials=credentials,
                      oauth=oauth,
                      origins=list(filter(None, os.environ.get('BEATFORGE_CONNECTOR_ORIGINS', '').split(','))),
                      hosts=os.environ.get('BEATFORGE_CONNECTOR_HOSTS', 'localhost:*,127.0.0.1:*').split(','))
