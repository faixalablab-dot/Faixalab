"""Private audio adapter for the Invidious Companion trial."""
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlsplit

import httpx

from .media import Cancelled, youtube_id
from .youtube_native import validate_audio

VIDEOS = ('7iNbnineUCI', 'aqz-KE-bpKQ')
ORIGIN = 'http://127.0.0.1:8282'
MAX_BYTES = 32 * 1024 * 1024
LOG = logging.getLogger('uvicorn.error')


class Companion:
    def __init__(self):
        self.process = None
        self.key = secrets.token_hex(8)

    def start(self):
        if self.process and self.process.poll() is None:
            return
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'PATH', 'HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'DENO_TLS_CA_STORE'}}
        env.update(HOST='127.0.0.1', PORT='8282', SERVER_SECRET_KEY=self.key,
                   NETWORKING_FETCH_TIMEOUT_MS='12000', DENO_TLS_CA_STORE='system')
        proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        if proxy:
            env.update(PROXY=proxy, HTTPS_PROXY=proxy)
        binary = os.environ.get('FAIXALAB_COMPANION_BINARY', '/usr/local/bin/invidious_companion')
        self.process = subprocess.Popen([binary], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def player(self, client, identifier, cancel):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise Cancelled()
            if self.process.poll() is not None:
                raise RuntimeError('O processo do Invidious Companion encerrou durante a inicialização.')
            try:
                response = client.post('/companion/youtubei/v1/player',
                                       headers={'Authorization': f'Bearer {self.key}'},
                                       json={'videoId': identifier})
            except httpx.ConnectError:
                if cancel.wait(2):
                    raise Cancelled()
                continue
            if response.status_code != 200:
                raise RuntimeError(f'O player do Companion retornou HTTP {response.status_code}.')
            data = response.json()
            state = data.get('playabilityStatus') or {}
            if 'until a valid potoken is found' in state.get('reason', '').lower():
                if cancel.wait(2):
                    raise Cancelled()
                continue
            code = state.get('status', '')
            if code != 'OK':
                code = code if re.fullmatch(r'[A-Z_]{1,40}', code) else 'SEM_STATUS'
                raise RuntimeError(f'O player do YouTube retornou {code}.')
            return data
        raise RuntimeError('O Companion não ficou pronto para obter o áudio dentro do prazo.')

    def download(self, url, folder, cancel, progress):
        identifier = youtube_id(url)
        try:
            self.start()
            progress('Consultando o player pelo Invidious Companion', 3)
            with httpx.Client(base_url=ORIGIN, trust_env=False, timeout=15) as client:
                data = self.player(client, identifier, cancel)
                details = data['videoDetails']
                expected = float(details['lengthSeconds'])
                if details['videoId'] != identifier or details.get('isLive') or not 0 < expected <= 900:
                    raise RuntimeError('O teste aceita vídeos gravados de até 15 minutos.')
                formats = [item for item in data['streamingData']['adaptiveFormats']
                           if item.get('mimeType', '').startswith('audio/') and type(item.get('itag')) is int
                           and item.get('audioChannels') == 2 and not item.get('drmFamilies')]
                if not formats:
                    raise RuntimeError('O Companion não retornou uma faixa de áudio estéreo.')
                selected = max(formats, key=lambda item: (item['itag'] == 251, item.get('bitrate', 0)))
                LOG.info('Companion %s: player OK, %s formatos de áudio', identifier, len(formats))
                progress('Obtendo o áudio pelo Invidious Companion', 10)
                with TemporaryDirectory(dir=folder, prefix='.companion-') as temporary:
                    audio = Path(temporary) / 'audio.download'
                    redirect = client.get('/companion/latest_version',
                                          params={'id': identifier, 'itag': selected['itag'], 'local': 'true'})
                    target = urlsplit(redirect.headers.get('Location', ''))
                    if (redirect.status_code != 302 or target.scheme or target.netloc
                            or target.path != '/companion/videoplayback' or target.fragment):
                        raise RuntimeError(f'O Companion não entregou a rota de mídia (HTTP {redirect.status_code}).')
                    with client.stream('GET', target.path + '?' + target.query) as response:
                        mime = response.headers.get('Content-Type', '').split(';')[0]
                        if response.status_code != 200 or not (mime.startswith('audio/') or mime == 'application/octet-stream'):
                            content = 'HTML' if mime == 'text/html' else 'tipo de conteúdo incompatível'
                            raise RuntimeError(f'O servidor de mídia retornou HTTP {response.status_code} ({content}).')
                        size = 0
                        deadline = time.monotonic() + 45
                        with audio.open('wb') as output:
                            for chunk in response.iter_bytes(64 * 1024):
                                if cancel.is_set():
                                    raise Cancelled()
                                size += len(chunk)
                                if size > MAX_BYTES or time.monotonic() > deadline:
                                    raise RuntimeError('A transferência excedeu o limite de tamanho ou tempo do teste.')
                                output.write(chunk)
                    seconds = validate_audio(audio, expected, cancel, progress)
                    LOG.info('Companion %s: %s bytes, %.3f segundos validados pelo FFmpeg', identifier, size, seconds)
                    destination = folder / 'youtube-companion.audio'
                    audio.replace(destination)
                    return destination, details.get('title') or 'Música do YouTube'
        except httpx.HTTPError:
            raise RuntimeError('O Companion não concluiu a requisição de áudio ou metadados.') from None
        except OSError:
            raise RuntimeError('O Companion não conseguiu iniciar ou gravar o arquivo de áudio.') from None
        except (KeyError, TypeError, ValueError):
            raise RuntimeError('O Companion retornou metadados de vídeo inválidos.') from None
