"""Adapter from the experimental YouTube source to the FaixaLab audio API."""

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time

from .media import ffmpeg_executable, run_process, youtube_id
from .youtube_support import youtube_watch_cookie


NODE_ENV = {
    'PATH', 'SYSTEMROOT', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS',
}


def download_youtube(url, folder, cancel, progress):
    identifier = youtube_id(url)
    node = shutil.which('node')
    entry = Path(os.environ.get('FAIXALAB_YOUTUBE_NATIVE_SCRIPT', '/opt/youtube-source/extract.mjs'))
    if not node or not entry.is_file():
        raise RuntimeError('O extrator experimental do YouTube não está instalado.')
    cookie = youtube_watch_cookie()
    env = {name: value for name, value in os.environ.items() if name.upper() in NODE_ENV}
    if cookie:
        env['FAIXALAB_YOUTUBE_WATCH_COOKIE'] = cookie
    with TemporaryDirectory(dir=folder, prefix='.youtube-native-') as directory:
        audio = Path(directory) / 'audio.download'
        output = []
        progress('Lendo o player do YouTube', 3)
        run_process([node, '--use-env-proxy', str(entry), identifier, str(audio)],
                    cancel, output.append, env=env)
        try:
            result = json.loads(output[-1])
            expected = float(result['duration'])
            title = result['title']
        except (IndexError, KeyError, TypeError, ValueError):
            raise RuntimeError('O extrator retornou metadados inválidos.') from None

        validate_audio(audio, expected, cancel, progress)
        destination = folder / 'youtube-native.audio'
        audio.replace(destination)
        return destination, title


def validate_audio(audio, expected, cancel, progress):
    progress('Verificando o áudio completo', 90)
    seconds = 0
    deadline = time.monotonic() + 60

    def read_progress(line):
        nonlocal seconds
        field, _, value = line.strip().partition('=')
        if field == 'out_time_us' and value.isdigit():
            seconds = int(value) / 1_000_000

    def check_deadline(_):
        if time.monotonic() > deadline:
            raise RuntimeError('A validação do áudio excedeu o prazo.')

    try:
        run_process([ffmpeg_executable(), '-nostdin', '-v', 'error', '-xerror',
                     '-protocol_whitelist', 'file,pipe', '-i', str(audio), '-map', '0:a:0',
                     '-progress', 'pipe:1', '-f', 'null', '-'], cancel, read_progress, on_tick=check_deadline)
    except RuntimeError:
        raise RuntimeError('O arquivo entregue não passou pela decodificação completa do FFmpeg.') from None
    if seconds <= 0 or abs(seconds - expected) > max(2, expected * .01):
        raise RuntimeError(f'O áudio tem {seconds:.1f} s de {expected:.1f} s esperados.')
    return seconds
