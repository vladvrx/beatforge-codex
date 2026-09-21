import pytest
from beatforge.connector_store import ConnectorStore


def test_requests_are_owner_scoped_and_survive_restart(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    store = ConnectorStore(path)
    job = store.submit('alice', 'one', {'brief': 'lighter verses'})
    assert ConnectorStore(path).get('alice', job['id']) == job
    assert store.submit('alice', 'one', {'brief': 'lighter verses'}) == job
    with pytest.raises(ValueError):
        store.submit('alice', 'one', {'brief': 'different'})
    with pytest.raises(KeyError):
        store.get('bob', job['id'])
    assert store.claim('bob') is None


def test_stale_worker_cannot_overwrite_retry_or_cancel(tmp_path, monkeypatch):
    import beatforge.connector_store as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    store = ConnectorStore(tmp_path / 'jobs.sqlite3')
    job = store.submit('alice', 'one', {})
    first = store.claim('alice', lease_seconds=10)
    assert store.claim('alice') is None
    clock[0] = 111
    second = store.claim('alice')
    assert second['attempts'] == 2
    with pytest.raises(ValueError):
        store.finish('alice', job['id'], first['leaseToken'], {})
    store.cancel('alice', job['id'])
    with pytest.raises(ValueError):
        store.finish('alice', job['id'], second['leaseToken'], {})
    assert store.get('alice', job['id'])['state'] == 'cancelled'


def test_successful_result_and_no_lease_disclosure(tmp_path):
    store = ConnectorStore(tmp_path / 'jobs.sqlite3')
    job = store.submit('alice', 'one', {})
    lease = store.claim('alice')
    store.heartbeat('alice', job['id'], lease['leaseToken'])
    result = store.finish('alice', job['id'], lease['leaseToken'], {'qaErrors': 0})
    assert result['state'] == 'completed'
    assert result['result'] == {'qaErrors': 0}
    assert 'leaseToken' not in store.get('alice', job['id'])


def test_parallel_workers_claim_a_job_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = ConnectorStore(tmp_path / 'jobs.sqlite3')
    job = store.submit('alice', 'one', {})
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: store.claim('alice'), range(5)))
    leases = [result for result in results if result]
    assert len(leases) == 1
    assert leases[0]['id'] == job['id']
    assert leases[0]['attempts'] == 1


def test_crashed_worker_attempts_are_bounded(tmp_path, monkeypatch):
    import beatforge.connector_store as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    store = ConnectorStore(tmp_path / 'jobs.sqlite3')
    job = store.submit('alice', 'one', {})
    for attempt in range(3):
        lease = store.claim('alice', lease_seconds=10)
        assert lease['attempts'] == attempt + 1
        clock[0] += 11
    assert store.claim('alice') is None
    assert store.get('alice', job['id'])['state'] == 'failed'
    with pytest.raises(ValueError):
        store.finish('alice', job['id'], lease['leaseToken'], {})
