"""A/B diagnostic for the Render test service."""

from contextlib import contextmanager
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .youtube_support import check_support


VIDEOS = ('7iNbnineUCI', 'aqz-KE-bpKQ')
CLIENT = 'mweb'
PROVIDER = 'http://127.0.0.1:4416'
BGUTIL_DIRECTORY = Path('/opt/bgutil')
REPEAT = 2


class AttemptLog:
    def __init__(self):
        self.token_received = False

    def debug(self, message):
        if re.search(r'Retrieved a (?:gvs|player) PO Token for mweb client\s*$', message, re.I):
            self.token_received = True

    def warning(self, message):
        pass

    error = warning


class ObservedDownloader(YoutubeDL):
    def __init__(self, options):
        self.token_in_media_request = False
        super().__init__(options)

    def urlopen(self, request):
        address = request.full_url if isinstance(request, Request) else request
        url = urlsplit(address if isinstance(address, str) else address.url)
        if (url.hostname or '').endswith('.googlevideo.com'):
            if 'pot' in parse_qs(url.query) or '/pot/' in url.path:
                self.token_in_media_request = True
        return super().urlopen(request)


def failure_code(error):
    detail = str(error).lower()
    for marker, code in (
        ('certificate_verify_failed', 'tls'),
        ('sign in to confirm', 'youtube_verification'),
        ('failed to extract any player response', 'player_response'),
        ('no video formats found', 'formats_unavailable'),
        ('http error 403', 'http_403'),
        ('http error 429', 'http_429'),
        ('invalid data found', 'invalid_audio'),
    ):
        if marker in detail:
            return code
    return 'download_failed'


def decoded_duration(audio):
    process = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
         '-i', str(audio), '-map', '0:a:0', '-progress', 'pipe:1', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=30,
    )
    positions = re.findall(r'^out_time_us=(\d+)$', process.stdout, re.M)
    if process.returncode or not positions:
        return 0
    return int(positions[-1]) / 1_000_000


def run_attempt(video, mode, folder):
    runtime = check_support()['runtime']
    log = AttemptLog()
    row = {'video': video, 'mode': mode, 'ok': False}
    options = {
        'format': 'bestaudio/best', 'outtmpl': str(folder / '%(id)s.%(ext)s'),
        'noplaylist': True, 'quiet': True, 'noprogress': True, 'verbose': True, 'logger': log,
        'js_runtimes': {runtime['name']: {'path': runtime['path']}},
        'compat_opts': {'no-certifi'}, 'cachedir': False, 'retries': 0, 'fragment_retries': 0,
        'socket_timeout': 15, 'max_filesize': 32 * 1024 * 1024,
        'extractor_args': {
            'youtube': {'player_client': [CLIENT], 'fetch_pot': ['auto' if mode == 'with' else 'never']},
            'youtubepot-bgutilhttp': {'base_url': [PROVIDER]},
        },
    }
    with ObservedDownloader(options) as downloader:
        try:
            info = downloader.extract_info(f'https://www.youtube.com/watch?v={video}', download=True)
            audio = Path(info['requested_downloads'][0]['filepath'])
            if audio.resolve().parent != folder or not audio.is_file():
                row['error'] = 'audio_missing'
            else:
                seconds = decoded_duration(audio)
                expected = info.get('duration') or 0
                row['bytes'] = audio.stat().st_size
                row['decoded_seconds'] = round(seconds, 3)
                row['ok'] = seconds > 0 and (not expected or seconds >= expected - max(2, expected * .05))
                if not row['ok']:
                    row['error'] = 'invalid_or_incomplete_audio'
        except DownloadError as exc:
            row['error'] = failure_code(exc)
        except (KeyError, IndexError, OSError, subprocess.TimeoutExpired):
            row['error'] = 'audio_missing_or_validation_failed'
    row['token_received'] = log.token_received
    row['token_in_media_request'] = downloader.token_in_media_request
    return row


def provider_version(process):
    http = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 15
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with http.open(PROVIDER + '/ping', timeout=1) as response:
                return json.load(response)['version']
        except (URLError, OSError, ValueError, KeyError):
            time.sleep(.3)
    raise RuntimeError('O provedor local não respondeu ao preflight.')


@contextmanager
def running_provider(output=subprocess.DEVNULL):
    with subprocess.Popen(
        ['/opt/bgutil-node', 'build/main.js', '--host', '127.0.0.1'],
        cwd=BGUTIL_DIRECTORY, stdout=output, stderr=output,
    ) as provider:
        try:
            yield provider_version(provider)
        finally:
            if provider.poll() is None:
                provider.terminate()
                try:
                    provider.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    provider.kill()


def run_probe(stop, report):
    runtime = check_support()['runtime']
    ffmpeg = subprocess.check_output(['ffmpeg', '-version'], text=True, timeout=5).splitlines()[0]
    with running_provider() as provider:
        report(environment={
            'client': CLIENT, 'runtime': runtime['name'] + ' ' + runtime['version'],
            'yt_dlp': version('yt-dlp'), 'ejs': version('yt-dlp-ejs'),
            'plugin': version('bgutil-ytdlp-pot-provider'), 'provider': provider,
            'ffmpeg': ffmpeg,
        })
        rows = []
        for repeat in range(REPEAT):
            for index, video in enumerate(VIDEOS):
                modes = ('without', 'with') if (repeat + index) % 2 == 0 else ('with', 'without')
                for mode in modes:
                    if stop.is_set():
                        return
                    with TemporaryDirectory(prefix='faixalab-pot-ab-') as folder:
                        try:
                            result = subprocess.check_output(
                                [sys.executable, '-m', __name__, video, mode, folder],
                                text=True, timeout=65, stderr=subprocess.DEVNULL,
                            )
                            row = json.loads(result)
                        except (OSError, ValueError, subprocess.SubprocessError):
                            row = {'video': video, 'mode': mode, 'ok': False, 'error': 'attempt_failed_or_timed_out'}
                    rows.append(row)
                    report(rows=list(rows))
                    if stop.wait(5):
                        return


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        check_support()
        with running_provider(output=None) as provider:
            print(f'bgutil {provider}: /ping OK')
    else:
        _, identifier, condition, destination = sys.argv
        if identifier not in VIDEOS or condition not in {'with', 'without'}:
            raise SystemExit(2)
        print(json.dumps(run_attempt(identifier, condition, Path(destination).resolve())))
