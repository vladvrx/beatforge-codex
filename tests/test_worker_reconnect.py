import httpx
import pytest

from beatforge.connector_worker import run_once, serve
from beatforge.connector_store import ConnectorStore
from test_connector_worker import setup_job


def test_transient_download_failure_leaves_job_recoverable(tmp_path, monkeypatch):
    client, job, _ = setup_job(tmp_path)
    def unavailable(*args, **kwargs):
        raise httpx.ReadTimeout('synthetic network outage')
    monkeypatch.setattr(client, 'stream', unavailable)
    with client:
        client.headers['Authorization'] = 'Bearer ' + 'w' * 32
        with pytest.raises(httpx.ReadTimeout):
            run_once(client, tmp_path / 'worker')
    store = ConnectorStore(tmp_path / 'jobs.db')
    saved = store.get('alice', job['id'])
    assert saved['state'] == 'running'
    assert saved['result'] is None
    with store.connect() as db:
        db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
    reclaimed = store.claim('alice')
    assert reclaimed['id'] == job['id']
    assert reclaimed['attempts'] == 2


def test_reconnect_backoff_is_bounded_and_resets_after_success(tmp_path, monkeypatch):
    outcomes = [httpx.ConnectError('outage')] * 8 + [True, httpx.ReadTimeout('outage'), KeyboardInterrupt()]
    waits = []
    def step(*args):
        result = outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr('beatforge.connector_worker.run_once', step)
    with pytest.raises(KeyboardInterrupt):
        serve(None, tmp_path, sleep=waits.append)
    assert waits == [2, 4, 8, 16, 32, 60, 60, 60, 2]


def test_lost_finish_response_preserves_committed_result(tmp_path, monkeypatch):
    client, job, _ = setup_job(tmp_path)
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', lambda **kwargs: {'status': 'needs_anchors'})
    original = client.post
    finishes = []
    def post(url, **kwargs):
        response = original(url, **kwargs)
        if url.endswith('/finish'):
            finishes.append(kwargs['json'])
            response.raise_for_status()
            raise httpx.ReadTimeout('Response lost after commit')
        return response
    monkeypatch.setattr(client, 'post', post)
    with client:
        client.headers['Authorization'] = 'Bearer ' + 'w' * 32
        with pytest.raises(httpx.ReadTimeout):
            run_once(client, tmp_path / 'worker')
    saved = ConnectorStore(tmp_path / 'jobs.db').get('alice', job['id'])
    assert saved['state'] == 'completed'
    assert saved['result']['status'] == 'needs_anchors'
    assert len(finishes) == 1


@pytest.mark.parametrize('status', [401, 403, 404])
def test_auth_and_configuration_failures_do_not_retry(tmp_path, monkeypatch, status):
    error = httpx.HTTPStatusError('rejected', request=httpx.Request('POST', 'https://example.test'),
                                 response=httpx.Response(status))
    def step(*args):
        raise error
    monkeypatch.setattr('beatforge.connector_worker.run_once', step)
    with pytest.raises(httpx.HTTPStatusError):
        serve(None, tmp_path, sleep=lambda delay: pytest.fail('Permanent error retried'))


def test_once_does_not_hide_outage(tmp_path, monkeypatch):
    def step(*args):
        raise httpx.ConnectError('outage')
    monkeypatch.setattr('beatforge.connector_worker.run_once', step)
    with pytest.raises(httpx.ConnectError):
        serve(None, tmp_path, once=True, sleep=lambda delay: pytest.fail('Once retried'))
