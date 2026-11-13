#!/usr/bin/env bash
set -euo pipefail

directory=$(cd -- "$(dirname -- "$0")" && pwd)
for command in node npm python3; do
    if ! command -v "$command" >/dev/null; then
        printf 'Comando necessário: %s\n' "$command" >&2
        exit 2
    fi
done
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
    printf 'Use Node.js 22 ou mais recente.\n' >&2
    exit 2
fi

session_cookie=
if [[ -n "${FAIXALAB_YOUTUBE_COOKIES:-}${FAIXALAB_YOUTUBE_COOKIE_FILE:-}" ]]; then
    session_cookie=$(PYTHONPATH="$directory/../../app" python3 - <<'PY'
from pathlib import Path
import os
from faixalab.youtube_support import YouTubeSupportError, youtube_watch_cookie

try:
    filename = os.environ.get('FAIXALAB_YOUTUBE_COOKIE_FILE')
    cookie = youtube_watch_cookie(Path(filename).read_text('utf-8') if filename else None)
    if not cookie:
        raise YouTubeSupportError('O arquivo de sessão do YouTube está vazio.')
    print(cookie)
except (OSError, UnicodeError):
    raise SystemExit('Não foi possível ler o arquivo de sessão do YouTube.') from None
except YouTubeSupportError as error:
    raise SystemExit(str(error)) from None
PY
    )
    printf 'Sessão da conta dedicada disponível para a verificação do player.\n'
fi
unset FAIXALAB_YOUTUBE_COOKIES

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

ffmpeg_bin=${FFMPEG_BINARY:-$(command -v ffmpeg || true)}
packages=('yt-dlp-ejs==0.8.0')
if [[ -z "$ffmpeg_bin" ]]; then
    packages+=('imageio-ffmpeg==0.8.1')
    printf 'Preparando o FFmpeg portátil para este teste…\n' >&2
fi
if ! python3 -m pip install --disable-pip-version-check --quiet --only-binary=:all: \
    --target "$work/python" "${packages[@]}"; then
    printf 'Falha ao preparar as dependências temporárias do teste.\n' >&2
    exit 2
fi
if [[ -z "$ffmpeg_bin" ]]; then
    ffmpeg_bin=$(PYTHONPATH="$work/python" python3 -c 'from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())')
fi
if ! "$ffmpeg_bin" -version >/dev/null; then
    printf 'Não foi possível iniciar o FFmpeg.\n' >&2
    exit 2
fi

cp "$directory"/{package.json,package-lock.json,extract.mjs} "$work/"
mkdir "$work/solver"
cp "$work/python/yt_dlp_ejs/yt/solver"/{core,lib}.min.js "$work/solver/"
npm --prefix "$work" ci --omit=dev --ignore-scripts --no-audit --no-fund >/dev/null

videos=("$@")
if (( ${#videos[@]} == 0 )); then
    videos=(7iNbnineUCI aqz-KE-bpKQ)
fi
passed=0
for video in "${videos[@]}"; do
    audio="$work/audio.download"
    rm -f "$audio"
    if metadata=$(FAIXALAB_YOUTUBE_WATCH_COOKIE="$session_cookie" node "$work/extract.mjs" "$video" "$audio") &&
        progress=$("$ffmpeg_bin" -nostdin -v error -xerror -protocol_whitelist file,pipe \
            -i "$audio" -map 0:a:0 -progress pipe:1 -f null -) &&
        result=$(node -e '
            const meta = JSON.parse(process.argv[1]);
            const times = [...process.argv[2].matchAll(/^out_time_us=(\d+)$/gm)];
            const seconds = Number(times.at(-1)?.[1]) / 1e6;
            if (!(seconds > 0) || Math.abs(seconds - meta.duration) > Math.max(2, meta.duration * .01)) {
                console.error(`Duração decodificada: ${seconds}s de ${meta.duration}s esperados.`);
                process.exit(1);
            }
            console.log(`${seconds.toFixed(1)} s, modo ${meta.mode}`);
        ' "$metadata" "$progress"); then
        printf '%s: áudio completo (%s)\n' "$video" "$result"
        passed=$((passed + 1))
    else
        printf '%s: falhou\n' "$video"
    fi
done
printf 'Áudio completo: %s/%s\n' "$passed" "${#videos[@]}"
(( passed == ${#videos[@]} ))
