#!/usr/bin/env bash
set -euo pipefail

directory=$(cd -- "$(dirname -- "$0")" && pwd)
for command in node npm; do
    if ! command -v "$command" >/dev/null; then
        printf 'Comando necessário: %s\n' "$command" >&2
        exit 2
    fi
done
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
    printf 'Use Node.js 22 ou mais recente.\n' >&2
    exit 2
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

ffmpeg_bin=${FFMPEG_BINARY:-$(command -v ffmpeg || true)}
if [[ -z "$ffmpeg_bin" ]]; then
    if ! command -v python3 >/dev/null; then
        printf 'Instale o FFmpeg ou use Python 3 para obter a versão portátil.\n' >&2
        exit 2
    fi
    printf 'Preparando o FFmpeg portátil para este teste…\n' >&2
    if ! python3 -m pip install --disable-pip-version-check --quiet --only-binary=:all: \
        --target "$work/ffmpeg" 'imageio-ffmpeg==0.8.1'; then
        printf 'Falha ao obter o FFmpeg portátil. Verifique se python3 -m pip está disponível.\n' >&2
        exit 2
    fi
    ffmpeg_bin=$(PYTHONPATH="$work/ffmpeg" python3 -c 'from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())')
fi
if ! "$ffmpeg_bin" -version >/dev/null; then
    printf 'Não foi possível iniciar o FFmpeg.\n' >&2
    exit 2
fi

cp "$directory"/{package.json,package-lock.json,extract.mjs} "$work/"
npm --prefix "$work" ci --omit=dev --ignore-scripts --no-audit --no-fund >/dev/null

videos=("$@")
if (( ${#videos[@]} == 0 )); then
    videos=(7iNbnineUCI aqz-KE-bpKQ)
fi
passed=0
for video in "${videos[@]}"; do
    audio="$work/audio.download"
    rm -f "$audio"
    if metadata=$(node "$work/extract.mjs" "$video" "$audio") &&
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
