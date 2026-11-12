from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager, suppress
import asyncio
import importlib.util
import io
import json
import os
import re
import zipfile
import tempfile
import hashlib
import shutil
import time
from urllib.parse import urlencode
from starlette.background import BackgroundTask

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import storage, pipeline, __version__
from .solo_tabs import current as current_tab
from .media import youtube_id
from .chord_dictionary import load_dictionary, valid_label
from .remote_access import allowed_api, allowed_origin
from .youtube_support import COOKIE_SECRET
from . import source_client
from .pages_gateway import MEDIA_ROUTE, SESSION_HEADER, SESSION_SECONDS, issue_token, pages_origin, token_account

WEB = Path(__file__).parent / 'web'


async def codespace_status():
    while True:
        projects = await asyncio.to_thread(storage.list_projects)
        queued = sum(project['status'] == 'queued' for project in projects)
        processing = sum(project['status'] == 'processing' for project in projects)
        print(f'FaixaLab ativo | {queued} na fila | {processing} em processamento', flush=True)
        await asyncio.sleep(180)


@asynccontextmanager
async def lifespan(app):
    # A previous abrupt shutdown must not leave a project pretending to be active.
    for p in storage.list_projects():
        if p['status'] in {'queued', 'processing'}:
            storage.update_project(p['id'], status='interrupted', stage='Processamento interrompido; importe novamente ou abra as faixas já salvas.')
        full = storage.read_project(p['id'])
        tab = full.get('tablature') or {}
        if tab.get('status') in {'queued', 'processing'}:
            storage.update_project(p['id'], tablature={**tab, 'status':'interrupted', 'stage':'Transcrição interrompida; gere a tablatura novamente.'})
    status_task = asyncio.create_task(codespace_status()) if os.environ.get('CODESPACES') == 'true' else None
    try:
        yield
    finally:
        if status_task:
            status_task.cancel()
            with suppress(asyncio.CancelledError):
                await status_task
        from .solo_tabs import cancel as cancel_tabs
        cancel_tabs()


app = FastAPI(title='FaixaLab', lifespan=lifespan)


@app.get('/connect-pages', include_in_schema=False)
def connect_pages():
    url = os.environ.get('FAIXALAB_PAGES_URL', '')
    key = os.environ.get('FAIXALAB_PAGES_KEY', '')
    if not url or not key or os.environ.get('CODESPACES') != 'true':
        raise HTTPException(404)
    pages_origin(url)
    expires = int(time.time()) + SESSION_SECONDS
    fragment = urlencode({'faixalab_api': issue_token(key, 'api', expires),
                          'faixalab_media': issue_token(key, 'media', expires)})
    return RedirectResponse(url + '#' + fragment, status_code=303,
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@app.middleware('http')
async def local_origin(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.url.path != '/api/health':
        if not allowed_api(request.headers):
            return JSONResponse({'detail': 'Acesso ao servidor de processamento não autorizado.'}, status_code=401)
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if not allowed_origin(request.headers, str(request.base_url).rstrip('/')):
            return JSONResponse({'detail': 'Origem não autorizada.'}, status_code=403)
    account = None
    if (token := request.headers.get(SESSION_HEADER)) is not None:
        key = os.environ.get('FAIXALAB_PAGES_KEY', '')
        account = token_account(token, key, 'api')
        if account is None and request.method in {'GET', 'HEAD'} and MEDIA_ROUTE.fullmatch(request.url.path):
            account = token_account(token, key, 'media')
        if not account:
            return JSONResponse({'detail': 'Sessão do FaixaLab inválida.'}, status_code=401)
    scope = storage.REQUEST_ACCOUNT.set(account)
    try:
        response = await call_next(request)
    finally:
        storage.REQUEST_ACCOUNT.reset(scope)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def project(identifier):
    try:
        item = storage.read_project(identifier)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, 'Projeto não encontrado.')
    if not storage.owns_project(item):
        raise HTTPException(404, 'Projeto não encontrado.')
    return item


class Options(BaseModel):
    stems: int = 6
    quality: str = 'balanced'
    language: str = ''
    lyrics: bool = True
    lyrics_model: str = 'small'
    device: str = 'auto'
    split_guitars: bool = True
    chord_stability: str = 'stable'
    instrument_detail: bool = True

    def checked(self):
        if self.stems not in {4, 6} or self.quality not in {'fast', 'balanced', 'quality'}:
            raise HTTPException(422, 'Opções de separação inválidas.')
        if self.language not in {'', 'pt', 'en', 'es', 'fr', 'it', 'de'} or self.lyrics_model not in {'base', 'small', 'medium'}:
            raise HTTPException(422, 'Opções de letra inválidas.')
        if self.device not in {'auto', 'cpu'}:
            raise HTTPException(422, 'Dispositivo inválido.')
        if self.chord_stability not in {'stable', 'detailed'}:
            raise HTTPException(422, 'Modo de cifra inválido.')
        result = self.model_dump()
        if self.stems == 4:
            result['split_guitars'] = False
            result['instrument_detail'] = False
        return result


class LinkInput(BaseModel):
    url: str = Field(max_length=2000)
    options: Options = Field(default_factory=Options)


def cloud_processing():
    return os.environ.get('CODESPACES') == 'true' or os.environ.get('FAIXALAB_REMOTE') == '1'


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': __version__, 'processing': 'cloud' if cloud_processing() else 'local',
            'engines': {n: importlib.util.find_spec(n) is not None for n in ['demucs', 'faster_whisper', 'librosa', 'yt_dlp']}}


@app.get('/api/connection')
def check_connection():
    # Unlike the liveness probe, this route verifies the gateway's secret first.
    return health()


@app.get('/service-worker.js')
def service_worker():
    return FileResponse(WEB/'service-worker.js',media_type='application/javascript',
                        headers={'Service-Worker-Allowed':'/','Cache-Control':'no-cache'})


@app.get('/api/projects')
def projects():
    return storage.list_projects(owned=True)


@app.get('/api/chord-dictionary')
def dictionary():
    return load_dictionary()


@app.get('/api/instruments')
def instruments_catalog():
    from .instrument_catalog import public_catalog
    return public_catalog()


class InstrumentSettings(BaseModel):
    selection: list[str] = Field(default_factory=list,max_length=18)


@app.post('/api/projects/{identifier}/refine-instruments',status_code=202)
def refine_other_instruments(identifier: str, settings: InstrumentSettings):
    from .instrument_catalog import checked_selection
    p=project(identifier)
    if p['status'] in {'queued','processing'} or not p.get('stems'):
        raise HTTPException(409,'Aguarde a separação atual terminar.')
    if not any(s['name']=='drums' for s in p['stems']):
        raise HTTPException(409,'Este projeto precisa concluir a separação inicial antes do refinamento.')
    try:selection=checked_selection(settings.selection)
    except ValueError as exc:raise HTTPException(422,str(exc))
    if not (storage.project_dir(identifier)/'original.wav').is_file():
        raise HTTPException(409,'O áudio original deste projeto não está disponível.')
    from .solo_tabs import cancel as cancel_tabs
    cancel_tabs(identifier)
    options=Options.model_validate({**p.get('options',{}),'instrument_detail':True}).checked()
    storage.update_project(identifier,options=options,instrument_settings={'selection':selection},
        status='queued',stage='Na fila para separar e verificar os instrumentos por IA',progress=0)
    pipeline.submit(identifier,retry='instruments')
    return {'queued':True,'mode':'manual' if selection else 'automatic'}


@app.get('/api/projects/{identifier}')
def get_project(identifier: str):
    return project(identifier)


@app.delete('/api/projects/{identifier}')
def delete_incomplete_import(identifier: str):
    with pipeline.JOB_LOCK, storage.LOCK:
        item = project(identifier)
        if identifier in pipeline.CANCELLATIONS or not storage.is_incomplete_youtube_import(item):
            raise HTTPException(409, 'Apenas tentativas de importação do YouTube encerradas sem áudio podem ser excluídas aqui.')
        # Move the whole directory out of the library before cleaning up files.
        # Import lookups already treat a missing project as a cache miss.
        with tempfile.TemporaryDirectory(dir=storage.ROOT, prefix='.deleted-import-',
                                         ignore_cleanup_errors=True) as temporary:
            storage.project_dir(identifier).rename(Path(temporary) / identifier)
    return {'deleted': True}


def checked_video_id(url):
    try:
        return youtube_id(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def file_import_key(digest, video_id=None):
    return f'youtube-audio:{video_id}:{digest}' if video_id else f'file:{digest}'


@app.post('/api/import/youtube', status_code=202)
def import_youtube(data: LinkInput):
    vid = checked_video_id(data.url)
    options=data.options.checked()
    with storage.LOCK:
        linked = storage.linked_youtube_audio(vid)
        if linked:
            digest = linked['input_sha256']
            source_key = file_import_key(digest, vid)
            cached = storage.cached_import(source_key, options)
            if cached:
                return {**cached, 'reused': True}
            source = linked['source']
            p = storage.new_project(linked['title'], source, options)
            folder = storage.project_dir(p['id'])
            original = storage.project_dir(linked['id']) / source['file']
            destination = folder / source['file']
            try:
                try:
                    os.link(original, destination)
                except OSError:
                    shutil.copyfile(original, destination)
                p = storage.update_project(p['id'], input_sha256=digest)
                storage.remember_import(source_key, options, p['id'])
            except OSError:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        else:
            try:
                remote_source = source_client.settings() is not None
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            source_key = ('youtube-faixalab:' if remote_source else 'youtube:') + vid
            cached = storage.cached_import(source_key, options)
            if cached:
                return {**cached, 'reused': True}
            if (not remote_source and os.environ.get('CODESPACES') == 'true'
                    and not os.environ.get(COOKIE_SECRET, '').strip()):
                raise HTTPException(503, f'Configure o segredo {COOKIE_SECRET} no Codespaces para importar links do YouTube.')
            source = {'type': 'youtube', 'url': 'https://www.youtube.com/watch?v=' + vid}
            if remote_source:
                source['provider'] = 'faixalab-source'
            p = storage.new_project('Música do YouTube', source, options)
            storage.remember_import(source_key, options, p['id'])
    pipeline.submit(p['id'])
    return p


@app.post('/api/import/file', status_code=202)
async def import_file(file: UploadFile = File(...), options: str = Form('{}'), youtube_url: str = Form('', max_length=2000)):
    try:
        parsed = Options.model_validate_json(options).checked()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Opções inválidas.')
    video_id = checked_video_id(youtube_url) if youtube_url else None
    name = Path(file.filename or 'musica.mp3').name
    suffix = Path(name).suffix.lower()
    if suffix not in {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.webm', '.mp4'}:
        raise HTTPException(422, 'Escolha um arquivo de áudio MP3, WAV, FLAC, M4A, AAC ou OGG.')
    source = {'type': 'file', 'name': name, 'file': 'input' + suffix}
    if video_id:
        source.update(youtube_id=video_id, url='https://www.youtube.com/watch?v=' + video_id)
    p = storage.new_project(Path(name).stem, source, parsed)
    target = storage.project_dir(p['id']) / source['file']
    total = 0
    digest=hashlib.sha256()
    try:
        with target.open('wb') as dest:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 1024 ** 3:
                    raise HTTPException(413, 'O arquivo excede 1 GB.')
                dest.write(chunk)
                digest.update(chunk)
        if total == 0:
            raise HTTPException(422, 'O arquivo está vazio.')
    except Exception as exc:
        storage.update_project(p['id'], status='failed', stage='Importação não concluída', error=str(exc))
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    audio_digest = digest.hexdigest()
    source_key = file_import_key(audio_digest, video_id)
    with storage.LOCK:
        cached=storage.cached_import(source_key,parsed)
        if cached:
            shutil.rmtree(storage.project_dir(p['id']))
            if video_id:
                storage.remember_youtube_audio(video_id, cached['id'])
            return {**cached,'reused':True}
        p = storage.update_project(p['id'], input_sha256=audio_digest)
        storage.remember_import(source_key,parsed,p['id'])
        if video_id:
            storage.remember_youtube_audio(video_id, p['id'])
    pipeline.submit(p['id'])
    return p


@app.post('/api/projects/{identifier}/cancel')
def cancel(identifier: str):
    project(identifier)
    return {'requested': pipeline.cancel(identifier)}


@app.post('/api/projects/{identifier}/retry/{stage}', status_code=202)
def retry(identifier: str, stage: str):
    p = project(identifier)
    if stage not in {'lyrics', 'chords', 'guitars'} or not p.get('stems') or p['status'] in {'queued', 'processing'}:
        raise HTTPException(409, 'Esta etapa não pode ser reiniciada agora.')
    storage.update_project(identifier, status='queued', stage='Na fila', progress=0)
    pipeline.submit(identifier, retry=stage)
    return {'queued': True}


@app.post('/api/projects/{identifier}/split-guitars', status_code=202)
def split_guitars(identifier: str):
    p = project(identifier)
    if p['status'] in {'queued', 'processing'} or not any(s['name'] == 'guitar' for s in p.get('stems', [])):
        raise HTTPException(409, 'Abra um projeto que já tenha uma faixa de guitarra única.')
    options = Options.model_validate({**p.get('options', {}), 'split_guitars': True}).checked()
    storage.update_project(identifier, options=options, status='queued', stage='Na fila para separar solo e base', progress=0)
    pipeline.submit(identifier, retry='guitars')
    return {'queued': True}


class RefineSettings(BaseModel):
    quality: str = 'quality'


@app.post('/api/projects/{identifier}/refine', status_code=202)
def refine(identifier: str, settings: RefineSettings | None = None):
    with pipeline.JOB_LOCK:
        p = project(identifier)
        if p['status'] in {'queued', 'processing'} or (not p.get('stems') and p['status'] not in {'failed', 'cancelled'}):
            raise HTTPException(409, 'Aguarde o processamento atual terminar.')
        if not (storage.project_dir(identifier) / 'original.wav').is_file():
            raise HTTPException(409, 'Importe o áudio novamente para separar este projeto.')
        quality = settings.quality if settings else 'quality'
        options = Options.model_validate({**p.get('options', {}), 'quality': quality}).checked()
        stage = 'Na fila para refinar' if p.get('stems') else 'Na fila para separar o áudio salvo'
        storage.update_project(identifier, options=options, status='queued', stage=stage, progress=0, error=None)
        pipeline.submit(identifier, retry='separation')
    return {'queued': True}


class ChordSettings(BaseModel):
    stability: str = 'stable'


@app.post('/api/projects/{identifier}/analyze-chords', status_code=202)
def analyze_chords(identifier: str, settings: ChordSettings):
    p = project(identifier)
    if not p.get('stems') or not (storage.project_dir(identifier) / 'original.wav').is_file():
        raise HTTPException(409, 'Aguarde a primeira separação terminar para recalcular a cifra.')
    options = Options.model_validate({**p.get('options', {}), 'chord_stability': settings.stability}).checked()
    stopping = pipeline.request_chords(identifier, options)
    return {'queued': True, 'stopping_separation': stopping}


class TabSettings(BaseModel):
    min_fret: int = Field(default=0, ge=0, le=23)
    max_fret: int = Field(default=24, ge=1, le=24)
    voices: int = Field(default=1, ge=1, le=2)
    preserve_edits: bool = True
    scope: Literal['solos', 'full'] = 'solos'


@app.post('/api/projects/{identifier}/solo-tabs', status_code=202)
def create_solo_tab(identifier: str, settings: TabSettings):
    project(identifier)
    if settings.min_fret >= settings.max_fret:
        raise HTTPException(422, 'Escolha uma região válida do braço.')
    from .solo_tabs import request as request_tab
    try:
        queued = request_tab(identifier, settings.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {'queued':queued}


@app.post('/api/projects/{identifier}/solo-tabs/cancel')
def cancel_solo_tab(identifier: str):
    project(identifier)
    from .solo_tabs import cancel as cancel_tabs
    cancel_tabs(identifier)
    return {'requested': True}


class TabAnnotation(BaseModel):
    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    string: int = Field(ge=0, le=5)
    fret: int = Field(ge=0, le=24)


@app.patch('/api/projects/{identifier}/solo-tabs')
def correct_solo_tab(identifier: str, data: TabAnnotation):
    from .solo_tabs import TUNING, note_name
    with storage.LOCK:
        p = project(identifier)
        tab = p.get('tablature') or {}
        notes = tab.get('notes') or []
        if not current_tab(tab, p):
            raise HTTPException(409, 'Conclua a transcrição da faixa atual antes de editar.')
        if data.index >= len(notes) or not data.start < data.end <= p.get('duration',0) + .05:
            raise HTTPException(422, 'Nota ou intervalo inválido.')
        if any(i != data.index and n.get('string') == data.string and n['start'] < data.end - .01 and n['end'] > data.start + .01 for i,n in enumerate(notes)):
            raise HTTPException(422, 'Essa corda já possui outra nota nesse intervalo.')
        pitch = TUNING[data.string] + data.fret
        notes[data.index] = {**notes[data.index], **data.model_dump(exclude={'index'}),
                             'midi':pitch, 'note':note_name(pitch), 'edited':True}
        notes.sort(key=lambda n:(n['start'],n['midi']))
        return storage.update_project(identifier, tablature={**tab,'notes':notes,
                                      'unmapped':sum(n.get('string') is None for n in notes)})


@app.delete('/api/projects/{identifier}/solo-tabs/{index}')
def delete_solo_note(identifier: str, index: int):
    with storage.LOCK:
        p=project(identifier);tab=p.get('tablature') or {};notes=tab.get('notes') or []
        if not current_tab(tab, p):
            raise HTTPException(409,'Conclua a transcrição da faixa atual antes de editar.')
        if not 0<=index<len(notes):
            raise HTTPException(422,'Nota inválida.')
        removed=notes.pop(index)
        excluded=[*tab.get('excluded_notes',[]),{key:removed[key] for key in ['start','end','midi']}]
        return storage.update_project(identifier,tablature={**tab,'notes':notes,'excluded_notes':excluded,
                                      'unmapped':sum(n.get('string') is None for n in notes)})


@app.get('/api/projects/{identifier}/audio/{name}')
def audio(identifier: str, name: str, download: bool = False, format: str = 'wav', v: str | None = None):
    p = project(identifier)
    if v is not None and len(v) > 128:raise HTTPException(404)
    revision = p.get('audio_revision') or 'original'
    if v is not None and v != revision:
        entry = (p.get('audio_history') or {}).get(v, {}).get(name)
    else:
        entry = next((s['file'] for s in p.get('stems', []) if s['name'] == name), None)
    if name == 'original':
        entry = 'original.wav'
    if entry is None:
        raise HTTPException(404)
    root = storage.project_dir(identifier)
    path = (root / entry).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404)
    if format not in {'wav','m4a'}:raise HTTPException(422,'Formato de áudio inválido.')
    if format=='m4a':
        from .backing import stream
        path=stream(path,root)
    return FileResponse(path, media_type='audio/mp4' if format=='m4a' else 'audio/wav',
                        filename=name+'.'+format if download else None,
                        headers={'Cache-Control':'private, max-age=31536000, immutable'} if v else {'Cache-Control':'no-cache'})


class BackingInput(BaseModel):
    preset: Literal['no_vocals_lead','no_vocals','mixer'] = 'no_vocals_lead'
    gains: dict[str,float] | None = None


@app.post('/api/projects/{identifier}/backing')
def prepare_backing(identifier: str, settings: BackingInput):
    p=project(identifier)
    if p['status'] not in {'ready','partial','cancelled','interrupted'} or not p.get('stems'):
        raise HTTPException(409,'Aguarde a separação das faixas para preparar a backing track.')
    from .backing import prepare
    try:return prepare(p,settings.gains if settings.preset=='mixer' else None,settings.preset)
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/projects/{identifier}/backing/{key}')
def get_backing(identifier: str, key: str, download: bool = False):
    p=project(identifier)
    if not re.fullmatch(r'[a-f0-9]{24}',key):raise HTTPException(404)
    path=storage.project_dir(identifier)/'mobile'/('backing_'+key+'.m4a')
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='audio/mp4',filename='FaixaLab_backing.m4a' if download else None)


class Annotation(BaseModel):
    chords: list[dict] | None = None
    lyrics: list[dict] | None = None


@app.patch('/api/projects/{identifier}/annotations')
def annotations(identifier: str, data: Annotation):
    p = project(identifier)
    changes = {}
    for kind, values in [('chords', data.chords), ('lyrics', data.lyrics)]:
        if values is None:
            continue
        if len(values) > 20000:
            raise HTTPException(422, 'Quantidade de trechos inválida.')
        cleaned = []
        last = -1
        original = {(v['start'], v['end']): v for v in p.get(kind, [])}
        for item in values:
            try:
                a, b = float(item['start']), float(item['end'])
                if not (0 <= a <= b <= p.get('duration', 0) + .1) or a < last:
                    raise ValueError()
                last = b
                text = str(item['label' if kind == 'chords' else 'text'])[:1500]
                if kind == 'chords' and not valid_label(text):
                    raise ValueError()
                field = 'label' if kind == 'chords' else 'text'
                prior = original.get((a, b))
                edited = prior is None or prior.get('edited', False) or prior[field] != text
                entry = {'start': a, 'end': b, field: text, 'edited': edited}
                if prior and prior[field] == text:
                    for preserved in ['confidence', 'words']:
                        if preserved in prior:
                            entry[preserved] = prior[preserved]
                cleaned.append(entry)
            except (ValueError, KeyError, TypeError):
                raise HTTPException(422, 'Trecho ou acorde inválido.')
        changes[kind] = cleaned
    return storage.update_project(identifier, **changes)


def lyric_text(p, lrc=False):
    lines = []
    for l in p.get('lyrics', []):
        t = l['start']
        prefix = f'[{int(t // 60):02d}:{t % 60:05.2f}]' if lrc else ''
        lines.append(prefix + l['text'])
    return '\n'.join(lines) + '\n'


def transpose_chord(label, amount):
    from .chords import NOTES
    flats = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#', 'Cb': 'B', 'Fb': 'E', 'E#': 'F', 'B#': 'C'}
    return re.sub(r'(^|/)([A-G](?:#|b)?)', lambda m: m[1] + NOTES[(NOTES.index(flats.get(m[2], m[2])) + amount) % 12], label)


def chord_text(p, transpose=0, chordpro=False):
    lines = [p['title'], 'Tom original estimado: ' + (p.get('key') or {}).get('label', '—'), '']
    if transpose:
        lines.append(f'Cifra transposta: {transpose:+d} semitons\n')
    if p.get('lyrics'):
        for line in p['lyrics']:
            if not line['text'].strip():
                continue
            words = line.get('words') or [{'text': t, 'start': line['start'] + i * (line['end'] - line['start']) / len(line['text'].split())} for i, t in enumerate(line['text'].split())]
            chords = [c for c in p.get('chords', []) if line['start'] <= c['start'] < line['end'] and c['label'] != 'N']
            carried = next((c for c in reversed(p.get('chords', [])) if c['start'] <= line['start'] and c['label'] != 'N'), None)
            if carried:
                chords.insert(0, {**carried, 'start': line['start']})
            assigned = {}
            for c in chords:
                index = min(range(len(words)), key=lambda i: abs(words[i]['start'] - c['start'])) if words else 0
                assigned[index] = transpose_chord(c['label'], transpose)
            if chordpro:
                lines.append(' '.join(('[' + assigned[i] + ']' if i in assigned else '') + w['text'] for i, w in enumerate(words)))
            else:
                positions, text = [], ''
                for w in words:
                    positions.append(len(text)); text += w['text'] + ' '
                top = ''
                for i, chord in sorted(assigned.items()):
                    top += ' ' * max(1 if top else 0, positions[i] - len(top)) + chord
                lines.extend([top, text.rstrip(), ''])
        return '\n'.join(lines) + '\n'
    for c in p.get('chords', []):
        t = c['start']
        lines.append(f'{int(t // 60):02d}:{t % 60:05.2f}  {transpose_chord(c["label"], transpose)}')
    return '\n'.join(lines) + '\n'


@app.get('/api/projects/{identifier}/export/{kind}')
def export(identifier: str, kind: str, transpose: int = 0):
    p = project(identifier)
    if abs(transpose) > 24:
        raise HTTPException(422, 'Transposição inválida.')
    if kind in {'solo-tab','solo-midi'}:
        tab = p.get('tablature') or {}
        if not current_tab(tab, p):
            raise HTTPException(409, 'Gere a tablatura da faixa atual antes de exportar.')
        from .solo_tabs import midi_bytes, text_tab
        content = midi_bytes(tab['notes']) if kind == 'solo-midi' else text_tab(p)
        return Response(content, media_type='audio/midi' if kind=='solo-midi' else 'text/plain; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="guitarra_solo.%s"' % ('mid' if kind=='solo-midi' else 'txt')})
    if kind in {'lyrics', 'lrc', 'chords', 'chordpro'}:
        text = chord_text(p, transpose, kind == 'chordpro') if kind in {'chords', 'chordpro'} else lyric_text(p, kind == 'lrc')
        filename = {'lyrics': 'letra.txt', 'lrc': 'letra.lrc', 'chords': 'cifra.txt', 'chordpro': 'cifra.cho'}[kind]
        return Response(text, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    if kind != 'zip':
        raise HTTPException(404)
    root = storage.project_dir(identifier)
    handle = tempfile.NamedTemporaryFile(prefix='export_', suffix='.zip', dir=root, delete=False)
    target = Path(handle.name)
    handle.close()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for s in p.get('stems', []):
            path = (root / s['file']).resolve()
            if path.is_relative_to(root) and path.is_file():
                archive.write(path, s['name'] + '.wav')
        archive.writestr('letra.lrc', lyric_text(p, True))
        archive.writestr('cifra.txt', chord_text(p, transpose))
        archive.writestr('cifra.cho', chord_text(p, transpose, True))
        tab = p.get('tablature') or {}
        if current_tab(tab, p):
            from .solo_tabs import text_tab, midi_bytes
            archive.writestr('guitarra_solo.txt', text_tab(p))
            archive.writestr('guitarra_solo.mid', midi_bytes(tab.get('notes',[])))
        archive.writestr('projeto.json', json.dumps(p, ensure_ascii=False, indent=2))
    return FileResponse(target, filename='FaixaLab_faixas_cifra_letra.zip', media_type='application/zip', background=BackgroundTask(target.unlink, missing_ok=True))


app.mount('/', StaticFiles(directory=WEB, html=True), name='web')
