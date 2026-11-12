#!/usr/bin/env bash
set -euo pipefail

directory=$(cd -- "$(dirname -- "$0")" && pwd)
for command in node npm ffmpeg; do
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
        progress=$(ffmpeg -nostdin -v error -xerror -protocol_whitelist file,pipe \
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
