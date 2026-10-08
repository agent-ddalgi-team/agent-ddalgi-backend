"""Opt-in local run archives and offline saved-document comparisons.

No server hooks, database access, product-policy overrides or automatic retries.
Run without arguments for a no-call status; --saved-manifest replays offline.
Paid pilot/main runs require an explicit source archive, company, prompt pair,
recording flag and a frozen rubric. Inputs and artifacts may contain private
material: keep them under ignored private_runs; never commit an archive.
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import os
import re
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
from app.models import Brief, Document, Fact, Issue
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

class RecordingRequester(agent_llm.OpenAIRequester):
    """Experiment-only observer of the unchanged product requester and ledger.

    Records the wire projection and provider response; the product still owns
    transport, decoding, aliases, per-fact status, validation and accounting.
    No callbacks or raw material are added to the server's Job trace (4405a5d).
    """
    def __init__(self, options, *, ledger=None, record_call=None, extract_prompt=None):
        super().__init__(options, ledger=ledger)
        self.record_call = record_call
        self.extract_prompt = extract_prompt
        self._response_record = {}

    def _decode(self, response):
        if self.record_call is not None:
            self._response_record = {'output_text': getattr(response, 'output_text', None),
                                     'actual_model': getattr(response, 'model', None)}
        return super()._decode(response)

    def __call__(self, instructions, payload, schema, schema_name, *, images=None):
        if self.extract_prompt is not None and schema_name == agent_legacy.MODEL_SCHEMA_NAME:
            original = agent_legacy.load_extract_prompt()
            if not instructions.startswith(original):
                raise ValueError('Unexpected extraction instructions; refusing prompt replacement')
            # Retain the current server's source_origins/metadata instructions.
            instructions = self.extract_prompt + instructions[len(original):]
        if self.record_call is None:
            return super().__call__(instructions, payload, schema, schema_name, images=images)
        if images:
            raise ValueError('Evaluation recorder supports text extraction/drafts only')
        wire, wire_schema = payload, schema
        if schema_name == agent_legacy.MODEL_SCHEMA_NAME and schema in (
                agent_legacy.build_model_output_schema(), agent_legacy.build_model_output_schema(per_fact_status=True)):
            wire, wire_schema, _ = agent_llm._extraction_wire_request(payload, schema)
        elif schema_name == 'draft_sections' and payload.get('prompt_version') == 'editorial_v2':
            wire, wire_schema, _ = agent_llm._editorial_wire_request(payload, schema)
        self.record_call({'event': 'prepared', 'schema_name': schema_name,
                          'instructions': instructions, 'payload': wire, 'schema': wire_schema})
        self._response_record = {}
        discarded = True
        try:
            result = super().__call__(instructions, payload, schema, schema_name)
            discarded = False
            return result
        finally:
            self.record_call({'event': 'finished', 'schema_name': schema_name,
                              'actual_model': None, **self._response_record,
                              'discarded': discarded, 'ledger': self.ledger.snapshot()})


def load_source_archive(folder: Path):
    """Read an explicitly supplied input snapshot; retain all SourceIn metadata.

    This is separate from server selection/Job tracing. Never discover a live DB
    or silently re-select excluded files. SourceIn JSON is the saved input.
    """
    folder = folder.resolve()
    rows = json.loads((folder / 'source_units.json').read_text(encoding='utf-8'))
    sources = [SourceIn(**{**row, 'segments': [SegmentIn(**s) for s in row['segments']]}) for row in rows]
    if not sources:
        raise ValueError('An explicit nonempty source snapshot is required')
    agent_llm.SourceIndex(sources)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if {s.source_id for s in sources} != {m['source_id'] for m in manifest}:
        raise ValueError('Source manifest does not match saved inputs')
    asset_manifest = json.loads((folder / 'asset_manifest.json').read_text(encoding='utf-8'))
    assets = load_assets(folder, asset_manifest)
    return sources, manifest, assets, asset_manifest


def load_assets(folder, rows):
    assets = {}
    for row in rows:
        path = (folder / row['archive_file']).resolve()
        if not path.is_relative_to(folder.resolve()) or row['asset_id'] in assets:
            raise ValueError('Invalid or duplicate archived asset')
        data = path.read_bytes()
        if digest(data) != row['sha256']:
            raise ValueError('Archived asset hash mismatch')
        asset = export_render.asset_from_bytes(row['asset_id'], data)
        if not asset.ok:
            raise ValueError('Archived asset failed integrity check')
        assets[row['asset_id']] = asset
    return assets


def fact_locations(facts, manifest):
    paths = {m['source_id']: m['path'] for m in manifest}
    return [{'fact_id': fact.fact_id, 'status': fact.status, 'value': fact.value,
             'locations': [{'file': paths[ref.source_id], **ref.model_dump()} for ref in fact.evidence_refs]}
            for fact in facts]

def prose_style_counts(text: str) -> dict[str, int]:
    """Same offline indicators for repair feedback and archived PDF review."""
    text = re.sub(r'\s+', ' ', text)
    certificate = r'인증서\s*(?:기준|에\s*기재된|에\s*표시된)'
    reporting = (r'자료\s*에는|소개\s*자료|소개\s*되어|기재\s*되어|'
        r'설명\s*(?:한다|합니다|되어)|제시\s*(?:한다|합니다)|열거\s*되어|언급\s*되어|'
        r'말씀\s*드립니다|안내\s*드립니다|이력이\s*있습니다|수록값\s*기준')
    return {'source_description': len(re.findall(reporting, re.sub(certificate, '', text))),
            'certificate_attribution': len(re.findall(certificate, text)),
            'plain_endings': len(re.findall(r'(?<!니)(?<!니 )다[.!?](?=\s|$)', text)),
            'formal_endings': len(re.findall(r'니\s*다[.!?](?=\s|$)', text))}

def editorial_checks(page_texts: list[str], document: dict | None = None) -> dict:
    """Offline indicators, not a factual verdict. Provenance counts require JSON.

    Title/label links are deliberately excluded from body-fact duplicate counts.
    PDF line wrapping is normalized; tables/fact IDs are checked in the draft.
    """
    pages = []
    for n, text in enumerate(page_texts, 1):
        text = re.sub(r'\s+', ' ', text)
        counts = prose_style_counts(text)
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

def run_case(*, label, brief, sources, manifest, assets, asset_manifest, prompt_bytes,
             prompt_version, options, record_run=False, repo=ROOT, renderer=None,
             rubric_sha256=None, rubric_bytes=None, shared_run=None, budget_guard=None):
    """Observe the current per-fact product path without changing its policies.

    Every case uses the existing bounded trial ledger, with no retry override.
    Archives are evaluation artifacts, never sessions or approved publications.
    """
    archive = RunArchive(enabled=record_run, label=label, repo=repo, secret_values=(options.api_key,))
    if archive.path is None:
        raise ValueError('Explicit record_run=True is required; no API call made')
    ledger = agent_llm.TrialLedger(max_calls=2 if shared_run else 4, budget_usd=Decimal('0.50'),
        timeout_limit_seconds=180, input_char_limit=40000, output_token_limit=32000)
    def record(event):
        if budget_guard and event['event'] == 'prepared':
            budget_guard(event)
        archive.record_call(event)
        if budget_guard and event['event'] == 'finished':
            budget_guard(event)
            archive.update(batch_budget=budget_guard.snapshot())
    requester = RecordingRequester(options, ledger=ledger, record_call=record,
                                   extract_prompt=prompt_bytes.decode('utf-8'))
    agent = agent_llm.LlmAgent(requester, max_input_chars=options.max_input_chars, per_fact_status=True)
    required = ['manifest.json','source_units.json','facts.json','fact_locations.json','analysis.json',
                'draft_raw.json','draft.json','document.json','output.pdf','brief.json','extract_prompt.txt',
                'render.json','call_1_finished.json'] + ([] if shared_run else ['call_2_finished.json'])
    stage, started = 'prepare', time.monotonic()
    try:
        archive.write('manifest.json', manifest)
        archive.write('source_units.json', sources)
        if set(assets) != {a['asset_id'] for a in asset_manifest} or len(assets) != len(asset_manifest):
            raise ValueError('Asset manifest must cover exactly the supplied assets')
        saved_assets = []
        for i, row in enumerate(asset_manifest, 1):
            data = assets[row['asset_id']].data
            if digest(data) != row['sha256']:
                raise ValueError('Asset manifest hash mismatch')
            name = f'asset_{i}.bin'
            archive.write_bytes(name, data)
            saved_assets.append({**row, 'archive_file': name})
        archive.write('asset_manifest.json', saved_assets)
        archive.write_bytes('extract_prompt.txt', prompt_bytes)
        archive.write_bytes('draft_prompt.txt', agent_legacy.DRAFT_PROMPT_PATH.read_bytes())
        archive.write('brief.json', brief)
        if rubric_bytes is not None:
            if digest(rubric_bytes) != rubric_sha256:
                raise ValueError('Rubric hash mismatch')
            archive.write_bytes('rubric.json', rubric_bytes)
        archive.update(git_commit=git_revision(repo), extraction_prompt_version=prompt_version,
            extraction_schema='per_fact_status', extraction_prompt_file_sha256=digest(prompt_bytes),
            rubric_sha256=rubric_sha256, source_units_sha256=digest(json_bytes(sources)),
            settings={'model': options.model, 'reasoning_effort': 'medium', 'service_tier': 'default',
                      'timeout_seconds': options.timeout_seconds, 'max_retries': options.max_retries,
                      'max_output_tokens': options.max_output_tokens, 'max_input_chars': options.max_input_chars},
            brief=brief.model_dump(), actual_pdf_pages=None)
        sid = 'quality_eval_' + uuid.uuid4().hex
        request = AnalyzeRequest(sid, 1, brief, sources)
        stage = 'extraction'
        if shared_run is None:
            analyzed = agent.analyze(request)
        else:
            shared = json.loads((shared_run / 'run.json').read_text(encoding='utf-8'))
            if (not shared.get('extraction_validated')
                    or shared['git_commit'] != archive.state['git_commit']
                    or shared.get('extraction_schema') != 'per_fact_status'
                    or shared['source_units_sha256'] != archive.state['source_units_sha256']
                    or shared['extraction_prompt_file_sha256'] != digest(prompt_bytes)
                    or shared['settings'] != archive.state['settings']
                    or shared['brief']['target_company'] != brief.target_company):
                raise ValueError('Shared extraction inputs, schema, settings or code differ')
            saved = {}
            for name in ('facts.json', 'analysis.json', 'extraction_raw.json'):
                raw = (shared_run / name).read_bytes()
                if digest(raw) != shared['files'][name]['sha256']:
                    raise ValueError('Shared extraction hash mismatch')
                saved[name] = raw
            facts = [Fact.model_validate(f) for f in json.loads(saved['facts.json'])]
            analysis = json.loads(saved['analysis.json'])
            if analysis['facts'] != json.loads(saved['facts.json']):
                raise ValueError('Shared facts and analysis differ')
            analyzed = AnalyzeResult(facts, [Issue.model_validate(i) for i in analysis['issues']],
                agent._recommendations(request, facts, has_text=True))
            archive.write_bytes('extraction_raw.json', saved['extraction_raw.json'])
            archive.update(shared_extraction={'run': str(shared_run), 'facts_sha256': digest(saved['facts.json'])})
        archive.write('facts.json', analyzed.facts)
        archive.write('fact_locations.json', fact_locations(analyzed.facts, manifest))
        archive.write('analysis.json', analyzed)
        if validate_analyze(analyzed, sources):
            raise ValueError('Existing server analysis validation failed')
        archive.update(extraction_validated=True, extraction_reused=shared_run is not None)
        # Preserve test's readiness/blocker policy; do not manufacture can_generate.
        preflight = ai_preflight(request, analyzed)
        stage = 'draft'
        draft_request = DraftRequest(sid, 1, brief, sources, preflight)
        archive.write('draft_request.json', draft_request)
        result = agent.draft(draft_request)
        archive.write('draft.json', result)
        if validate_draft(result, sources, {f.fact_id for f in analyzed.facts}, preflight):
            raise ValueError('Existing server draft validation failed')
        document = Document(document_id='evaluation', session_id=sid, document_revision=1,
            input_revision=1, title=result.title, target_pages=brief.target_pages, status='draft',
            pages=result.pages, editorial=result.editorial)
        archive.write('document.json', document)
        stage = 'render'
        snapshot = export_render.snapshot_from_document(document, assets)
        if renderer is None:
            settings = Settings(private_runs_dir=archive.path, db_path=archive.path/'unused.sqlite3')
            pagination = export_render.paginate_draft(snapshot, archive.path/'pagination', settings)
            archive.write('pagination.json', pagination)
            document = document.model_copy(update={'pages': pagination.pages})
            archive.write('document.json', document)
            snapshot = export_render.snapshot_from_document(document, assets)
        rendered = (renderer or export_render.render)(snapshot, 'pdf', archive.path/'render')
        archive.write_bytes('output.pdf', rendered.file_path.read_bytes())
        archive.write('render.json', rendered.to_dict())
        stage = 'verify'
        if any(not (archive.path/name).is_file() for name in required):
            raise ValueError('Required artifacts missing')
        archive.update(status='complete', actual_pdf_pages=rendered.actual_pages, layout_ok=rendered.layout_ok,
                       publication_approved=False)
    except BaseException as exc:
        archive.update(status='failed', failed_stage=stage, error_type=type(exc).__name__,
                       error_code=exc.code if isinstance(exc, agent_llm.AgentError) else None)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        archive.update(finished_at=stamp(), elapsed_seconds=time.monotonic()-started, ledger=ledger.snapshot(),
                       required_artifacts_missing=[n for n in required if not (archive.path/n).is_file()])
    return archive


def ai_preflight(request, analyzed):
    from app.models import PreflightOut
    from app.services import preflights
    result = PreflightOut(preflight_id='evaluation', session_id=request.session_id,
        input_revision=request.input_revision, usable_source_ids=[s.source_id for s in request.sources if s.segments],
        facts=analyzed.facts, issues=analyzed.issues, recommendations=analyzed.recommendations,
        can_generate=any(s.segments for s in request.sources), confirmed_at=stamp())
    return preflights.apply_draft_readiness(result, request.brief, 'llm')


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
    from scripts.experiments.stage_runner import load_manifest
    manifest = {'documents': load_manifest(manifest_path.resolve())}
    if not manifest.get('documents'):
        raise ValueError('Saved comparison requires explicit document/asset paths')
    # An existing run is immutable; a failed run must be reviewed, not overwritten.
    output.mkdir(parents=True, exist_ok=False)
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
        assets = load_assets(assets_dir, asset_rows)
        hashes = {aid: digest(asset.data) for aid, asset in assets.items()}
        folder = output / label
        folder.mkdir()
        snapshot = export_render.snapshot_from_document(document, assets)
        result = export_render.render(snapshot, 'pdf', folder,
            Settings(private_runs_dir=folder, db_path=folder / 'unused.sqlite3'))
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
               'target_pages_match': result.actual_pages == document.target_pages,
               'diagnostics': layout_diagnostics(result)}
        results.append(row)
        record = {'root': str(ROOT.resolve()), 'renderer_module': str(Path(export_render.__file__).resolve()),
                  'git_commit': git_revision(), 'template_version': layout_checks.TEMPLATE_VERSION,
                  'template_fingerprint': export_render.template_fingerprint(), 'api_calls': 0,
                  'input_manifest_sha256': digest(manifest_path.read_bytes()), 'results': results}
        (output / 'results.json').write_bytes(json_bytes(record))
        print(json.dumps({k: row[k] for k in ['label', 'pages', 'layout_ok', 'missing_text_blocks', 'diagnostics']},
                         ensure_ascii=False), flush=True)
        if not result.layout_ok or missing:
            raise ValueError(f'Saved replay failed preservation/layout: {label}')
    return results

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--pilot', action='store_true', help='Two explicit prompts, current product extraction/draft, no automatic repairs')
    mode.add_argument('--main', action='store_true', help='Approved comparison: six extractions, 24 drafts')
    mode.add_argument('--saved-manifest', type=Path, help='Offline only: explicit saved documents/assets JSON')
    parser.add_argument('--saved-output', type=Path, help='New immutable folder for offline replay')
    parser.add_argument('--pilot-runs', type=Path, nargs=2)
    parser.add_argument('--record-run', action='store_true', default=False)
    parser.add_argument('--target-company', help='Explicit company name for a paid comparison; no company default')
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--before-prompt', type=Path)
    parser.add_argument('--after-prompt', type=Path)
    parser.add_argument('--rubric', type=Path, help='Pre-existing human rubric; hash only, never sent to the model')
    args = parser.parse_args(argv)
    if args.saved_manifest:
        if not args.saved_output:
            parser.error('--saved-manifest requires --saved-output')
        from scripts.experiments.stage_runner import offline_network
        with offline_network():
            render_saved_comparison(args.saved_manifest, args.saved_output)
        return
    if not args.pilot and not args.main:
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
    if not args.source_archive or not args.before_prompt or not args.after_prompt:
        parser.error('Paid comparison requires --source-archive, --before-prompt and --after-prompt')
    sources, manifest, assets, asset_manifest = load_source_archive(args.source_archive)
    before, after = args.before_prompt.read_bytes(), args.after_prompt.read_bytes()
    commit = git_revision()
    guard = None
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
    batch = RunArchive(enabled=True, label='main_batch' if args.main else 'pilot_batch', secret_values=(key,))
    batch.write('rubric_lock.json', lock)
    batch.write('manifest.json', manifest)
    batch.update(git_commit=commit, rubric_sha256=rubric_hash, runs=[],
                 planned_calls={'extraction':6,'draft':24} if args.main else {'extraction':2,'draft':2})
    if guard:
        batch.update(budget=guard.snapshot(), pilot_mean_seconds=mean_times)
    stop = False
    for rep in range(1, 4 if args.main else 2):
        for condition, prompt, version in [('before', before, digest(before)), ('after', after, commit)]:
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
                archive = run_case(label=('m' if args.main else 'p')+f'_{condition}_{rep}_{doc}',
                    brief=brief, sources=sources, manifest=manifest, assets=assets, asset_manifest=asset_manifest,
                    prompt_bytes=prompt, prompt_version=version, options=options, record_run=True,
                    rubric_sha256=rubric_hash, rubric_bytes=rubric_bytes, shared_run=parent, budget_guard=guard)
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
    batch.update(status='stopped' if guard and guard.stopped else 'finished',finished_at=stamp())
    print(json.dumps({'batch':str(batch.path),'status':batch.state['status'],
                      'budget':guard.snapshot() if guard else None}),flush=True)

if __name__ == '__main__':
    main()
