"""Standard-library fixture verification; does not load a DB or contact an API."""
from pathlib import Path
import csv
import hashlib
import json
import re
import struct
import tempfile

ROOT = Path(__file__).resolve().parent
checks = 0
CSV_HEADER = ['폴더', '파일명', 'PPT페이지', '원본이미지', '처리', '가로px', '세로px']
SOURCE_STATUSES = {'ready', 'missing_original', 'planning_reference',
                   'content_confirmed_original_missing', 'received_by_team_not_in_package', 'mock'}
EVIDENCE_STATUSES = {'자료에 기재됨', '이미지에서 판독한 발췌', '자료에 기재됨 (2017년 카다로그)'}


def bundle_path(root, relative):
    if not isinstance(relative, str) or not relative or ':' in relative:
        raise ValueError('INVALID_PATH')
    portable = relative.replace('\\', '/')
    if portable.startswith('/'):
        raise ValueError('PATH_OUTSIDE_BUNDLE')
    path = (root / portable).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('PATH_OUTSIDE_BUNDLE')
    return path


def source_hash(source, root):
    """Schema sample section 5. No DB/import side effects; result is metadata only."""
    expected = source.get('sha256')
    if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected)):
        raise ValueError('INVALID_SHA256')
    result = dict(hash_verified=False, supplied_sha256=expected, package_sha256=None,
                  dedup_sha256=None, reason='original_not_in_package')
    relative = source.get('path')
    if relative is None:
        if source.get('available_in_package') is True:
            raise ValueError('MISSING_PATH')
        return result
    path = bundle_path(root, relative)
    if not path.is_file():
        raise ValueError('FILE_NOT_FOUND')
    actual = sha(path)
    result['package_sha256'] = actual
    if expected is None:
        result['reason'] = 'sha256_null_not_a_dedup_key'
        return result
    note = source.get('sha256_note') or ''
    if re.search(r'원본(?:\s*파일)?의\s*(?:SHA-?256|해시)', note, re.IGNORECASE):
        result['reason'] = 'declared_hash_is_original_not_packaged_derivative'
        return result
    if actual != expected.lower():
        raise ValueError('HASH_MISMATCH')
    result.update(hash_verified=True, dedup_sha256=actual, reason='matched_packaged_file')
    return result


def locator_object(value):
    """Fixture comparison helper; source strings are retained, never rewritten."""
    if not isinstance(value, str):
        raise ValueError('INVALID_LOCATOR')
    patterns = [
        (r'PPT ([1-9]\d*)쪽(?:\s*·\s*.+)?', 'slide'),
        (r'(?:카다로그|PDF) ([1-9]\d*)쪽(?:\s*·\s*.+)?', 'page'),
        (r'TXT ([1-9]\d*)행', 'line'),
        (r'MD 문단 ([1-9]\d*)', 'paragraph'),
        (r'DOCX ([1-9]\d*)문단', 'paragraph'),
    ]
    for pattern, key in patterns:
        match = re.fullmatch(pattern, value)
        if match:
            number = int(match[1])
            return {'line_start': number, 'line_end': number} if key == 'line' else {key: number}
    raise ValueError('AMBIGUOUS_LOCATOR' if re.fullmatch(r'\d+쪽', value) else 'INVALID_LOCATOR')


def verify_schema_rules():
    """Exercise exceptions using synthetic temporary bytes, never real originals."""
    def raises(code, fn):
        try:
            fn()
        except ValueError as error:
            check(str(error) == code, 'expected error ' + code)
        else:
            check(False, 'missing error ' + code)

    with tempfile.TemporaryDirectory(prefix='mock_hash_rules_') as folder:
        root = Path(folder)
        (root / 'source.txt').write_bytes(b'[MOCK] original fixture\n')
        (root / 'reduced.txt').write_bytes(b'[MOCK] smaller derivative\n')
        digest = sha(root / 'source.txt')
        source = dict(path='source.txt', sha256=digest, available_in_package=True)
        check(source_hash(source, root)['hash_verified'], 'matching original verified')
        raises('HASH_MISMATCH', lambda: source_hash(dict(source, sha256='0' * 64), root))
        null_hash = source_hash(dict(source, sha256=None), root)
        check(not null_hash['hash_verified'] and null_hash['dedup_sha256'] is None, 'null hash cannot deduplicate')
        absent = source_hash(dict(path=None, sha256=None, available_in_package=False), root)
        check(not absent['hash_verified'] and absent['dedup_sha256'] is None, 'missing original supported')
        for note in ['원본 파일의 SHA-256', '원본의 해시']:
            reduced = source_hash(dict(source, path='reduced.txt', sha256_note=note), root)
            check(not reduced['hash_verified'] and reduced['package_sha256'] != digest
                  and reduced['dedup_sha256'] is None, 'derivative skips original hash comparison')
        raises('HASH_MISMATCH', lambda: source_hash(dict(source, path='reduced.txt', sha256_note='일반 메모'), root))
        raises('INVALID_SHA256', lambda: source_hash(dict(source, sha256='truncated…hash'), root))
        raises('FILE_NOT_FOUND', lambda: source_hash(dict(source, path='missing.txt'), root))
        raises('MISSING_PATH', lambda: source_hash(dict(source, path=None), root))
        raises('PATH_OUTSIDE_BUNDLE', lambda: source_hash(dict(source, path='../outside.txt'), root))
        # Filename is a display field; path alone locates the original.
        check(source_hash(dict(source, filename='display-only.txt'), root)['hash_verified'], 'use path, not filename')

    for value, expected in [
        ('PPT 12쪽 · 가상 제목', {'slide': 12}),
        ('PPT 16쪽 · image42 · 가상 공정', {'slide': 16}),
        ('카다로그 2쪽 · 가상 회사소개 · 연혁', {'page': 2}),
        ('TXT 2행', {'line_start': 2, 'line_end': 2}),
        ('MD 문단 3', {'paragraph': 3}),
    ]:
        check(locator_object(value) == expected, 'team locator pattern: ' + value)
    for case in read('quality/locator_cases.json'):
        if 'expected' in case:
            check(locator_object(case['input']) == case['expected'], 'existing locator case')
        else:
            raises(case['error'], lambda case=case: locator_object(case['input']))


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(message)


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(s) for s in (ROOT / path).read_text(encoding='utf-8').splitlines() if s.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    global checks
    checks = 0
    verify_schema_rules()
    manifest = read('bundle_manifest.json')
    sources = read('ingest/sources.json')
    chunks = rows('ingest/company_chunks.jsonl')
    segments = rows('normalized/segments.jsonl')
    catalog = read('ui/catalog.json')
    index = read('ui/scenarios/index.json')
    by_src = {s['source_id']: s for s in sources}
    by_seg = {s['segment_id']: s for s in segments}
    by_seg.update({s['segment_id']: s for s in catalog['session_segments']})
    by_asset = {a['asset_id']: a for a in catalog['assets']}
    check(len(by_src) == len(sources), 'source IDs must be unique')
    check(len({c['chunk_id'] for c in chunks}) == len(chunks), 'chunk IDs must be unique')
    check(len({s['segment_id'] for s in segments}) == len(segments), 'segment IDs must be unique')
    check(manifest['requires_with_mock'] is True, 'mock opt-in required')
    hash_results = {}
    for s in sources:
        check(s['mock'] is True and s['source_id'].startswith('MOCK') and s['name'].startswith('[MOCK]'), 'source markers')
        required = {'source_id', 'filename', 'available_in_package', 'status', 'path',
                    'date_from_filename', 'document_date_verified', 'extraction_method',
                    'origin_group', 'use_as_company_evidence', 'note'}
        check(required <= s.keys(), 'source schema fields')
        check(s['status'] in SOURCE_STATUSES and s['status'] == 'mock', 'source schema status')
        check(all(type(s[k]) is bool for k in ['available_in_package', 'document_date_verified', 'use_as_company_evidence']), 'source boolean fields')
        check(s['document_date_verified'] is False, 'source dates remain unverified')
        check(s['date_from_filename'] is None, 'date not invented from undated filename')
        check(isinstance(s['origin_group'], str) and s['origin_group'].startswith('MOCK'), 'source origin group')
        check(s['extraction_method'] == 'mock', 'source mock extraction metadata')
        path = bundle_path(ROOT / 'ingest', s['path'])
        check(s['filename'] == path.name, 'filename display versus path')
        hash_results[s['source_id']] = source_hash(s, ROOT / 'ingest')
        check(hash_results[s['source_id']]['hash_verified'], 'packaged mock original SHA: ' + s['source_id'])
    check(len({s['sha256'] for s in sources}) == len(sources), 'distinct mock originals must not collide under SHA dedup')
    check(by_src['MOCK03']['origin_group'] == by_src['MOCK04']['origin_group'], 'derived evidence shares an origin group')
    for c in chunks:
        check(c['source_id'] in by_src, 'chunk source exists')
        check(c['mock'] is True and c['text'].startswith('[MOCK]'), 'chunk markers')
        check({'origin_group', 'company_confirmation', 'publication_allowed', 'notes'} <= c.keys(), 'chunk team fields')
        check(c['evidence_status'] in EVIDENCE_STATUSES, 'team evidence enum')
        check(c['company_confirmation'] == '미확인', 'company confirmation remains unknown')
        check(c['publication_allowed'] is None, 'publication is not inferred')
        check(c['extraction_method'] in {'parser', 'manual', 'manual_excerpt'}, 'team extraction enum')
        source = by_src[c['source_id']]
        check(c['origin_group'] == source['origin_group'], 'chunk origin group matches source')
        date = c.get('document_date') or source.get('document_date') or source.get('date_from_filename')
        segment = by_seg['seg_' + c['chunk_id']]
        check(date == segment['document_date'], 'date preservation and fallback')
        check(segment['text'] == c['text'] and segment['source_id'] == c['source_id'], 'raw-normalized text/source preserved')
        check(isinstance(c['locator'], str) and c['locator'] == segment['original_locator'], 'raw locator remains a string')
        check(locator_object(c['locator']) == segment['locator'], 'normalized locator object matches')
        original = bundle_path(ROOT / 'ingest', source['path']).read_text(encoding='utf-8').splitlines()
        line = locator_object(c['locator'])['line_start']
        check(original[line - 1] == c['text'], 'locator resolves to exact original')
    check(by_src['MOCK02']['document_date'] == '2017', 'year-only date must remain year-only')
    check(read('ingest/mock_source_entry.json') == sources[0], 'single-source alias')
    check(rows('ingest/mock_chunks.jsonl') == [c for c in chunks if c['source_id'] == 'MOCK01'], 'single-source chunks alias')
    csv_path = ROOT / 'ingest/00_이미지목록.csv'
    check(csv_path.read_bytes().startswith(b'\xef\xbb\xbf'), 'CSV UTF-8 BOM')
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        check(reader.fieldnames == CSV_HEADER, 'CSV exactly seven team columns in order')
        images = list(reader)
    candidates = read('ingest/photo_candidates.json')
    check(isinstance(candidates, list), 'photo candidates array')
    by_photo_path = {p['path']: p for p in candidates}
    check(len(by_photo_path) == len(candidates) == len(images), 'candidate paths unique and cover this fixture CSV')
    check(len({p['photo_id'] for p in candidates}) == len(candidates), 'photo IDs unique')
    raw_approved_images = set()
    csv_paths = set()
    ui_image_metadata = {r['image_id']: r for r in catalog['image_metadata']}
    for r in images:
        check(set(r) == set(CSV_HEADER) and all(v is not None for v in r.values()), 'CSV row has seven values')
        relative = f"05_이미지/전체_추출이미지/{r['폴더']}/{r['파일명']}"
        check(relative in by_photo_path and relative not in csv_paths, 'CSV-to-candidate path pairing')
        csv_paths.add(relative)
        photo = by_photo_path[relative]
        required = {'photo_id', 'path', 'caption_candidate', 'locator', 'reason', 'width', 'height',
                    'ratio', 'selected_as_candidate', 'approved_for_external_use', 'caption_confirmed',
                    'confirmed_by', 'confirmed_at'}
        check(required <= photo.keys(), 'photo team fields')
        check(photo['mock'] is True and photo['source_id'] in by_src and photo['photo_id'].startswith('MOCK'), 'photo mock markers')
        check(photo['caption_candidate'].startswith('[MOCK]'), 'mock photo caption')
        check(photo['approved_for_external_use'] is None, 'raw photo publication stays unknown')
        check(photo['caption_confirmed'] is False and photo['confirmed_by'] is None and photo['confirmed_at'] is None, 'caption not auto-confirmed')
        check(type(photo['selected_as_candidate']) is bool, 'candidate selection is boolean')
        check(type(photo['width']) is int and type(photo['height']) is int, 'photo dimensions integer')
        check(r['처리'] == '원본 그대로 추출', 'team image processing value')
        check(locator_object(photo['locator']) == {'slide': int(r['PPT페이지'])}, 'photo locator matches virtual slide position')
        p = bundle_path(ROOT / 'ingest', relative)
        check(p.is_file(), 'image exists')
        b = p.read_bytes()
        check(b[:8] == b'\x89PNG\r\n\x1a\n', 'PNG signature')
        w, h = struct.unpack('>II', b[16:24])
        check((w, h) == (int(r['가로px']), int(r['세로px'])) == (photo['width'], photo['height']), 'PNG dimensions')
        check(photo['ratio'] == round(w / h, 4), 'photo ratio')
        aid = photo['photo_id']
        check(sha(p) == photo['sha256'] == by_asset[aid]['content_hash'], 'candidate image SHA')
        alias = bundle_path(ROOT / 'ingest', ui_image_metadata[aid]['filename'])
        check(alias.read_bytes() == b, 'frozen UI image path still resolves to identical bytes')
        if photo['approved_for_external_use'] is True:
            raw_approved_images.add(aid)
    check(not raw_approved_images, 'raw candidates cannot become publicly approved automatically')
    check(set(by_photo_path) == csv_paths, 'no candidate outside CSV in this fixture')
    # UI snapshots model separate, explicitly fictitious approval states. Never
    # derive raw ingest permission from them or overwrite them during alignment.
    allowed_images = {aid for aid, r in ui_image_metadata.items() if r['external_use'] == 'allowed'}
    check(allowed_images == {'MOCK_IMG01', 'MOCK_IMG02', 'MOCK_IMG03', 'MOCK_IMG04'}, 'frozen UI approval states')
    source_views = {s['source_id']: s for s in catalog['sources']['items']}
    for s in source_views.values():
        check(s['scope'] == 'registered' and s['session_id'] is None, 'registered scope')
        check(set(s['asset_ids']) <= allowed_images, 'unconfirmed/denied images must not be selectable')
        for sid in s['usable_segment_ids']:
            check(sid in by_seg and by_seg[sid]['source_id'] == s['source_id'], 'usable segment belongs to source')
            check(by_seg[sid]['evidence_status'] in {'usable', 'needs_confirmation'}, 'excluded segment cannot be usable')
    check(not source_views['MOCK06']['usable_segment_ids'], 'excluded source has no usable segments')
    check(by_src['MOCK06']['use_as_company_evidence'] is False, 'excluded raw source policy')

    def refs_check(refs):
        for ref in refs:
            check(ref['segment_id'] in by_seg, 'evidence segment exists')
            sg = by_seg[ref['segment_id']]
            check(ref['source_id'] == sg['source_id'], 'evidence source pairing')
            check(ref['source_version'] == sg['source_version'], 'evidence source version')
            check(ref['locator'] == sg['locator'], 'evidence locator pairing')
            check(ref['excerpt'] in sg['text'], 'evidence excerpt exists in source')

    def document_check(doc, facts, selected=None):
        page_ids, block_ids = set(), set()
        for p in doc['pages']:
            check(p['page_id'] not in page_ids, 'unique page ID')
            page_ids.add(p['page_id'])
            for b in p['blocks']:
                check(b['block_id'] not in block_ids, 'unique block ID')
                block_ids.add(b['block_id'])
                check(set(b['fact_ids']) <= facts, 'block fact references')
                refs_check(b['evidence_refs'])
                if selected is not None:
                    check(all(r['source_id'] in selected for r in b['evidence_refs']), 'document evidence in selected sources')
                if b['type'] == 'image':
                    aid = b['content']['asset_id']
                    check(aid in allowed_images, 'document image must be usable')
                    if selected is not None: check(by_asset[aid]['source_id'] in selected, 'image source selected')
        return block_ids

    required_design = {
        'source_normal', 'source_upload', 'source_preflight', 'source_unread', 'source_photoonly',
        'edit_normal', 'edit_conflict', 'edit_required', 'edit_nophoto', 'edit_overflow',
        'approve_ready', 'approve_blocked', 'approve_approved', 'approve_modified', 'approve_failure',
    }
    check(required_design <= {x['scenario_id'] for x in index}, 'all 15 guide states covered')
    check(len({x['scenario_id'] for x in index}) == len(index), 'scenario IDs unique')
    for entry in index:
        scene = read(entry['path']); data = scene['data']; pf = data['preflight']
        check(scene['mock'] and scene['ui']['fixture_only'], 'UI fixtures marked')
        check(scene['scenario_id'] == entry['scenario_id'], 'scenario index matches file')
        facts = pf['facts'] if pf else catalog['facts']
        fact_ids = {f['fact_id'] for f in facts}
        for f in facts:
            refs_check(f['evidence_refs'])
            if f['status'] == 'missing': check(f['value'] is None and not f['evidence_refs'], 'missing fact has no invented value')
            for alt in f.get('alternatives') or []: refs_check(alt.get('evidence_refs', []))
        docout = data['document_response']
        block_ids = set()
        if docout:
            doc = docout['document']; block_ids = document_check(doc, fact_ids, set(data['session']['selected_source_ids']))
            check(doc['input_revision'] <= data['session']['input_revision'], 'document input cannot exceed session input')
            check(data['session']['document_summary']['document_revision'] == doc['document_revision'], 'session summary revision')
            approval = docout['approval']
            if approval and approval['status'] == 'active':
                check(approval['document_revision'] == doc['document_revision'] and doc['status'] == 'approved', 'active approval matches document')
                check(approval['input_revision'] == doc['input_revision'] == data['session']['input_revision'], 'active approval input matches')
                check(docout['validation']['status'] == 'passed', 'active approval has passed validation')
                check(data['layout_check']['status'] == 'passed', 'active approval has passed layout')
            if scene['implementation_status'] != 'future_ui_fixture':
                check(docout['validation'] is None and docout['approval'] is None, 'BE-05 snapshots keep null validation/approval')
        for prop in data['proposals']:
            check(set(prop['target_block_ids']) <= block_ids, 'proposal targets exist')
            check(prop['base_document_revision'] <= docout['document']['document_revision'], 'proposal revision')
            for candidate in prop.get('candidates') or []:
                for op in candidate['changes']:
                    if op['op'] == 'replace_block_content' and 'asset_id' in op['content']:
                        check(op['content']['asset_id'] in allowed_images, 'candidate image permission')
            for op in prop['changes']:
                if 'block_id' in op: check(op['block_id'] in prop['target_block_ids'], 'proposal operation within target')
        for iss in data['issues']:
            check(set(iss['fact_ids']) <= fact_ids, 'issue fact references')
            check(set(iss['block_ids']) <= block_ids, 'issue block references')
        if data['export'] and data['export']['status'] == 'failed':
            check(data['export']['artifact_id'] is None and data['export']['error'], 'failed export has no artifact')

    for count in [1, 4, 6, 8, 10]:
        doc = read(f'ui/documents/document_{count}pages.json')
        check(doc['target_pages'] == len(doc['pages']) == count, 'page count navigation fixtures')
        document_check(doc, {f['fact_id'] for f in catalog['facts']})
    flow = read('api_flows/be05_edit_cycle.json')
    check([d['document_revision'] for d in flow['documents']] == [1, 2, 3, 4], 'flow revision sequence')
    check(flow['steps'][2]['expected_body'] == flow['steps'][3]['expected_body'], 'same-key replay exact response')
    check(flow['documents'][0]['pages'] == flow['documents'][3]['pages'], 'restore content equals rev1')
    for i in [1, 2]:
        previous = {b['block_id']: b for p in flow['documents'][i-1]['pages'] for b in p['blocks']}
        current = {b['block_id']: b for p in flow['documents'][i]['pages'] for b in p['blocks']}
        for bid, b in previous.items():
            if bid != 'block_mock_intro': check(b == current[bid], 'non-target block preserved')
            else: check(b['fact_ids'] == current[bid]['fact_ids'] and b['evidence_refs'] == current[bid]['evidence_refs'], 'target provenance preserved')
    checksum_map = read('checksums.json')
    actual_paths = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
                    if p.is_file() and p.name not in {'checksums.json', 'validation_report.json'}
                    and '__pycache__' not in p.parts}
    check(set(checksum_map) == actual_paths, 'checksums cover every deliverable except report and checksum manifest')
    for path, expected in checksum_map.items():
        check(sha(bundle_path(ROOT, path)) == expected, 'package checksum: ' + path)
    preserved = read('preservation_checksums.json')
    preserved_paths = {p.relative_to(ROOT).as_posix() for folder in ['ui', 'api_flows', 'quality', 'normalized']
                       for p in (ROOT / folder).rglob('*') if p.is_file()}
    check(set(preserved) == preserved_paths, 'frozen directories contain exactly the original files')
    for path, expected in preserved.items():
        check(sha(bundle_path(ROOT, path)) == expected, 'preserved original: ' + path)
    for key, count in [('source_count', len(sources)), ('chunk_count', len(chunks)), ('image_count', len(images)), ('scenario_count', len(index))]:
        check(manifest[key] == count, 'manifest count: ' + key)
    return dict(status='PASS', checks=checks, sources=len(sources), chunks=len(chunks), images=len(images), scenarios=len(index),
                source_hash_checks=hash_results, raw_publication_approved_images=len(raw_approved_images),
                preserved_files=len(preserved), schema_rules_self_checks='PASS',
                scope='Fixture structure, byte hashes, ID/provenance links and state consistency only. No backend/API/LLM execution.')


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=True, indent=2))
