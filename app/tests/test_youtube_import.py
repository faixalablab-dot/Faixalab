"""Regression coverage for missing/old JS runtimes and ambiguous YouTube errors."""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from faixalab import youtube_support as support
from faixalab.media import Cancelled, download_youtube, youtube_id
import prepare_youtube


VIDEO = 'https://www.youtube.com/watch?v=u_ix058WpVo'
VERSIONS = {'yt-dlp': '2026.10.26', 'yt-dlp-ejs': '0.8.0'}
SUPPORTED = {**VERSIONS, 'runtime': {'name': 'deno', 'path': '/private/deno', 'version': '2.6.0'}}
TEST_COOKIES = '# Netscape HTTP Cookie File\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tTEST\tfixture-value\n'


class WatchCookieChecks(unittest.TestCase):
    def test_watch_cookie_respects_domain_path_and_expiration(self):
        secret = TEST_COOKIES + (
            '.youtube.com\tTRUE\t/\tTRUE\t1\tEXPIRED\tno\n'
            '.youtube.com\tTRUE\t/account\tTRUE\t0\tWRONG_PATH\tno\n'
            'youtube.com\tFALSE\t/\tTRUE\t0\tHOST_ONLY\tno\n'
            'music.youtube.com\tFALSE\t/\tTRUE\t0\tSIBLING\tno\n'
            'www.youtube.com\tFALSE\t/watch\tTRUE\t0\tWATCH\tyes\n'
        )
        self.assertEqual(support.youtube_watch_cookie(secret), 'WATCH=yes; TEST=fixture-value')

    def test_invalid_cookie_is_not_exposed_in_the_error(self):
        with self.assertRaisesRegex(support.YouTubeSupportError, 'cookie inválido') as error:
            support.youtube_watch_cookie(TEST_COOKIES.replace('fixture-value', 'secret;invalid'))
        self.assertNotIn('secret', str(error.exception))


class RuntimeChecks(unittest.TestCase):
    def test_private_runtime_precedes_global_deno_even_outside_app_directory(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            private = root / '.runtime' / ('deno.exe' if support.os.name == 'nt' else 'deno')
            private.parent.mkdir()
            private.touch()
            global_deno = root / 'old-deno'
            global_deno.touch()
            stack.enter_context(patch.dict(support.os.environ, {'FAIXALAB_DENO': ''}))
            stack.enter_context(patch.object(support.shutil, 'which', return_value=str(global_deno)))
            run = stack.enter_context(patch.object(support.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, 'deno 2.6.0 (stable)\n', '')))
            runtime = support.find_runtime(root)
            self.assertEqual(runtime['path'], str(private.resolve()))
            self.assertEqual(run.call_args.args[0], [str(private), '--version'])

    def test_incompatible_deno_does_not_prevent_node_fallback(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            deno, node = root / 'deno', root / 'node'
            deno.touch(); node.touch()
            stack.enter_context(patch.dict(support.os.environ, {'FAIXALAB_DENO': '', 'FAIXALAB_NODE': ''}))
            stack.enter_context(patch.object(support.shutil, 'which', side_effect=
                lambda name: str(deno if name == 'deno' else node)))
            def run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, 'deno 2.2.0' if args[0] == str(deno) else 'v24.0.0', '')
            stack.enter_context(patch.object(support.subprocess, 'run', side_effect=run))
            self.assertEqual(support.find_runtime(root)['name'], 'node')

    def test_invalid_configured_runtime_is_skipped(self):
        with patch.object(support.subprocess, 'run', side_effect=subprocess.TimeoutExpired('deno', 5)), \
             patch.object(Path, 'is_file', return_value=True):
            self.assertIsNone(support.runtime_info('deno', '/broken/deno'))

    def test_dependencies_and_runtime_are_verified_before_import(self):
        with patch.object(support, 'distribution_versions', return_value=VERSIONS), \
             patch.object(support.importlib.metadata, 'requires', return_value=["yt-dlp-ejs==0.8.0; extra == 'default'"]), \
             patch.object(support, 'find_runtime', return_value=None):
            with self.assertRaisesRegex(support.YouTubeSupportError, 'runtime JavaScript compatível'):
                support.check_support()
        with patch.object(support, 'distribution_versions', return_value={**VERSIONS, 'yt-dlp-ejs': '0.7.0'}), \
             patch.object(support.importlib.metadata, 'requires', return_value=["yt-dlp-ejs==0.8.0; extra == 'default'"]):
            with self.assertRaisesRegex(support.YouTubeSupportError, 'incompatíveis'):
                support.check_support()
        with patch.object(support, 'distribution_versions', return_value={**VERSIONS, 'yt-dlp': '2026.3.3'}):
            with self.assertRaisesRegex(support.YouTubeSupportError, 'desatualizado'):
                support.check_support()

    def test_missing_ejs_is_reported(self):
        with patch.object(support, 'distribution_versions', return_value={**VERSIONS, 'yt-dlp-ejs': None}):
            with self.assertRaisesRegex(support.YouTubeSupportError, 'EJS'):
                support.check_support()

    def test_explicit_access_restriction_is_not_reported_as_runtime_failure(self):
        message = support.download_error('Video unavailable. Private video. Sign in if you have access')
        self.assertIn('restringiu o acesso', message)
        self.assertNotIn('ATUALIZAR_YOUTUBE', message)


class ImportChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.cancel = threading.Event()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(support, 'check_support', return_value=SUPPORTED))
        self.stack.enter_context(patch('faixalab.media.ffmpeg_executable', return_value='ffmpeg'))
        self.stack.enter_context(patch.dict(os.environ, {support.COOKIE_SECRET: ''}))

    def downloader(self, payload=b'RIFF\x00\x00\x00\x00WAVE', error=None, hook=None):
        folder = self.folder
        instance = self
        class FakeDownload:
            def __init__(self, options):
                instance.options = options
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def extract_info(self, url, download):
                instance.requested_url = url
                if hook:
                    hook(instance.options)
                if error:
                    instance.options['logger'].warning('JavaScript challenge unavailable')
                    raise error
                (folder / 'youtube.wav').write_bytes(payload)
                return {'title': 'Teste'}
            def prepare_filename(self, info):
                return str(folder / 'youtube.wav')
        return patch('yt_dlp.YoutubeDL', FakeDownload)

    def test_success_uses_explicit_runtime_and_canonical_single_video_url(self):
        with self.downloader():
            path, title = download_youtube(VIDEO + '&list=ignored', self.folder, self.cancel, lambda *_: None)
        self.assertTrue(path.is_file())
        self.assertEqual(title, 'Teste')
        self.assertEqual(self.requested_url, VIDEO)
        self.assertEqual(self.options['js_runtimes'], {'deno': {'path': '/private/deno'}})
        self.assertNotIn('cookiefile', self.options)
        self.assertTrue(self.options['nocolor'])
        diagnostic = json.loads((self.folder / 'youtube-diagnostico.json').read_text('utf-8'))
        self.assertIsNone(diagnostic['error'])
        self.assertEqual(diagnostic['yt-dlp'], '2026.10.26')

    def test_codespace_cookie_is_private_and_removed_even_if_extraction_fails(self):
        def inspect_cookie(options):
            file = Path(options['cookiefile'])
            self.assertNotIn(self.folder, file.parents)
            self.assertEqual(file.read_bytes(), TEST_COOKIES.encode())
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            options['logger'].warning('unexpected upstream fixture-value')

        secret = '\ufeff' + TEST_COOKIES.replace('\n', '\r\n')
        with patch.dict(os.environ, {support.COOKIE_SECRET: secret}):
            with self.downloader(hook=inspect_cookie):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
            self.assertFalse(Path(self.options['cookiefile']).exists())
            with self.downloader(hook=inspect_cookie, error=RuntimeError('Sign in to confirm you are not a bot: fixture-value')):
                with self.assertRaisesRegex(RuntimeError, 'verificação para a sessão') as raised:
                    download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
            self.assertFalse(Path(self.options['cookiefile']).exists())
            self.assertNotIn('fixture-value', str(raised.exception))
            self.assertNotIn('fixture-value', (self.folder / 'youtube-diagnostico.json').read_text('utf-8'))

    def test_invalid_cookie_secret_never_opens_the_extractor(self):
        invalid = TEST_COOKIES.replace('.youtube.com', '.example.invalid')
        with patch.dict(os.environ, {support.COOKIE_SECRET: invalid}), patch('yt_dlp.YoutubeDL') as downloader:
            with self.assertRaisesRegex(support.YouTubeSupportError, 'somente cookies do domínio youtube.com'):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
        downloader.assert_not_called()

    def test_missing_runtime_stops_before_any_network_request(self):
        with patch.object(support, 'check_support', side_effect=support.YouTubeSupportError('Falta runtime')), \
             patch('yt_dlp.YoutubeDL') as downloader:
            with self.assertRaisesRegex(support.YouTubeSupportError, 'Falta runtime'):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
        downloader.assert_not_called()
        self.assertTrue((self.folder / 'youtube-diagnostico.json').is_file())

    def test_ambiguous_error_keeps_warning_and_removes_console_escapes(self):
        with self.downloader(error=RuntimeError('\x1b[0;31mERROR:\x1b[0m This video is not available')):
            with self.assertRaisesRegex(RuntimeError, 'não confirma que o vídeo foi removido') as raised:
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
        self.assertNotIn('\x1b', str(raised.exception))
        diagnostic = json.loads((self.folder / 'youtube-diagnostico.json').read_text('utf-8'))
        self.assertIn('JavaScript', diagnostic['messages'][0])
        self.assertNotIn('\x1b', diagnostic['error'])

    def test_cancellation_before_extraction_never_opens_downloader(self):
        self.cancel.set()
        with patch('yt_dlp.YoutubeDL') as downloader:
            with self.assertRaises(Cancelled):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
        downloader.assert_not_called()

    def test_cancellation_in_progress_remains_cancellation(self):
        def hook(options):
            self.cancel.set()
            options['progress_hooks'][0]({'status': 'downloading'})
        with self.downloader(hook=hook):
            with self.assertRaises(Cancelled):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)

    def test_html_is_rejected_and_deleted_instead_of_entering_audio_pipeline(self):
        with self.downloader(payload=b'<!doctype html><title>Blocked</title>'):
            with self.assertRaisesRegex(RuntimeError, 'página de bloqueio'):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)
        self.assertFalse((self.folder / 'youtube.wav').exists())
        self.assertIn('bloqueio', json.loads((self.folder / 'youtube-diagnostico.json').read_text('utf-8'))['error'])

    def test_empty_download_is_rejected(self):
        with self.downloader(payload=b''):
            with self.assertRaisesRegex(RuntimeError, 'áudio utilizável'):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)

    def test_diagnostic_write_failure_does_not_mask_download_failure(self):
        with self.downloader(error=RuntimeError('network failed')), \
             patch.object(Path, 'write_text', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'network failed'):
                download_youtube(VIDEO, self.folder, self.cancel, lambda *_: None)

    def test_url_validation_preserves_host_boundaries(self):
        self.assertEqual(youtube_id(VIDEO), 'u_ix058WpVo')
        with self.assertRaises(ValueError):
            youtube_id('https://youtube.com.evil.test/watch?v=u_ix058WpVo')


class SetupChecks(unittest.TestCase):
    def test_corrupt_runtime_download_does_not_replace_existing_binary(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('deno', b'not executed')
        data = stream.getvalue()
        asset = {'name': 'deno-x86_64-unknown-linux-gnu.zip', 'size': len(data),
                 'digest': 'sha256:' + '0' * 64, 'browser_download_url': 'https://example.invalid/deno.zip'}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            binary = root / '.runtime' / 'deno'
            binary.parent.mkdir(); binary.write_bytes(b'previous runtime')
            stack.enter_context(patch.object(prepare_youtube.platform, 'machine', return_value='x86_64'))
            stack.enter_context(patch.object(prepare_youtube.platform, 'system', return_value='Linux'))
            stack.enter_context(patch.object(prepare_youtube, 'read_url', side_effect=
                [io.BytesIO(json.dumps({'assets': [asset]}).encode()), io.BytesIO(data)]))
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                prepare_youtube.install_deno(root)
            self.assertEqual(binary.read_bytes(), b'previous runtime')
            self.assertEqual(list(binary.parent.iterdir()), [binary])


if __name__ == '__main__':
    unittest.main(verbosity=2)
