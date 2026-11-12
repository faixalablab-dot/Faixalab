"""Private server for fetching the audio that FaixaLab processes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import re
from threading import Event, Lock

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .media import download_youtube, youtube_id


class YouTubeRequest(BaseModel):
    url: str = Field(max_length=2000)


def create_app(root, key):
    if not key:
        raise ValueError('Configure FAIXALAB_AUDIO_SOURCE_KEY antes de iniciar a API de áudio.')
    root = Path(root).resolve()
    jobs = {}
    lock = Lock()
    stop = Event()
    pool = None

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal pool
        stop.clear()
        jobs.clear()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='faixalab-source')
        try:
            yield
        finally:
            stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            pool = None

    def authorize(request: Request):
        supplied = request.headers.get('authorization', '').encode('utf-8')
        if not hmac.compare_digest(supplied, f'Bearer {key}'.encode('utf-8')):
            raise HTTPException(401, 'A chave da API de áudio é inválida.', headers={'WWW-Authenticate': 'Bearer'})

    app = FastAPI(title='FaixaLab — API de áudio', lifespan=lifespan, dependencies=[Depends(authorize)],
                  docs_url=None, redoc_url=None, openapi_url=None)

    def cached_audio(identifier):
        directory = root / identifier
        try:
            metadata = json.loads((directory / 'audio.json').read_text('utf-8'))
            filename = metadata['file']
            if Path(filename).name == filename:
                audio = directory / filename
                if audio.is_file() and audio.stat().st_size:
                    return audio, metadata['title']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def update(identifier, **changes):
        with lock:
            jobs[identifier].update(changes)

    def run(identifier):
        directory = root / identifier
        update(identifier, status='processing', stage='Preparando o vídeo', progress=1)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            url = f'https://www.youtube.com/watch?v={identifier}'
            audio, title = download_youtube(url, directory, stop, lambda stage, progress: update(
                identifier, stage=stage, progress=progress))
            if audio.resolve().parent != directory or not audio.is_file() or not audio.stat().st_size:
                raise RuntimeError('O extrator não entregou um arquivo de áudio utilizável.')
            temporary = directory / 'audio.json.tmp'
            temporary.write_text(json.dumps({'file': audio.name, 'title': title}, ensure_ascii=False), 'utf-8')
            temporary.replace(directory / 'audio.json')
            update(identifier, status='ready', title=title, progress=100, stage='Áudio pronto')
        except Exception as exc:
            update(identifier, status='failed', error=str(exc)[-1500:], stage='A importação falhou')

    @app.get('/v1/health')
    def health():
        return {'status': 'ok', 'service': 'faixalab-audio-source'}

    if os.environ.get('FAIXALAB_ENABLE_POT_AB') == '1':
        from .pot_ab_test import run_probe

        probe = {'status': 'idle', 'rows': []}

        def update_probe(**changes):
            with lock:
                probe.update(changes)

        def run_pot_ab():
            try:
                run_probe(stop, update_probe)
            except Exception:
                update_probe(status='failed', error='provider_or_environment_unavailable')
            else:
                update_probe(status='interrupted' if stop.is_set() else 'finished')

        @app.get('/v1/diagnostics/pot-ab')
        def pot_ab_status():
            with lock:
                return dict(probe)

        @app.post('/v1/diagnostics/pot-ab', status_code=202)
        def submit_pot_ab():
            with lock:
                if pool is None:
                    raise HTTPException(503, 'A API de áudio está iniciando.')
                if probe['status'] == 'idle':
                    probe['status'] = 'running'
                    pool.submit(run_pot_ab)
                return dict(probe)

    @app.post('/v1/youtube-jobs', status_code=202)
    def submit(data: YouTubeRequest):
        try:
            identifier = youtube_id(data.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with lock:
            previous = jobs.get(identifier)
            if previous and previous['status'] in ('queued', 'processing'):
                return dict(previous)
            cached = cached_audio(identifier)
            if cached:
                return {'id': identifier, 'status': 'ready', 'title': cached[1], 'progress': 100}
            if pool is None:
                raise HTTPException(503, 'A API de áudio está iniciando.')
            job = {'id': identifier, 'status': 'queued', 'stage': 'Na fila', 'progress': 0}
            jobs[identifier] = job
            pool.submit(run, identifier)
            return dict(job)

    def validate_identifier(identifier):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
            raise HTTPException(404, 'Vídeo não encontrado.')

    @app.get('/v1/youtube-jobs/{identifier}')
    def status(identifier: str):
        validate_identifier(identifier)
        with lock:
            job = jobs.get(identifier)
            if job and job['status'] != 'ready':
                return dict(job)
            cached = cached_audio(identifier)
        if cached:
            return {'id': identifier, 'status': 'ready', 'title': cached[1], 'progress': 100}
        raise HTTPException(404, 'Vídeo não encontrado.')

    @app.get('/v1/youtube-jobs/{identifier}/audio')
    def audio(identifier: str):
        validate_identifier(identifier)
        cached = cached_audio(identifier)
        if not cached:
            raise HTTPException(404, 'O áudio ainda não está disponível.')
        return FileResponse(cached[0], filename=cached[0].name)

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description='FaixaLab — API de áudio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=os.environ.get('PORT', '7870'))
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get(
        'FAIXALAB_AUDIO_SOURCE_DATA', str(Path.home() / '.faixalab-audio-source'))))
    args = parser.parse_args()
    key = os.environ.get('FAIXALAB_AUDIO_SOURCE_KEY', '').strip()
    if not key:
        parser.error('Defina FAIXALAB_AUDIO_SOURCE_KEY.')
    uvicorn.run(create_app(args.data_dir, key), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
