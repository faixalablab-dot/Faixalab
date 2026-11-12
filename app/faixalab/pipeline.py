from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import threading
import traceback
import json
import uuid
import time

from . import storage
from .media import Cancelled, decode_audio, download_youtube, run_process, waveform

from .compute import worker_environment
from . import __version__


def run_neural_worker(args, cancel, on_line, environment, *, cwd, on_tick, on_fallback):
    """Retry once on CPU when an existing GPU cannot run this workload."""
    position = args.index('--device') + 1
    device = args[position]
    try:
        run_process(args, cancel, on_line, environment, cwd=cwd, on_tick=on_tick)
        return device, None
    except RuntimeError as error:
        message = str(error)
        recoverable = ('cuda out of memory', 'cuda error: out of memory',
                       'no kernel image is available', 'cuda driver version is insufficient')
        if device != 'cuda' or not any(s in message.lower() for s in recoverable):
            raise
        if cancel.is_set():
            raise Cancelled()
        on_fallback()
        retry_args = list(args)
        retry_args[position] = 'cpu'
        run_process(retry_args, cancel, on_line, environment, cwd=cwd, on_tick=on_tick)
        return 'cpu', message[-1500:]


def transcription_audio(project, folder):
    """Prefer an isolated vocal, including the initial separation if needed.

    Detailed timbre verification can omit a vocal candidate. Do not make ASR
    decode the entire instrumental mix when a saved vocal already exists.
    """
    candidates = [(s.get('file'), 'current_vocals') for s in project.get('stems', [])
                  if s.get('name') == 'vocals']
    candidates += [(revision.get('vocals'), 'previous_vocals')
                   for revision in reversed(list((project.get('audio_history') or {}).values()))]
    root = folder.resolve()
    for relative, kind in candidates:
        if not relative:
            continue
        path = (folder/relative).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path, kind
    return folder/'original.wav', 'original'

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='faixalab')
CANCELLATIONS = {}
PENDING_RETRIES = {}
JOB_LOCK = threading.RLock()
STEM_LABELS = {'vocals': 'Voz', 'drums': 'Bateria', 'bass': 'Baixo',
               'guitar': 'Guitarra / violão', 'guitar_lead': 'Guitarra solo',
               'guitar_rhythm': 'Guitarra base', 'piano': 'Piano', 'other': 'Outros instrumentos'}


def refine_instruments(p, folder, options, cancel, progress, base=16):
    """Run a separate, bounded-memory worker; retain the original drums file."""
    from .instrument_catalog import CATALOG
    drum=next((s for s in p.get('stems',[]) if s['name']=='drums'),None)
    if not drum:raise ValueError('Este projeto precisa de uma faixa de bateria já separada.')
    root=folder.resolve()
    sources=[]
    for stem in p['stems']:
        path=(folder/stem['file']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Uma das faixas do projeto não está disponível.')
        if stem['name']!='drums':sources.append(str(path))
    destination=folder/'separated'/'instruments'/uuid.uuid4().hex
    destination.mkdir(parents=True)
    manifest=destination/'scan_sources.json'
    manifest.write_text(json.dumps(sources),'utf-8')
    worker=sys.executable
    if os.name=='nt' and Path(worker).with_name('python.exe').is_file():
        worker=str(Path(worker).with_name('python.exe'))
    import torch
    device='cuda' if options.get('device','auto')!='cpu' and torch.cuda.is_available() else 'cpu'
    settings=p.get('instrument_settings') or {}
    environment=worker_environment()
    stage,value='Preparando refinamento dos instrumentos',base
    def line(text):
        nonlocal stage,value
        if text.startswith('FAIXALAB_INSTRUMENTS '):
            message=json.loads(text.split(' ',1)[1]);stage=message['stage'];value=base+(74-base)*message['fraction']
            progress(stage,value)
    args=[worker,'-m','faixalab.instrument_runner',str(folder/'original.wav'),str(destination),
          '--drums',str(folder/drum['file']),'--cache',str(storage.MODELS/'instruments'),
          '--scan-manifest',str(manifest),'--selection',json.dumps(settings.get('selection',[])),
          '--device',device]
    progress(stage,value)
    try:
        device,fallback=run_neural_worker(args,cancel,line,environment,cwd=str(Path(__file__).resolve().parent.parent),
                    on_tick=lambda runtime:progress(stage,value,worker_runtime=runtime),
                    on_fallback=lambda:progress('GPU indisponível para este processamento; continuando na CPU',value))
    except Exception:
        import shutil
        shutil.rmtree(destination,ignore_errors=True)
        raise
    details=json.loads((destination/'engine.json').read_text('utf-8'))
    if fallback:details['acceleration_fallback']=fallback
    stems=[dict(drum)]
    for name in details['retained']+['other']:
        path=destination/(name+'.wav')
        identity=details.get('validation',{}).get(name)
        label=CATALOG[name]['label'] if name in CATALOG else 'Outros instrumentos'
        if identity and identity.get('identity_state')=='uncertain':label+=' · estimativa'
        stems.append({'name':name,'label':label,
                      'waveform':waveform(path,count=1400),'file':path.relative_to(folder).as_posix(),
                      'instrument_identity':identity})
    order=['vocals','drums','bass','guitar','acoustic_guitar','accordion','harmonica','piano']
    stems.sort(key=lambda s:order.index(s['name']) if s['name'] in order else len(order)+(s['name']=='other'))
    engine={**p.get('engine',{}),'engine':'Demucs drums + MVSep Mega53 + EfficientAT',
            'model':details['model'],'device':device,'instrument_refinement':details}
    engine.pop('guitar_split',None)
    return stems,engine


def separate(audio, folder, options, cancel, progress):
    import torch
    quality = options.get('quality', 'balanced')
    model = ('studio_v2_' if quality == 'quality' else quality + '_v2_') + str(options['stems'])
    device = 'cuda' if options.get('device', 'auto') != 'cpu' and torch.cuda.is_available() else 'cpu'
    output = folder / 'separated'
    destination = output / model / (audio.stem + '_' + uuid.uuid4().hex[:12])
    worker_python = sys.executable
    if os.name == 'nt':
        console_python = Path(sys.executable).with_name('python.exe')
        if console_python.is_file():
            worker_python = str(console_python)
    args = [worker_python, '-m', 'faixalab.separator_runner', str(audio), str(destination),
            '--stems', str(options['stems']), '--quality', quality, '--device', device]
    environment = worker_environment()
    environment['TORCH_HOME'] = str(storage.MODELS / 'torch')

    highwater = 15
    current_stage = 'Preparando o modelo de separação'
    def line(text):
        nonlocal highwater, current_stage
        if text.startswith('FAIXALAB_PROGRESS '):
            message = json.loads(text.split(' ', 1)[1])
            highwater = max(highwater, 16 + 59 * message['fraction'])
            current_stage = message['stage']
            progress(current_stage, highwater)
            return
        if 'Downloading' in text or 'download' in text.lower():
            current_stage = 'Preparando o modelo de separação — primeiro uso'
            progress(current_stage, highwater)

    progress('Separando os instrumentos' + (' • GPU' if device == 'cuda' else ' • CPU'), 15)
    device,fallback=run_neural_worker(args, cancel, line, environment, cwd=str(Path(__file__).resolve().parent.parent),
                on_tick=lambda runtime: progress(current_stage, highwater, worker_runtime=runtime),
                on_fallback=lambda:progress('GPU indisponível para este processamento; continuando na CPU',highwater))
    stems = []
    for name in ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other']:
        path = destination / (name + '.wav')
        if path.is_file():
            stems.append({'name': name, 'label': STEM_LABELS[name], 'waveform': waveform(path, count=1400),
                          'file': str(path.relative_to(folder)).replace('\\', '/')})
    if len(stems) != options['stems']:
        raise RuntimeError('O modelo não retornou todas as faixas esperadas.')
    details = json.loads((destination / 'engine.json').read_text('utf-8'))
    if fallback:details['acceleration_fallback']=fallback
    return stems, {'engine': 'Demucs', 'device': device, **details}


def split_guitars(p, folder, options, cancel, progress):
    combined = next((s for s in p['stems'] if s['name'] == 'guitar'), p.get('guitar_source'))
    if not combined:
        raise RuntimeError('Este projeto ainda não possui uma faixa de guitarra para detalhar.')
    destination = folder / 'separated' / 'guitar_roles' / uuid.uuid4().hex
    worker = sys.executable
    if os.name == 'nt' and Path(worker).with_name('python.exe').is_file():
        worker = str(Path(worker).with_name('python.exe'))
    device = (p.get('engine') or {}).get('device', 'cpu')
    environment = worker_environment()
    current_stage, current_value = 'Preparando separação de guitarra solo e base', 75
    def line(text):
        nonlocal current_stage, current_value
        if text.startswith('FAIXALAB_GUITARS '):
            value = json.loads(text.split(' ', 1)[1])
            current_stage, current_value = value['stage'], 75 + 3 * value['fraction']
            progress(current_stage, current_value)
    progress('Preparando separação de guitarra solo e base', 75)
    device,fallback=run_neural_worker([worker, '-m', 'faixalab.lead_rhythm', str(folder / combined['file']),
                 str(destination), '--cache', str(storage.MODELS / 'guitars'), '--device', device],
                cancel, line, environment, cwd=str(Path(__file__).resolve().parent.parent),
                on_tick=lambda runtime: progress(current_stage, current_value, worker_runtime=runtime),
                on_fallback=lambda:progress('GPU indisponível para este processamento; continuando na CPU',current_value))
    extras = []
    for name in ['guitar_lead', 'guitar_rhythm']:
        path = destination / (name + '.wav')
        extras.append({'name': name, 'label': STEM_LABELS[name], 'waveform': waveform(path, count=1400),
                       'file': path.relative_to(folder).as_posix()})
    stems = [s for s in p['stems'] if s['name'] not in {'guitar', 'guitar_lead', 'guitar_rhythm'}] + extras
    order = ['vocals', 'drums', 'bass', 'guitar_lead', 'guitar_rhythm', 'piano', 'other']
    stems.sort(key=lambda s: order.index(s['name']) if s['name'] in order else len(order)+(s['name']=='other'))
    engine = {**p.get('engine', {}), 'device':device, 'guitar_split': json.loads((destination / 'engine.json').read_text('utf-8'))}
    if fallback:engine['guitar_split']['acceleration_fallback']=fallback
    return stems, engine, combined


def transcribe(audio, options, cancel, progress):
    from faster_whisper import WhisperModel
    model_size = options.get('lyrics_model', 'small')
    progress('Preparando o modelo de letra — primeiro uso pode exigir download', 86)
    kwargs = dict(device='cpu', compute_type='int8', cpu_threads=max(1,min(4,int(os.environ.get('FAIXALAB_CPU_THREADS',os.cpu_count() or 2)))),
                  download_root=str(storage.MODELS / 'whisper'))
    try:
        model = WhisperModel(model_size, local_files_only=True, **kwargs)
    except Exception:
        model = WhisperModel(model_size, local_files_only=False, **kwargs)
    language = options.get('language') or None
    segments, info = model.transcribe(str(audio), language=language,
                                      word_timestamps=True, beam_size=5,
                                      vad_filter=False, condition_on_previous_text=False,
                                      no_speech_threshold=.6)
    lyrics = []
    duration = max(float(info.duration), 1)
    for segment in segments:
        if cancel.is_set():
            raise Cancelled()
        text = segment.text.strip()
        if text:
            lyrics.append({'start': round(segment.start, 3), 'end': round(segment.end, 3),
                           'text': text, 'words': [{'start': round(w.start, 3), 'end': round(w.end, 3), 'text': w.word.strip(), 'confidence': round(w.probability, 3)} for w in (segment.words or [])]})
        progress('Transcrevendo a voz e sincronizando a letra', min(98, 86 + 12 * segment.end / duration))
    return lyrics, {'model': model_size, 'language': info.language,
                    'status': 'ready' if lyrics else 'no_speech'}


def submit(identifier, retry=None):
    with JOB_LOCK:
        event = threading.Event()
        CANCELLATIONS[identifier] = event
        POOL.submit(process_project, identifier, event, retry)


def cancel(identifier):
    with JOB_LOCK:
        PENDING_RETRIES.pop(identifier, None)
        event = CANCELLATIONS.get(identifier)
        if event:
            event.set()
        return bool(event)


def request_chords(identifier, options):
    """Stop the current worker before queuing analysis of the saved stems."""
    with JOB_LOCK:
        event = CANCELLATIONS.get(identifier)
        if event:
            PENDING_RETRIES[identifier] = options
            storage.update_project(identifier, stage='Parando a separação para recalcular a cifra',
                                   chord_analysis_pending=True)
            event.set()
        else:
            storage.update_project(identifier, options=options, status='queued',
                                   stage='Na fila para recalcular a cifra', progress=0,
                                   chord_analysis_pending=True, worker_runtime=None)
            submit(identifier, retry='chords')
        return bool(event)


def process_project(identifier, cancel_event, retry=None):
    folder = storage.project_dir(identifier)
    warnings = []
    started = time.monotonic()
    timings = {}

    def timed(stage, function, *args, **kwargs):
        began = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            timings[stage] = round(time.monotonic() - began, 6)
            storage.update_project(identifier, performance={
                'stages_seconds': dict(timings),
                'elapsed_seconds': round(time.monotonic() - started, 6),
                'operation': retry or 'import', 'version': __version__,
                'completed': False})

    def progress(stage, value, **details):
        if cancel_event.is_set():
            raise Cancelled()
        storage.update_project(identifier, status='processing', stage=stage, progress=round(value, 1),
                               elapsed_seconds=round(time.monotonic() - started, 1), **details)

    try:
        p = storage.read_project(identifier)
        options = p['options']
        first_separation = not retry or (retry == 'separation' and not p.get('stems'))
        def separation_progress(stage,value,**details):
            mapped=15+max(0,value-15)/3 if options.get('instrument_detail',False) else value
            progress(stage,mapped,**details)
        storage.update_project(identifier, worker_runtime=None,
                               chord_analysis_pending=(not retry or retry in {'chords', 'guitars', 'separation','instruments'}))
        if retry:
            cleared = {'chords', 'guitars', 'separation','instruments'} if retry == 'separation' else ({'guitars', 'chords'} if retry == 'guitars' else {'instruments','guitars','chords'} if retry=='instruments' else {retry})
            warnings = [w for w in p.get('warnings', []) if w.get('stage') not in cleared]
        audio = folder / 'original.wav'
        if not retry:
            progress('Preparando a música', 1)
            if p['source']['type'] == 'youtube':
                if p['source'].get('provider') == 'faixalab-source':
                    from .source_client import download_audio
                    source, title = timed('youtube_download', download_audio, p['source']['url'], folder, cancel_event, progress)
                else:
                    source, title = timed('youtube_download', download_youtube, p['source']['url'], folder, cancel_event, progress)
                storage.update_project(identifier, title=title)
            else:
                source = folder / p['source']['file']
            progress('Convertendo o áudio', 11)
            duration = timed('audio_decode', decode_audio, source, audio, cancel_event)
            storage.update_project(identifier, duration=round(duration, 3), waveform=waveform(audio))
            stems, engine = timed('initial_separation', separate, audio, folder, options, cancel_event, separation_progress)
            storage.update_project(identifier, stems=stems, engine=engine, audio_revision=uuid.uuid4().hex)
        elif retry == 'separation':
            stems, engine = timed('initial_separation', separate, audio, folder, options, cancel_event, separation_progress)
            storage.update_project(identifier, stems=stems, engine=engine, audio_revision=uuid.uuid4().hex)
        else:
            stems = p['stems']
        if retry=='instruments' or (options.get('instrument_detail',False) and (not retry or retry=='separation')):
            current=storage.read_project(identifier)
            stems,engine=timed('instrument_refinement',refine_instruments,current,folder,options,cancel_event,progress,
                                           base=16 if retry=='instruments' else 35)
            storage.update_project(identifier,stems=stems,engine=engine,guitar_source=None,
                                   audio_revision=uuid.uuid4().hex,worker_runtime=None)
        if options.get('split_guitars', False) and (not retry or retry in {'guitars', 'separation','instruments'}) and any(s['name']=='guitar' for s in stems):
            storage.update_project(identifier, worker_runtime=None)
            try:
                current = storage.read_project(identifier)
                stems, engine, combined = timed('guitar_roles', split_guitars, current, folder, options, cancel_event, progress)
                storage.update_project(identifier, stems=stems, engine=engine, guitar_source=combined,
                                       audio_revision=uuid.uuid4().hex)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append({'stage': 'guitars', 'message': 'A guitarra está pronta, mas solo e base ainda não foram separados: ' + str(exc)[-700:]})
        if not retry or retry in {'chords', 'separation', 'guitars','instruments'}:
            storage.update_project(identifier, worker_runtime=None)
            progress('Reconhecendo acordes e andamento', 78)
            try:
                from .chords import analyze, preserve_manual
                accompaniment = [s for s in stems if s['name'] in {'other', 'guitar', 'guitar_rhythm', 'acoustic_guitar','piano','electric_piano','organ'}]
                harmonic = [folder / s['file'] for s in accompaniment]
                dedicated = any(s['name'] != 'other' for s in accompaniment)
                weights = [.45 if s['name'] == 'other' and dedicated else 1. for s in accompaniment]
                bass = next((folder / s['file'] for s in stems if s['name'] == 'bass'), None)
                result = timed('chords', analyze, audio, harmonic, bass_path=bass, cancel=cancel_event,
                                 progress=lambda stage, value: progress(stage, min(value, 85)),
                                 harmonic_weights=weights,
                                 stability=options.get('chord_stability', 'stable'))
                if retry:
                    result['chords'] = preserve_manual(result['chords'], p.get('chords', []))
                storage.update_project(identifier, **result, chord_analysis_pending=False)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append({'stage': 'chords', 'message': 'Não foi possível calcular as cifras: ' + str(exc)[-500:]})
        if options.get('lyrics', True) and (first_separation or retry == 'lyrics'):
            try:
                vocals, vocal_kind = transcription_audio(storage.read_project(identifier), folder)
                lyrics, meta = timed('lyrics', transcribe, vocals, options, cancel_event, progress)
                meta['audio_source'] = vocal_kind
                storage.update_project(identifier, lyrics=lyrics, transcription=meta)
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append({'stage': 'lyrics', 'message': 'As faixas estão prontas, mas a letra não foi transcrita: ' + str(exc)[-700:]})
        progress('Finalizando o projeto', 99)
        storage.update_project(identifier, status='partial' if warnings else 'ready', stage='Pronto para tocar',
                               progress=100, warnings=warnings, error=None, worker_runtime=None,
                               performance={'stages_seconds':dict(timings), 'elapsed_seconds':round(time.monotonic()-started,6),
                                            'operation':retry or 'import', 'version':__version__, 'completed':True})
    except Cancelled:
        storage.update_project(identifier, status='cancelled', stage='Processamento cancelado', error=None)
    except Exception as exc:
        storage.update_project(identifier, status='failed', stage='Não foi possível concluir', error=str(exc)[-2000:])
        (folder / 'diagnostico.log').write_text(traceback.format_exc(), 'utf-8')
    finally:
        with JOB_LOCK:
            if CANCELLATIONS.get(identifier) is cancel_event:
                CANCELLATIONS.pop(identifier, None)
            pending = PENDING_RETRIES.pop(identifier, None)
            if pending is not None:
                storage.update_project(identifier, options=pending, status='queued',
                                       stage='Na fila para recalcular a cifra', progress=0,
                                       chord_analysis_pending=True, worker_runtime=None)
                submit(identifier, retry='chords')
