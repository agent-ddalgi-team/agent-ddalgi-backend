"""Opt-in local evaluation archive and explicitly gated bounded comparisons.

Default: no recording and no model calls. Explicit mode and --record-run are required.
Archives live outside session directories and survive session expiry by user decision.
Only the fixed, explicitly selected registered files are read; no user DB is written.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if __name__ == '__main__':
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'

from app import agent_legacy, agent_llm
from app.agent_bridge import AnalyzeRequest, AnalyzeResult, DraftRequest, SegmentIn, SourceIn
from app.config import Settings
from app.models import Brief, Document, Fact, Issue, PreflightOut
from app.services import export_render
from app.services.ai_jobs import validate_analyze, validate_draft


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    def encode(obj):
        if hasattr(obj, 'model_dump'):
            return obj.model_dump(mode='json')
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        raise TypeError(type(obj).__name__)
    return json.dumps(value, ensure_ascii=False, indent=2, default=encode).encode('utf-8')


def git_revision(repo: Path = ROOT) -> str:
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()


def fixed_briefs(target_company: str) -> dict[str, Brief]:
    return {key: Brief(purpose=purpose, emphasis=emphasis, direction=direction,
                       target_pages=8, photo_preference=photo, target_company=target_company)
            for key, purpose, emphasis, direction, photo in [
                ('new_customer', '신규 고객 소개', ['공정역량', '공정능력'], 'balanced', 'balanced'),
                ('quality', '품질·공정 검토', ['품질관리', '인증'], 'quality_process', 'many'),
                ('technical', '기술 협의', ['기술', '공정능력'], 'quality_process', 'none'),
                ('delivery', '납기·고객 대응', ['납기안정성', '고객대응'], 'customer_response', 'balanced')]}


class RunArchive:
    """One immutable-name folder per run; default off, atomic individual files.

    Does not discover or dump environment values. The caller supplies only known
    credential values for an in-memory leak guard; these are never serialized.
    A leak raises before writing, instead of silently changing evidence text.
    """
    def __init__(self, *, enabled: bool = False, label: str = 'run', repo: Path = ROOT,
                 secret_values: tuple[str, ...] = ()):
        self.path: Path | None = None
        self._secrets = tuple(s for s in secret_values if s)
        self._call_number = 0
        self.storage_retries: list[dict] = []
        self.files: dict[str, dict] = {}
        self.state: dict[str, Any] = {'recording_enabled': enabled, 'started_at': stamp(),
                                     'status': 'started', 'retention': 'manual_delete_outside_session_cleanup'}
        if not enabled:
            return
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', label):
            raise ValueError('Invalid run label')
        base = repo.resolve() / 'private_runs' / 'quality_runs'
        # Reject symlinks/junctions before creating anything (including a redirected private_runs).
        for part in (base.parent, base):
            if part.is_symlink() or part.is_junction():
                raise ValueError('Archive root must not be a link')
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / (label + '_' + uuid.uuid4().hex)
        self.path.mkdir(exist_ok=False)
        self.update()

    def _check(self, data: bytes) -> None:
        if any(s.encode('utf-8') in data for s in self._secrets):
            raise ValueError('Credential detected; archive write refused')

    def write_bytes(self, name: str, data: bytes) -> None:
        if self.path is None:
            return
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..'):
            raise ValueError('Invalid archive filename')
        self._check(data)
        destination = self.path / name
        temporary = self.path / (name + '.tmp')
        if destination.is_symlink() or temporary.is_symlink():
            raise ValueError('Archive file must not be a link')
        delays = (0.1, 0.3, 0.9, 2.7)
        for attempt in range(len(delays) + 1):
            try:
                with temporary.open('wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(destination)
                break
            except PermissionError as exc:
                # Only the experiment archive retries transient access/sharing
                # failures. Never retry model generation after a storage error.
                self.storage_retries.append({'file': name, 'attempt': attempt + 1,
                    'winerror': getattr(exc, 'winerror', None), 'errno': exc.errno,
                    'delay_seconds': delays[attempt] if attempt < len(delays) else None,
                    'exhausted': attempt == len(delays), 'at': stamp()})
                self.state['storage_retries'] = list(self.storage_retries)
                if attempt == len(delays):
                    raise
                time.sleep(delays[attempt])
        if name != 'run.json':
            self.files[name] = {'sha256': digest(data), 'bytes': len(data)}

    def write(self, name: str, value: Any) -> None:
        if self.path is not None:
            self.write_bytes(name, json_bytes(value))

    def update(self, **fields) -> None:
        self.state.update(fields)
        self.state['files'] = dict(self.files)
        self.write('run.json', self.state)

    def record_call(self, event: dict) -> None:
        if self.path is None:
            return
        if event['event'] == 'prepared':
            self._call_number += 1
        self.write(f'call_{self._call_number}_{event["event"]}.json', {**event, 'recorded_at': stamp()})
        if event['event'] == 'finished':
            raw = event.get('output_text')
            if isinstance(raw, str):
                kind = 'extraction' if event['schema_name'] == 'company_info' else 'draft'
                # Keep the initial extraction alias stable; numbered files retain every supplement.
                if kind != 'extraction' or 'extraction_raw.json' not in self.files:
                    self.write_bytes(kind + '_raw.json', raw.encode('utf-8'))
                self.write_bytes(f'{kind}_{self._call_number}_raw.json', raw.encode('utf-8'))
            self.state['ledger'] = event['ledger']
            self.state.setdefault('actual_models', []).append(event['actual_model'])
        self.update()


def load_registered(db_path: Path, source_dir: Path):
    """Explicit evaluation selection of ALL files, including three previously unselected PDFs.

    Existing source flags are recorded, not changed. Existing DB segments retain
    image transcriptions and page/slide locators; their bytes are also snapshotted.
    """
    db_path, source_dir = db_path.resolve(), source_dir.resolve()
    conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    sources, manifest, assets, asset_manifest = [], [], {}, []
    try:
        conn.execute('BEGIN')
        rows = conn.execute('SELECT * FROM sources ORDER BY source_id').fetchall()
        for row in rows:
            original = (db_path.parent / row['stored_path']).resolve()
            if original.parent != source_dir:
                continue
            raw = original.read_bytes()
            if digest(raw) != row['content_hash']:
                raise ValueError('Original hash differs from registered metadata')
            segments = [SegmentIn(r['segment_id'], json.loads(r['locator_json']), r['text'])
                        for r in conn.execute('SELECT * FROM segments WHERE source_id=? ORDER BY ordinal',
                                              (row['source_id'],))]
            pictures = conn.execute("SELECT * FROM assets WHERE source_id=? AND status='ready' "
                                    "AND deleted_at IS NULL AND approved_for_external_use=1 ORDER BY asset_id",
                                    (row['source_id'],)).fetchall()
            locators, descriptions = {}, {}
            for picture in pictures:
                asset_path = (db_path.parent / picture['stored_path']).resolve()
                if not asset_path.is_relative_to(db_path.parent):
                    raise ValueError('Asset outside the registered bundle')
                data = asset_path.read_bytes()
                asset = export_render.asset_from_bytes(picture['asset_id'], data,
                    mime_type=picture['mime_type'], content_hash=picture['content_hash'])
                if not asset.ok:
                    raise ValueError('Registered image failed integrity check')
                aid = picture['asset_id'];assets[aid] = asset
                locator = json.loads(picture['photo_locator_json'] or '{}')
                locators[aid] = {k: v for k, v in locator.items()
                                 if k in ('page', 'slide') and type(v) is int and v > 0}
                descriptions[aid] = {'caption': picture['caption_candidate'] or '자료 사진',
                                     'width': picture['width'], 'height': picture['height']}
                asset_manifest.append({'asset_id': aid, 'source_id': row['source_id'], 'path': str(asset_path),
                                       'sha256': digest(data), 'locator': locators[aid], 'mime_type': asset.mime_type})
            sources.append(SourceIn(row['source_id'], row['source_version'], row['kind'], row['name'],
                                    row['parse_status'], segments, list(locators), row['origin_kind'],
                                    locators, descriptions))
            manifest.append({'source_id': row['source_id'], 'source_version': row['source_version'],
                             'path': str(original), 'sha256': digest(raw), 'bytes': len(raw),
                             'origin_kind': row['origin_kind'], 'selected_for_evaluation': True,
                             'prior_use_as_company_evidence': bool(row['use_as_company_evidence']),
                             'text_origin': 'existing_registered_segments', 'segment_count': len(segments)})
        selected = {Path(m['path']) for m in manifest}
        actual = {p.resolve() for p in source_dir.iterdir() if p.is_file()}
        if selected != actual:
            raise ValueError('Registered manifest does not cover the entire selected directory')
        return sources, manifest, assets, asset_manifest
    finally:
        conn.close()


def fact_locations(facts, manifest):
    paths = {m['source_id']: m['path'] for m in manifest}
    return [{'fact_id': fact.fact_id, 'status': fact.status, 'value': fact.value,
             'locations': [{'file': paths[ref.source_id], **ref.model_dump()} for ref in fact.evidence_refs]}
            for fact in facts]


def editorial_checks(page_texts: list[str], document: dict | None = None) -> dict:
    """Offline indicators, not a factual verdict. Provenance counts require JSON.

    Title/label links are deliberately excluded from body-fact duplicate counts.
    PDF line wrapping is normalized; tables/fact IDs are checked in the draft.
    """
    pages = []
    for n, text in enumerate(page_texts, 1):
        text = re.sub(r'\s+', ' ', text)
        counts = agent_llm.prose_style_counts(text)
        pages.append({'page': n, **counts,
            'mixed_endings': bool(counts['plain_endings'] and counts['formal_endings']),
            'demo_footer': text.count(export_render.DEMO_FOOTER_TEXT),
            'generic_photo_caption': text.count('선택 자료 사진')})
    totals = {key: sum(p[key] for p in pages) for key in
              ('source_description','certificate_attribution','plain_endings','formal_endings','demo_footer','generic_photo_caption')}
    totals['mixed_endings'] = bool(totals['plain_endings'] and totals['formal_endings'])
    denom = totals['plain_endings'] + totals['formal_endings']
    totals['formal_ratio'] = totals['formal_endings']/denom if denom else None
    totals.update(repeated_row_cells=None, duplicate_body_fact_uses=None)
    duplicates, repeated, seen = [], 0, {}
    if document is not None:
        for n, page in enumerate(document['pages'], 1):
            label = None
            for block in page['blocks']:
                if block['type'] == 'heading':
                    label = block['content']['text'] if block['content'].get('level') == 2 else None
                elif block['type'] == 'paragraph':
                    if label and re.match(r'^' + re.escape(label) + r'\s*[:：]', block['content']['text']):
                        repeated += 1
                    for fid in block.get('fact_ids', []):
                        if fid in seen:
                            duplicates.append({'fact_id':fid,'first_page':seen[fid],'page':n})
                        seen.setdefault(fid,n)
                    label = None
        totals.update(repeated_row_cells=repeated, duplicate_body_fact_uses=len(duplicates))
    return {'scope': 'PDF text + draft provenance' if document is not None else 'text only',
            'pages': pages, 'totals': totals, 'duplicate_details': duplicates,
            'limits': '표현 빈도는 의미 오류 판정이 아님. JSON이 없으면 행/사실 중복은 null. 원문·이미지 확인 별도.'}


class BudgetStop(Exception):
    """No next API call when measured/prospective cost crosses the approved bound."""


class CycleBudget:
    """A separate one-arm cycle: measured cumulative cost, no reservations."""
    def __init__(self, means):
        self.means = {k: Decimal(str(v)) for k, v in means.items()}
        self.planned = {'company_info': 6, 'draft_sections': 24}
        if set(self.means) != set(self.planned) or any(v <= 0 for v in self.means.values()):
            raise ValueError('Measured extraction/draft costs are required')
        self.costs = {k: [] for k in self.planned}
        self.limit, self.stopped, self.reason = Decimal('1.0'), False, None
        self.estimated_usage_calls = 0

    def snapshot(self):
        spent = sum((sum(v) for v in self.costs.values()), Decimal(0))
        forecast = spent + sum(max(0, self.planned[k] - len(v)) * max(
            self.means[k], sum(v)/len(v) if v else Decimal(0)) for k, v in self.costs.items())
        return {'spent_usd': str(spent), 'forecast_usd': str(forecast), 'limit_usd': str(self.limit),
            'calls_finished': {k: len(v) for k,v in self.costs.items()},
            'estimated_usage_calls': self.estimated_usage_calls,
            'stopped': self.stopped, 'reason': self.reason, 'reservation_usd': '0'}

    def __call__(self, event):
        kind = event['schema_name']
        if kind not in self.planned:
            raise BudgetStop('Unplanned operation')
        if event['event'] == 'prepared':
            if self.stopped or len(self.costs[kind]) >= self.planned[kind]:
                raise BudgetStop('Cycle cost/call limit reached')
            if Decimal(self.snapshot()['forecast_usd']) > self.limit:
                self.stopped, self.reason = True, 'forecast_exceeds_cap'
                raise BudgetStop(self.reason)
            return
        record = event['ledger']['records'][-1]
        value = record.get('estimated_cost_usd')
        if value is None:
            value = (sum(self.costs[kind])/len(self.costs[kind]) if self.costs[kind] else self.means[kind])
            self.estimated_usage_calls += 1
        self.costs[kind].append(Decimal(value))
        if Decimal(self.snapshot()['forecast_usd']) > self.limit:
            self.stopped, self.reason = True, 'forecast_exceeds_cap'


class ComparisonBudget:
    def __init__(self, pilot_means: dict[str, Decimal]):
        self.planned = {'company_info': 6, 'draft_sections': 24}
        if set(pilot_means) != set(self.planned) or any(v <= 0 for v in pilot_means.values()):
            raise ValueError('Complete nonzero pilot costs are required')
        self.pilot_means = pilot_means
        self.costs = {key: [] for key in self.planned}
        self.expected = sum(self.planned[k] * pilot_means[k] for k in self.planned)
        self.limit = self.expected * 2
        self.stopped = False
        self.reason = None

    def snapshot(self):
        spent = sum((sum(v) for v in self.costs.values()), Decimal(0))
        forecast = spent + sum((self.planned[k] - len(self.costs[k])) * max(
            self.pilot_means[k], sum(self.costs[k])/len(self.costs[k]) if self.costs[k] else Decimal(0))
            for k in self.planned)
        return {'pilot_expected_usd': str(self.expected), 'stop_limit_usd': str(self.limit),
                'known_spent_usd': str(spent), 'forecast_usd': str(forecast),
                'calls_finished': {k:len(v) for k,v in self.costs.items()},
                'stopped': self.stopped, 'reason': self.reason}

    def __call__(self, event):
        kind = event['schema_name']
        if kind not in self.planned:
            raise BudgetStop('Unplanned operation')
        if event['event'] == 'prepared':
            if self.stopped or len(self.costs[kind]) >= self.planned[kind]:
                raise BudgetStop('Cost or call limit reached')
            return
        record = event['ledger']['records'][-1]
        value = record['estimated_cost_usd']
        if value is None:
            self.stopped, self.reason = True, 'usage_unconfirmed'
            return
        self.costs[kind].append(Decimal(value))
        if Decimal(self.snapshot()['forecast_usd']) > self.limit:
            self.stopped, self.reason = True, 'forecast_exceeds_twice_pilot'


def pilot_measurements(paths: list[Path]):
    values = {'company_info': [], 'draft_sections': []}
    times = {k: [] for k in values}
    for path in paths:
        run = json.loads((path/'run.json').read_text(encoding='utf-8'))
        if not run['ledger']['cost_complete']:
            raise ValueError('Pilot usage is incomplete; do not estimate a new paid batch')
        for record in run['ledger']['records']:
            kind = record['operation']
            if kind in values:
                values[kind].append(Decimal(record['estimated_cost_usd']))
                times[kind].append(record['elapsed_ms']/1000)
    if any(len(v) != 2 for v in values.values()):
        raise ValueError('Need both pilot extraction and draft measurements from both arms')
    return {k:sum(v)/len(v) for k,v in values.items()}, {k:sum(v)/len(v) for k,v in times.items()}


class ExperimentRetryLedger(agent_llm.TrialLedger):
    """Explicit experiment exception; cumulative measured-cost guard is mandatory.

    Retains the product's usage/validation records and settings checks. Only
    transport retry capacity and reservation accounting differ from TrialLedger.
    """
    def __init__(self, *, shared: bool, timeout_retries: int):
        super().__init__(max_calls=8, budget_usd=Decimal('1'),
            timeout_limit_seconds=180, input_char_limit=40000, output_token_limit=32000)
        self._max_calls = (2 if shared else 4) * (1 + timeout_retries)
        self._operation_limits = {'company_info': 0 if shared else 2 * (1 + timeout_retries),
                                  'draft_sections': 2 * (1 + timeout_retries)}
        self._call_reserve = Decimal('0')
        self._budget = Decimal('1.50')


class TimeoutRetryRequester(agent_llm.OpenAIRequester):
    """Repeat the exact request only for an observed request_timeout, at most twice."""
    def __init__(self, *args, timeout_retries=2, record_retry=None, **kwargs):
        super().__init__(*args, **kwargs)
        if type(timeout_retries) is not int or not 0 <= timeout_retries <= 2:
            raise ValueError('Timeout retries must be 0..2')
        self.timeout_retries = timeout_retries
        self.transport_retries: list[dict] = []
        self.record_retry = record_retry

    def __call__(self, instructions, payload, schema, schema_name, *, images=None):
        fingerprint = digest(json_bytes({'instructions': instructions, 'payload': payload,
            'schema': schema, 'schema_name': schema_name, 'images': images}))
        for attempt in range(self.timeout_retries + 1):
            try:
                return super().__call__(instructions, payload, schema, schema_name, images=images)
            except agent_llm.AgentError:
                if self.ledger.snapshot()['stop_reason'] != 'request_timeout':
                    raise
                retry = {'schema_name': schema_name, 'request_sha256': fingerprint,
                    'failed_attempt': attempt + 1, 'retry_number': attempt + 1,
                    'delay_seconds': 5 * 3 ** attempt if attempt < self.timeout_retries else None,
                    'exhausted': attempt == self.timeout_retries, 'at': stamp()}
                self.transport_retries.append(retry)
                if self.record_retry is not None:
                    self.record_retry(retry)
                if retry['exhausted']:
                    raise
                time.sleep(retry['delay_seconds'])
                with self.ledger._lock:
                    if self.ledger._stop_reason != 'request_timeout':
                        raise
                    self.ledger._stop_reason = None


def run_case(*, label, brief, sources, manifest, assets, asset_manifest, prompt_bytes,
             prompt_version, options, record_run=False, repo=ROOT, requester_factory=None, renderer=None,
             rubric_sha256=None, rubric_bytes=None, shared_run: Path | None = None, budget_guard=None,
             display_company_name=None, demo=False, source_scope='include_demo', confirmed_contact=None,
             timeout_retries=0, recheck_shared_facts=False):
    """One extract + draft; explicit transport retries never relax content checks."""
    if type(timeout_retries) is not int or not 0 <= timeout_retries <= 2:
        raise ValueError('Timeout retries must be 0..2')
    if timeout_retries and (budget_guard is None or requester_factory is not None):
        raise ValueError('Transport retries require a cumulative cost guard and the standard requester')
    archive = RunArchive(enabled=record_run, label=label, repo=repo, secret_values=(options.api_key,))
    if archive.path is None:
        raise ValueError('Pilot requires explicit record_run=True; no API call made')
    original_prompt = agent_legacy.EXTRACT_PROMPT_PATH
    ledger = (ExperimentRetryLedger(shared=shared_run is not None, timeout_retries=timeout_retries)
        if timeout_retries or isinstance(budget_guard, CycleBudget) else agent_llm.TrialLedger(max_calls=2 if shared_run else 4, budget_usd=Decimal('0.50'),
        timeout_limit_seconds=180, input_char_limit=40000, output_token_limit=32000))
    if isinstance(budget_guard, CycleBudget):
        ledger._budget = Decimal('1.0')
    factory = requester_factory or agent_llm.OpenAIRequester
    render = renderer or export_render.render
    started = time.monotonic()
    stage = 'prepare'
    agent = None
    requester = None
    required = ['manifest.json','source_units.json','facts.json','fact_locations.json','draft_raw.json',
                'draft.json','document.json','output.pdf','brief.json','extract_prompt.txt','render.json',
                'call_1_finished.json'] + ([] if shared_run else ['call_2_finished.json'])
    try:
        if confirmed_contact is not None:
            if not isinstance(confirmed_contact, str) or len(confirmed_contact) > 500:
                raise ValueError('Confirmed contact must be a string of at most 500 characters')
            confirmed_contact = confirmed_contact.strip() or None
        archive.write('manifest.json', manifest)
        archive.write('source_units.json', sources)
        archive.write('asset_manifest.json', [{**item, 'archive_file': f'asset_{i}.bin'}
                                              for i, item in enumerate(asset_manifest, 1)])
        for i, (aid, asset) in enumerate(assets.items(), 1):
            archive.write_bytes(f'asset_{i}.bin', asset.data)
        archive.write_bytes('extract_prompt.txt', prompt_bytes)
        archive.write_bytes('draft_prompt.txt', agent_legacy.DRAFT_PROMPT_PATH.read_bytes())
        archive.write('brief.json', brief)
        archive.write('contact_setting.json', {'value': confirmed_contact or None,
            'origin': 'company_confirmed_operator_setting', 'configured': bool(confirmed_contact)})
        if rubric_bytes is not None:
            if digest(rubric_bytes) != rubric_sha256:
                raise ValueError('Rubric hash mismatch')
            archive.write_bytes('rubric.json', rubric_bytes)
        archive.update(git_commit=git_revision(repo), extraction_prompt_version=prompt_version,
            extraction_review_version=2,
            extraction_prompt_file_sha256=digest(prompt_bytes), rubric_sha256=rubric_sha256,
            source_units_sha256=digest(json_bytes(sources)),
            settings={'model': options.model, 'reasoning_effort': 'medium', 'service_tier': 'default',
                      'timeout_seconds': options.timeout_seconds, 'max_retries': options.max_retries,
                      'max_output_tokens': options.max_output_tokens, 'max_input_chars': options.max_input_chars},
            brief=brief.model_dump(), actual_pdf_pages=None, display_company_name=display_company_name,
            demo=demo, source_scope=source_scope)
        agent_legacy.EXTRACT_PROMPT_PATH = archive.path / 'extract_prompt.txt'
        def record_call(event):
            if budget_guard is not None and event['event'] == 'prepared':
                budget_guard(event)
            archive.record_call(event)
            if budget_guard is not None and event['event'] == 'finished':
                budget_guard(event)
                archive.update(batch_budget=budget_guard.snapshot())
        requester = (TimeoutRetryRequester(options, ledger=ledger, record_call=record_call,
            timeout_retries=timeout_retries,
            record_retry=lambda event: archive.update(transport_retry_events=[
                *archive.state.get('transport_retry_events', []), event]))
            if timeout_retries else factory(options, ledger=ledger, record_call=record_call))
        agent = agent_llm.LlmAgent(requester, max_input_chars=options.max_input_chars,
                                 display_company_name=display_company_name, source_scope=source_scope)
        sid = 'quality_eval_' + uuid.uuid4().hex
        request = AnalyzeRequest(sid, 1, brief, sources)
        stage = 'extraction'
        analyze_started = time.monotonic()
        if shared_run is None:
            analyzed = agent.analyze(request)
        else:
            shared = json.loads((shared_run/'run.json').read_text(encoding='utf-8'))
            if (not shared.get('extraction_validated')
                    or shared['source_units_sha256'] != digest(json_bytes(sources))
                    or shared['extraction_prompt_file_sha256'] != digest(prompt_bytes)
                    or shared['brief']['target_company'] != brief.target_company):
                raise ValueError('Shared extraction does not match this exact source/prompt/company')
            raw = (shared_run/'facts.json').read_bytes()
            if digest(raw) != shared['files']['facts.json']['sha256']:
                raise ValueError('Shared facts hash mismatch')
            facts = [Fact.model_validate(f) for f in json.loads(raw)]
            analysis = json.loads((shared_run/'analysis.json').read_text(encoding='utf-8'))
            if recheck_shared_facts:
                archive.write_bytes('facts_input.json', raw)
                facts, rechecks = agent_llm.recheck_recorded_facts(facts, agent_llm.SourceIndex(sources))
                archive.write('fact_guard_rechecks.json', rechecks)
                analysis['issues'] = [issue.model_dump() for issue in agent._issues(facts)]
            analyzed = AnalyzeResult(facts, [Issue.model_validate(i) for i in analysis['issues']],
                agent._recommendations(request, facts, has_text=True))
            archive.write_bytes('extraction_raw.json', (shared_run/'extraction_raw.json').read_bytes())
            archive.write('shared_extraction_usage.json', next(r for r in reversed(shared['ledger']['records'])
                if r['operation'] == 'company_info' and r['outcome'] == 'json_received'))
            archive.update(shared_extraction={'run':str(shared_run),'facts_sha256':digest(raw),
                                             'started_at':shared['started_at']})
        archive.write('facts.json', analyzed.facts)
        archive.write('extraction_preservation.json', agent.extraction_preservation if shared_run is None
            else json.loads((shared_run/'extraction_preservation.json').read_text('utf-8'))
            if (shared_run/'extraction_preservation.json').exists() else {'version': 0, 'replayed_legacy': True})
        archive.write('fact_locations.json', fact_locations(analyzed.facts, manifest))
        archive.write('analysis.json', analyzed)
        if validate_analyze(analyzed, sources):
            raise ValueError('Analysis failed existing server reference validation')
        archive.update(extraction_seconds=time.monotonic() - analyze_started, extraction_validated=True,
                       extraction_reused=shared_run is not None)
        preflight = PreflightOut(preflight_id='evaluation', session_id=sid, input_revision=1,
            usable_source_ids=[s.source_id for s in sources], facts=analyzed.facts, issues=analyzed.issues,
            recommendations=analyzed.recommendations, can_generate=bool(any(s.segments for s in sources)),
            confirmed_at=stamp())
        stage = 'draft'
        draft_request = DraftRequest(sid, 1, brief, sources, preflight)
        archive.write('draft_request.json', draft_request)
        draft_started = time.monotonic()
        result = agent.draft(draft_request)
        archive.write('draft.json', result)
        if validate_draft(result, sources, {f.fact_id for f in analyzed.facts}, preflight):
            raise ValueError('Draft failed existing server reference validation')
        archive.update(draft_seconds=time.monotonic() - draft_started)
        document = Document(document_id='evaluation', session_id=sid, document_revision=1,
            input_revision=1, title=result.title, target_pages=brief.target_pages, status='draft',
            pages=result.pages, editorial=result.editorial)
        archive.write('document.json', document)
        stage = 'render'
        effective_demo = demo or bool(agent.used_demo_sources)
        archive.update(demo=effective_demo, used_demo_sources=agent.used_demo_sources)
        if renderer is None:
            snapshot = export_render.snapshot_from_document(document, assets, demo=effective_demo,
                                                            confirmed_contact=confirmed_contact)
            pagination = export_render.paginate_draft(snapshot, archive.path / 'render',
                Settings(private_runs_dir=archive.path, db_path=archive.path/'unused.sqlite3'),
                reduction_priorities=export_render.draft_reduction_priorities(analyzed.facts, brief, result.editorial))
            agent.review_notes.extend(pagination.review_notes)
            archive.write('pagination.json', pagination)
            if pagination.pages != document.pages:
                document = document.model_copy(update={'pages': pagination.pages})
                archive.write('document.json', document)
        rendered = render(export_render.snapshot_from_document(document, assets, demo=effective_demo,
                                                               confirmed_contact=confirmed_contact),
                          'pdf', archive.path / 'render')
        archive.write_bytes('output.pdf', rendered.file_path.read_bytes())
        archive.write('render.json', rendered.to_dict())
        stage = 'verify'
        missing = [f for f in required if not (archive.path / f).is_file()]
        if missing:
            raise ValueError('Required artifacts missing')
        archive.update(status='complete', actual_pdf_pages=rendered.actual_pages,
                       layout_ok=rendered.layout_ok, required_artifacts_missing=[])
    except BaseException as exc:
        # Error messages/tracebacks can contain credentials or company text; store class and safe code only.
        archive.update(status='failed', failed_stage=stage, error_type=type(exc).__name__,
                       error_code=exc.code if isinstance(exc, agent_llm.AgentError) else None,
                       validation_message=exc.message if isinstance(exc, agent_llm.AgentError) else None)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        agent_legacy.EXTRACT_PROMPT_PATH = original_prompt
        if shared_run is None and agent is not None:
            archive.write('extraction_preservation.json', agent.extraction_preservation)
        attempts = agent.draft_attempts if agent is not None else []
        archive.write('fact_origins.json', agent.fact_origins if agent is not None else {})
        archive.write('rewrite_audit.json', agent.rewrite_audit if agent is not None else [])
        archive.write('presentation_facts.json', agent.presentation_facts if agent is not None else [])
        archive.write('semantic_duplicates.json', agent.semantic_duplicates if agent is not None else {})
        paths = {item['source_id']: item['path'] for item in manifest}
        review_notes = [{**note, 'source_files': [
            {'file': paths.get(ref['source_id']), 'locator': ref['locator'],
             'segment_id': ref['segment_id']} for ref in note['evidence_refs']]}
            for note in (agent.review_notes if agent is not None else [])]
        if not confirmed_contact:
            review_notes.append({'fact_id': None, 'value': None, 'withheld_from_body': True,
                'reason': '연락처 미설정: 회사 확인 후 실행 설정에 입력해야 합니다.',
                'evidence_refs': [], 'source_files': [], 'origin': 'operator_setting'})
        archive.write('review_notes.json', review_notes)
        archive.write('photo_review.json', getattr(agent, 'photo_audit', []))
        archive.update(draft_attempts=attempts, retry_count=max(0, len(attempts)-1),
                       draft_blocked=stage == 'draft' and archive.state['status'] == 'failed')
        if timeout_retries:
            transport = getattr(requester, 'transport_retries', [])
            exhausted = any(r['exhausted'] for r in transport)
            archive.update(transport_retry_count=sum(not r['exhausted'] for r in transport),
                transport_retry_events=transport, transport_retries_exhausted=exhausted,
                storage_retries=archive.storage_retries)
            if exhausted:
                archive.update(draft_blocked=False, failure_kind='api_timeout')
        archive.update(finished_at=stamp(), elapsed_seconds=time.monotonic() - started, ledger=ledger.snapshot(),
                       required_artifacts_missing=[f for f in required if not (archive.path/f).is_file()])
    return archive


def saved_document_signature(document: Document) -> dict:
    """Content/provenance identity independent of page grouping and design tokens."""
    blocks = [b.model_dump(mode='json') for p in document.pages for b in p.blocks]
    return {'blocks_sha256': digest(json_bytes(blocks)), 'block_count': len(blocks),
            'fact_ids': sorted({f for b in blocks for f in b['fact_ids']}),
            'images': [b['content'] for b in blocks if b['type'] == 'image']}


def layout_diagnostics(result) -> dict:
    """80% is descriptive only; it never participates in layout_ok or pagination."""
    measure = result.details.get('measure', {})
    limit = measure.get('limit_px')
    pages = measure.get('pages', [])
    ratios = [p['height_px'] / limit for p in pages] if limit else []
    body = ratios[1:] or ratios
    return {'page_occupancy': ratios, 'body_peak': max(body) if body else None,
            'body_over_80': sum(value > .8 for value in body),
            'minimum_body_pt': min((p['min_body_font_pt'] for p in pages
                if p.get('min_body_font_pt') is not None), default=None)}


def render_saved_comparison(manifest_path: Path, output: Path) -> list[dict]:
    """Local saved-input replay. No requester, key lookup, source extraction or DB."""
    from app.services import layout_checks
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not manifest.get('documents'):
        raise ValueError('Saved comparison requires explicit document/asset paths')
    # An existing run is immutable; a failed run must be reviewed, not overwritten.
    output.mkdir(parents=True, exist_ok=False)
    work_dir = output.parent / '_tmp'
    work_dir.mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(work_dir.resolve())
    results = []
    for entry in manifest['documents']:
        label = entry['label']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', label):
            raise ValueError('Invalid saved document label')
        source = Path(entry['document_path'])
        assets_dir = Path(entry['assets_dir'])
        original_bytes = source.read_bytes()
        document = Document.model_validate_json(original_bytes)
        before = saved_document_signature(document)
        asset_rows = json.loads((assets_dir / 'asset_manifest.json').read_text('utf-8'))
        assets, hashes = {}, {}
        for asset in asset_rows:
            path = (assets_dir / asset['archive_file']).resolve()
            if not path.is_relative_to(assets_dir.resolve()):
                raise ValueError('Asset archive escaped its directory')
            data = path.read_bytes()
            assets[asset['asset_id']] = export_render.asset_from_bytes(asset['asset_id'], data)
            hashes[asset['asset_id']] = digest(data)
        folder = output / label
        folder.mkdir()
        snapshot = export_render.snapshot_from_document(document, assets)
        result = export_render.render(snapshot, 'pdf', folder,
            Settings(private_runs_dir=folder, db_path=folder / 'unused.sqlite3',
                     export_work_dir=work_dir.resolve()))
        if source.read_bytes() != original_bytes or saved_document_signature(document) != before:
            raise ValueError('Saved replay mutated its input')
        # Compare PDF text per block, separately from ID/provenance preservation.
        from pypdf import PdfReader
        reader = PdfReader(result.file_path)
        compact = lambda text: re.sub(r'\s+', '', text)
        pdf_text = compact(''.join(p.extract_text() or '' for p in reader.pages))
        missing = []
        for page in document.pages:
            for block in page.blocks:
                content = block.content
                texts = ([content.get('caption', '')] if block.type == 'image' else
                         content.get('items', []) if block.type == 'list' else [content.get('text', '')])
                if any(compact(str(t)) not in pdf_text for t in texts if t):
                    missing.append(block.block_id)
        (folder / 'document.json').write_bytes(json_bytes(document))
        (folder / 'render.json').write_bytes(json_bytes(result.to_dict()))
        row = {'label': label, 'source_document': str(source.resolve()),
               'source_sha256': digest(original_bytes), 'signature': before, 'asset_hashes': hashes,
               'pdf': str(result.file_path.resolve()), 'pdf_sha256': digest(result.file_path.read_bytes()),
               'pages': result.actual_pages, 'target_pages': document.target_pages,
               'layout_ok': result.layout_ok, 'missing_text_blocks': missing,
               'diagnostics': layout_diagnostics(result)}
        results.append(row)
        record = {'root': str(ROOT.resolve()), 'renderer_module': str(Path(export_render.__file__).resolve()),
                  'git_commit': git_revision(), 'template_version': layout_checks.TEMPLATE_VERSION,
                  'template_fingerprint': export_render.template_fingerprint(), 'api_calls': 0,
                  'input_manifest_sha256': digest(manifest_path.read_bytes()), 'results': results}
        (output / 'results.json').write_bytes(json_bytes(record))
        print(json.dumps({k: row[k] for k in ['label', 'pages', 'layout_ok', 'missing_text_blocks', 'diagnostics']},
                         ensure_ascii=False), flush=True)
        if not result.layout_ok or missing or result.actual_pages != document.target_pages:
            raise ValueError(f'Saved replay failed preservation/layout: {label}')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--pilot', action='store_true', help='Two arms; includes bounded preservation/draft repairs')
    mode.add_argument('--main', action='store_true', help='Approved comparison: six extractions, 24 drafts')
    mode.add_argument('--cycle-arm', choices=('before','after'), help='Approved one-arm cycle: 3 extractions, 12 drafts, $1 cumulative cap')
    mode.add_argument('--saved-manifest', type=Path, help='Offline only: explicit saved documents/assets JSON')
    parser.add_argument('--saved-output', type=Path, help='New immutable folder for offline replay')
    parser.add_argument('--cycle-estimate', type=Path, help='Offline measured costs for the approved cycle')
    parser.add_argument('--cycle-baseline', type=Path, help='Original main frozen.json; sources/settings must match')
    parser.add_argument('--cycle-annotations', type=Path, help='Unchanged real/demo/contact rubric annotations')
    parser.add_argument('--pilot-runs', type=Path, nargs=2)
    parser.add_argument('--record-run', action='store_true', default=False)
    parser.add_argument('--source-scope', choices=('real_only', 'include_demo'), default='include_demo',
                        help='Draft source scope; extraction and saved facts are not modified')
    parser.add_argument('--target-company', help='Explicit company name for a paid comparison; no company default')
    parser.add_argument('--confirmed-contact', help='Company-confirmed contact; omitted means no contact in PDF')
    parser.add_argument('--rubric', type=Path, help='Pre-existing human rubric; hash only, never sent to the model')
    args = parser.parse_args(argv)
    if args.saved_manifest:
        if not args.saved_output:
            parser.error('--saved-manifest requires --saved-output')
        render_saved_comparison(args.saved_manifest, args.saved_output)
        return
    if not args.pilot and not args.main and not args.cycle_arm:
        print('No API calls. Recording default: off. Select an explicit mode with --record-run.')
        return
    if not args.record_run or args.rubric is None or not args.rubric.is_file():
        parser.error('Evaluation needs --record-run and an existing --rubric')
    if not args.target_company or not args.target_company.strip():
        parser.error('Evaluation needs an explicit --target-company')
    rubric_bytes = args.rubric.read_bytes()
    rubric_hash = digest(rubric_bytes)
    lock = json.loads(args.rubric.with_name('rubric_lock.json').read_text(encoding='utf-8'))
    if lock['sha256'] != rubric_hash or lock['status'] != 'approved_and_frozen':
        parser.error('Rubric must match its approved frozen hash')
    # Only API key is read from environment; no full settings/env dump or implicit .env load.
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        parser.error('OPENAI_API_KEY is required (value never printed)')
    options = agent_llm.LlmOptions(key, 'gpt-6-luna', 180, 0, 32000, 40000)
    sources, manifest, assets, asset_manifest = load_registered(
        ROOT/'private_runs/team_demo_20260930/app.sqlite3', ROOT/'private_runs/team_demo_20260930/registered')
    if len(manifest) != 21:
        raise ValueError('Pilot requires the fixed 21-file source set')
    before = subprocess.check_output(['git','show','94d1e3c^:prompts/extract.txt'],cwd=ROOT)
    after = agent_legacy.EXTRACT_PROMPT_PATH.read_bytes()
    commit = git_revision()
    guard = None
    if args.cycle_arm:
        if args.source_scope != 'real_only' or not args.cycle_estimate or not args.cycle_estimate.is_file():
            parser.error('Cycle needs real_only and an existing measured --cycle-estimate')
        if not args.cycle_baseline or not args.cycle_annotations:
            parser.error('Cycle needs frozen baseline and annotations')
        baseline = json.loads(args.cycle_baseline.read_text('utf-8'))
        if (baseline['source_units_sha256'] != digest(json_bytes(sources))
                or baseline['rubric_sha256'] != rubric_hash
                or baseline['annotation_sha256'] != digest(args.cycle_annotations.read_bytes())
                or baseline['prompt_hashes'] != {'before': digest(before), 'after': digest(after)}
                or baseline['settings']['model'] != options.model
                or baseline['settings']['reasoning'] != 'medium'
                or baseline['settings']['service_tier'] != 'default'
                or args.confirmed_contact != baseline['settings']['contact']
                or {(m['source_id'],m['sha256']) for m in manifest} !=
                   {(m['source_id'],m['sha256']) for m in baseline['manifest']}):
            raise ValueError('Frozen cycle inputs/settings do not match the original main experiment')
        estimate = json.loads(args.cycle_estimate.read_text('utf-8'))
        guard = CycleBudget(estimate['means'])
    if args.main:
        if not args.pilot_runs:
            parser.error('Main comparison requires the two measured --pilot-runs')
        means, mean_times = pilot_measurements(args.pilot_runs)
        for path in args.pilot_runs:
            pilot = json.loads((path/'run.json').read_text(encoding='utf-8'))
            if (pilot['git_commit'] != commit or pilot['rubric_sha256'] != rubric_hash
                    or pilot['source_units_sha256'] != digest(json_bytes(sources))
                    or pilot['settings']['model'] != options.model):
                raise ValueError('Pilot code, rubric, sources, and model must match main run')
        if {json.loads((p/'run.json').read_text(encoding='utf-8'))['extraction_prompt_file_sha256']
                for p in args.pilot_runs} != {digest(before), digest(after)}:
            raise ValueError('Pilot prompt pair differs from main comparison')
        guard = ComparisonBudget(means)
    batch = RunArchive(enabled=True, label='cycle_batch' if args.cycle_arm else 'main_batch' if args.main else 'pilot_batch', secret_values=(key,))
    batch.write('rubric_lock.json', lock)
    batch.write('manifest.json', manifest)
    if args.cycle_arm:
        batch.write('baseline_frozen.json', baseline)
        batch.write_bytes('rubric_annotations.json', args.cycle_annotations.read_bytes())
    batch.update(git_commit=commit, rubric_sha256=rubric_hash, runs=[],
                 planned_calls={'extraction':3,'supplement_max':3,'draft':12,'draft_repair_max':12}
                    if args.cycle_arm else {'extraction':6,'draft':24} if args.main else {'extraction':2,'draft':2})
    if guard:
        batch.update(budget=guard.snapshot(), pilot_mean_seconds=estimate['mean_seconds'] if args.cycle_arm else mean_times)
    stop = False
    for rep in range(1, 4 if args.main or args.cycle_arm else 2):
        for condition, prompt, version in [('before', before, '94d1e3c^'), ('after', after, commit)]:
            if args.cycle_arm and condition != args.cycle_arm:
                continue
            parent = None
            for doc, brief in fixed_briefs(args.target_company.strip()).items():
                if args.pilot and doc != 'new_customer':
                    continue
                if digest(args.rubric.read_bytes()) != rubric_hash or git_revision() != commit:
                    raise ValueError('Frozen rubric or code changed during evaluation')
                if guard and guard.stopped:
                    stop = True
                    break
                if doc != 'new_customer' and parent is None:
                    batch.state['runs'].append({'condition':condition,'rep':rep,'document':doc,
                                               'status':'skipped_extraction_failed'})
                    batch.update()
                    continue
                archive = run_case(label=('c' if args.cycle_arm else 'm' if args.main else 'p')+f'_{condition}_{rep}_{doc}',
                    brief=brief, sources=sources, manifest=manifest, assets=assets, asset_manifest=asset_manifest,
                    prompt_bytes=prompt, prompt_version=version, options=options, record_run=True,
                    rubric_sha256=rubric_hash, rubric_bytes=rubric_bytes, shared_run=parent, budget_guard=guard,
                    source_scope=args.source_scope, confirmed_contact=args.confirmed_contact,
                    display_company_name=baseline['settings']['display_name'] if args.cycle_arm else None)
                if doc == 'new_customer' and archive.state.get('extraction_validated'):
                    parent = archive.path
                item = {'condition':condition,'rep':rep,'document':doc,'run':str(archive.path),
                        'status':archive.state['status'],'calls':archive.state['ledger']['calls_started']}
                batch.state['runs'].append(item)
                batch.update(budget=guard.snapshot() if guard else None)
                print(json.dumps(item,ensure_ascii=False),flush=True)
            if stop:
                break
        if stop:
            break
    for item in manifest:
        if digest(Path(item['path']).read_bytes()) != item['sha256']:
            raise ValueError('Original source changed during evaluation')
    batch.update(status='stopped' if guard and guard.stopped else 'finished',finished_at=stamp())
    print(json.dumps({'batch':str(batch.path),'status':batch.state['status'],
                      'budget':guard.snapshot() if guard else None}),flush=True)


if __name__ == '__main__':
    main()
