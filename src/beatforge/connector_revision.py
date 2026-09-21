"""Worker-side section editing using retained private analysis and full-map QA."""
import json
import shutil
import sys
from pathlib import Path

from beatforge.premium import SCRIPTS, PipelineCancelled
from beatforge.preview import chart_identity, chart_refs, confined, merge_section, read_json


def run_revision(root: Path, destination: Path, revision: dict, cancel_requested):
    # The gateway supplies UUIDs and a content hash, never a filesystem path.
    parent = root.resolve() / revision['parentJob']
    source = None
    for attempt in sorted(parent.glob('*/map')):
        if not attempt.resolve().is_relative_to(root.resolve()):
            continue
        if attempt.is_symlink() or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in attempt.rglob('*')):
            continue
        if chart_identity(attempt) == revision['baseHash']:
            source = attempt
            break
    if source is None:
        raise ValueError('The original worker cache is required for this revision')
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from choreography import generate_all
    from timing_export import project_map_timing, project_plan_timing
    from validate_map import validate_package
    if cancel_requested():
        raise PipelineCancelled('Revision cancelled')
    if validate_package(source).errors:
        raise ValueError('The parent map no longer passes QA')
    analysis = read_json(confined(source, '_beatforge/analysis.json'))
    sections = read_json(confined(source, '_beatforge/sections.json'))
    info = read_json(source / 'Info.dat')
    ref = next(item for item in chart_refs(info) if item['_difficulty'] == revision['difficulty'])
    original = read_json(confined(source, ref['_beatmapFilename']))
    if original.get('bpmEvents') or len(analysis.get('beatGrid', [])) < 2:
        raise ValueError('Revision requires cached analysis and constant exported BPM')
    if revision['endBeat'] * 60 / float(info['_beatsPerMinute']) > float(analysis['durationSeconds']) + 1e-6:
        raise ValueError('Revision extends beyond the song')
    merge_section(original, original, revision['startBeat'], revision['endBeat'])
    maps, report = generate_all(analysis, sections, revision['seed'],
                               difficulties=[revision['difficulty']], mapping_plan=revision['mappingPlan'])
    if cancel_requested():
        raise PipelineCancelled('Revision cancelled')
    candidate = project_map_timing(maps[revision['difficulty']], analysis)
    merged = merge_section(original, candidate, revision['startBeat'], revision['endBeat'])
    shutil.copytree(source, destination)
    confined(destination, ref['_beatmapFilename']).write_text(json.dumps(merged, indent=2), encoding='utf-8')
    provenance_path = destination / '_beatforge/provenance.json'
    provenance = read_json(provenance_path) if provenance_path.is_file() else {}
    provenance.update(revision={**revision, 'preservedOutsideSelection': True},
                      releaseGate={}, humanPlaytests=[], status='unreviewed_revision')
    for key in ('reviews', 'providerReviews', 'playtests', 'vrPlaytestPassed', 'freshSightReadPassed'):
        provenance.pop(key, None)
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    plan_path = destination / '_beatforge/mapping_plan.json'
    old_plan = read_json(plan_path) if plan_path.is_file() else {}
    plan = project_plan_timing(report.get('mappingPlan', {'controls': revision['mappingPlan'], 'sections': sections.get('sections', [])}), analysis)
    plan.update(appliesTo={key: revision[key] for key in ('difficulty', 'startBeat', 'endBeat')}, parentPlan=old_plan)
    plan_path.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    qa = validate_package(destination)
    (destination / '_beatforge/qa_report.json').write_text(json.dumps(qa.to_dict()), encoding='utf-8')
    if cancel_requested():
        raise PipelineCancelled('Revision cancelled')
    return {'status': 'invalid' if qa.errors else 'playtest_candidate'}
