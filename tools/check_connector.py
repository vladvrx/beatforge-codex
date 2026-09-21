"""Read-only HTTP check using the official MCP client and the same REST account."""
import argparse
import asyncio
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(gateway, token, job_id=None):
    parsed = urlsplit(gateway)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in {'', '/'} or not parsed.hostname or
            (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}))):
        raise ValueError('Expected HTTPS origin or local HTTP origin')
    if job_id and not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job identifier')
    gateway = gateway.rstrip('/')
    async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'}, timeout=30,
                                 follow_redirects=False) as http:
        async def rest(path, body=None):
            reply = await (http.get(gateway + path) if body is None else http.post(gateway + path, json=body))
            reply.raise_for_status()
            return reply.json()
        async with streamable_http_client(gateway + '/mcp', http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'BeatForge'
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {'get_capabilities', 'resolve_mapping_plan', 'get_job', 'get_qa', 'get_artifacts'} <= names
                async def call(name, arguments=None):
                    result = await session.call_tool(name, arguments or {})
                    if result.isError:
                        raise RuntimeError(f'MCP operation failed: {name}')
                    return json.loads(next(item.text for item in result.content if item.type == 'text'))
                plan = {'brief': 'lighter verses, no bombs', 'style': 'flow'}
                assert await call('resolve_mapping_plan', {'plan': plan}) == await rest('/api/mapping-plan/resolve', plan)
                report = {'transport': 'real HTTP', 'client': 'official Python MCP SDK',
                          'server': initialized.serverInfo.name, 'tools': len(names), 'planParity': True,
                          'assistantClientsVerified': False, 'downloadsVerified': 0}
                if job_id:
                    job = await rest('/api/jobs/' + job_id)
                    assert await call('get_job', {'job_id': job_id}) == job
                    report['jobParity'] = True
                    if job['result'] and job['result']['status'] == 'playtest_candidate':
                        qa = await rest(f'/api/jobs/{job_id}/qa')
                        assert await call('get_qa', {'job_id': job_id}) == qa
                        report['qaParity'] = True
                        manifest = await call('get_artifacts', {'job_id': job_id})
                        for item in manifest['artifacts']:
                            assert re.fullmatch('[a-f0-9]{32}', item['id'])
                            digest, size = hashlib.sha256(), 0
                            async with http.stream('GET', f"{gateway}/api/jobs/{job_id}/artifacts/{item['id']}") as response:
                                response.raise_for_status()
                                async for chunk in response.aiter_bytes():
                                    size += len(chunk)
                                    if size > 64 * 1024 * 1024:
                                        raise ValueError('Artifact exceeded bounded check size')
                                    digest.update(chunk)
                            assert size == item['size'] and digest.hexdigest() == item['sha256']
                            report['downloadsVerified'] += 1
                return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--job')
    args = parser.parse_args()
    try:
        report = asyncio.run(check(args.gateway, os.environ['BEATFORGE_CHECK_TOKEN'], args.job))
    except Exception as error:
        # Do not print exception URLs, signed tickets or credentials.
        print(json.dumps({'ok': False, 'errorType': type(error).__name__}))
        raise SystemExit(1)
    print(json.dumps({'ok': True, **report}, indent=2))


if __name__ == '__main__':
    main()
