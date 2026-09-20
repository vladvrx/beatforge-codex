import pytest

from beatforge.connector_maintenance import recover_uploads
from beatforge.connector_store import ConnectorStore


def test_offline_recovery_preserves_ready_audio_and_allows_retry(tmp_path):
    store = ConnectorStore(tmp_path/'jobs.db')
    root = tmp_path/'uploads'
    root.mkdir()
    interrupted = store.reserve_upload('alice','interrupted.wav',4)
    completed = store.reserve_upload('bob','completed.wav',4)
    for owner, upload in [('alice',interrupted),('bob',completed)]:
        store.begin_upload(owner,upload['id'])
    store.complete_upload('bob',completed['id'],'a'*64)
    partial = root/(interrupted['id']+'.part')
    partial.write_bytes(b'pa')
    ready = root/(completed['id']+'.audio')
    ready.write_bytes(b'keep')
    assert recover_uploads(store.path)['partialFiles'] == 1
    assert partial.read_bytes() == b'pa'
    assert store.get_upload('alice',interrupted['id'])['state'] == 'writing'
    assert recover_uploads(store.path,apply=True)['interruptedUploads'] == 1
    assert not partial.exists()
    assert ready.read_bytes() == b'keep'
    assert store.get_upload('bob',completed['id'])['state'] == 'ready'
    store.begin_upload('alice',interrupted['id'])
    store.abort_upload('alice',interrupted['id'])
    assert recover_uploads(store.path,apply=True)['interruptedUploads'] == 0


def test_recovery_checks_all_paths_before_removal(tmp_path):
    store = ConnectorStore(tmp_path/'jobs.db')
    upload = store.reserve_upload('alice','track.wav',4)
    store.begin_upload('alice',upload['id'])
    root = tmp_path/'uploads'
    root.mkdir()
    partial = root/(upload['id']+'.part')
    partial.write_bytes(b'pa')
    (root/(upload['id']+'.audio')).mkdir()
    with pytest.raises(ValueError,match='regular file'):
        recover_uploads(store.path,apply=True)
    assert partial.read_bytes() == b'pa'
    assert store.get_upload('alice',upload['id'])['state'] == 'writing'
