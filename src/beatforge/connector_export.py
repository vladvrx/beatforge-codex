"""Publish only gameplay files and selected review data, never local pipeline logs."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from beatforge.preview import chart_refs, confined, preview_payload, read_json


def prepare_artifacts(folder: Path, destination: Path, mapping_plan: dict) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    info = read_json(confined(folder, 'Info.dat'))
    refs = chart_refs(info)
    names = {'Info.dat', info['_songFilename']}
    if info.get('_coverImageFilename'):
        names.add(info['_coverImageFilename'])
    for ref in refs:
        names.add(ref['_beatmapFilename'])
        if ref.get('_lightshowDataFilename'):
            names.add(ref['_lightshowDataFilename'])
    # A portable pack uses flat gameplay filenames, not copied directory trees.
    if any(not isinstance(name, str) or '/' in name or '\\' in name or ':' in name or name in {'.', '..'} for name in names):
        raise ValueError('Generated pack contains a nonportable filename')
    outputs = {'map.zip': destination/'map.zip', 'audio.ogg': destination/'audio.ogg'}
    with zipfile.ZipFile(outputs['map.zip'], 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            archive.write(confined(folder, name), name)
    shutil.copyfile(confined(folder, info['_songFilename']), outputs['audio.ogg'])
    qa = read_json(confined(folder, '_beatforge/qa_report.json'))

    def public_finding(item):
        public = {key: item[key] for key in ('code', 'beat', 'difficulty', 'severity') if key in item}
        # Detailed local diagnostic messages may contain host paths.
        public['message'] = str(item.get('code', 'Review finding')).replace('_', ' ')
        return public

    qa_public = {'status': qa.get('status'), 'errors': [public_finding(item) for item in qa.get('errors', [])],
                 'warnings': [public_finding(item) for item in qa.get('warnings', [])], 'humanPlaytestRequired': True}
    outputs['qa.json'] = destination/'qa.json'
    outputs['qa.json'].write_text(json.dumps(qa_public, allow_nan=False), encoding='utf-8')
    for ref in refs:
        name = ref['_difficulty']
        if name not in {'Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus'}:
            raise ValueError('Unsupported exported difficulty')
        payload = preview_payload(folder, name)
        payload.pop('provenance', None)
        payload['mappingPlan'] = mapping_plan
        payload['sections'] = [{key: item[key] for key in ('startBeat', 'endBeat', 'type', 'label') if key in item} for item in payload['sections']]
        payload['findings'] = [public_finding(item) for item in payload['findings']]
        analysis_path = folder / '_beatforge/analysis.json'
        payload['canRevise'] = (analysis_path.is_file() and
                                len(read_json(analysis_path).get('beatGrid', [])) >= 2 and
                                (folder / '_beatforge/sections.json').is_file() and
                                not payload['chart'].get('bpmEvents'))
        outputs[f'preview-{name}.json'] = destination/f'preview-{name}.json'
        outputs[f'preview-{name}.json'].write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
    return outputs
