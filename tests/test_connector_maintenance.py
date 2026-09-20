import pytest
import json

from beatforge.connector_maintenance import recover_uploads
from beatforge.connector_store import ConnectorStore
from beatforge.connector_maintenance import reclaim_artifacts
from beatforge.connector_artifacts import Artifacts


def test_obsolete_attempt_cleanup_preserves_results_and_current_leases(tmp_path):
    store = ConnectorStore(tmp_path/'jobs.db')
    artifacts = Artifacts(store,tmp_path/'artifacts')
    store.submit('alice','running',{})
    running = store.claim('alice')
    finished = store.submit('bob','finished',{})
    identifiers = ['1'*32,'2'*32,'3'*32]
    with store.connect() as db:
        db.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",
                   (json.dumps({'artifacts':[{'id':identifiers[0]}]}),finished['id']))
        for identifier, job, lease in [(identifiers[0],finished['id'],'old'),
                                       (identifiers[1],running['id'],running['leaseToken']),
                                       (identifiers[2],running['id'],'abandoned')]:
            db.execute('INSERT INTO artifacts(id,owner,job,lease,name,size) VALUES(?,?,?,?,?,?)',
                       (identifier,'alice',job,lease,'audio.ogg',4))
            artifacts.path(identifier).write_bytes(b'keep')
    assert reclaim_artifacts(store.path)['obsoleteArtifacts'] == 1
    assert all(artifacts.path(i).exists() for i in identifiers)
    assert reclaim_artifacts(store.path,apply=True)['reservedBytes'] == 4
    assert all(artifacts.path(i).read_bytes() == b'keep' for i in identifiers[:2])
    assert not artifacts.path(identifiers[2]).exists()
    assert reclaim_artifacts(store.path,apply=True)['obsoleteArtifacts'] == 0


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
