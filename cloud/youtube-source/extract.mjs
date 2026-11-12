import { createWriteStream } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { pathToFileURL } from 'node:url';
import { SabrStream } from 'googlevideo/sabr-stream';
import { buildSabrFormat, EnabledTrackTypes } from 'googlevideo/utils';
import { getQuickJS, shouldInterruptAfterDeadline } from 'quickjs-emscripten';

const MAX_BYTES = 32 * 1024 * 1024;
const MAX_SECONDS = 900;
const WATCH_HEADERS = { 'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US' };

class SourceError extends Error {}

function readPlayer(page) {
  const json = page.match(/\bytInitialPlayerResponse\s*=\s*({[^\n]+?});\s*<\/script>/)?.[1];
  if (!json) throw new SourceError('A página do YouTube não entregou a resposta do player.');
  try {
    return JSON.parse(json);
  } catch {
    throw new SourceError('A resposta do player veio em formato inválido.');
  }
}

function validateMediaUrl(url) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('.googlevideo.com')
      || parsed.username || parsed.password || parsed.port || !/^\/videoplayback(?:\/|$)/.test(parsed.pathname)) {
    throw new SourceError('O player indicou um endereço de mídia fora do domínio esperado.');
  }
  return parsed;
}

async function resolveMediaUrl(url, page, fetcher, overall) {
  const target = validateMediaUrl(url);
  const challenge = target.searchParams.get('n');
  if (!challenge) return target;

  const script = page.match(/\/s\/player\/[a-z0-9]+\/(?:[a-zA-Z0-9_-]+\.)?vflset\/[a-zA-Z0-9_-]+\/base\.js/)?.[0];
  if (!script) throw new SourceError('A página não informou a versão do JavaScript do player.');
  const scriptUrl = new URL(script, 'https://www.youtube.com');

  try {
    const response = await fetcher(scriptUrl, {
      headers: WATCH_HEADERS,
      redirect: 'error',
      signal: AbortSignal.any([overall, AbortSignal.timeout(15_000)]),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new SourceError(`O JavaScript do player retornou HTTP ${response.status}.`);
    }
    const [quickjs, library, core, player] = await Promise.all([
      getQuickJS(),
      readFile(new URL('./solver/lib.min.js', import.meta.url), 'utf8'),
      readFile(new URL('./solver/core.min.js', import.meta.url), 'utf8'),
      response.text(),
    ]);
    const input = { type: 'player', player, requests: [{ type: 'n', challenges: [challenge] }] };
    const result = quickjs.evalCode(`${library}\nObject.assign(globalThis, lib);\n${core}\njsc(${JSON.stringify(input)});`, {
      shouldInterrupt: shouldInterruptAfterDeadline(Date.now() + 15_000),
      memoryLimitBytes: 96 * 1024 * 1024,
      maxStackSizeBytes: 1024 * 1024,
    });
    const decoded = result?.responses?.[0]?.data?.[challenge];
    if (typeof decoded !== 'string' || !decoded || decoded === challenge || decoded.startsWith('enhanced_except_')) {
      throw new Error('Player challenge did not resolve');
    }
    // Avoid reserializing the other parameters of the signed URL.
    target.search = target.search.replace(/([?&])n=[^&]*/, (_, separator) =>
      `${separator}n=${encodeURIComponent(decoded)}`);
    return target;
  } catch (error) {
    if (error instanceof SourceError) throw error;
    throw new SourceError('O extrator não conseguiu resolver o parâmetro n do player.');
  }
}

async function mediaResponse(fetcher, url, options, overall, sabr = false) {
  const response = await fetcher(validateMediaUrl(url), {
    ...options,
    redirect: 'error',
    signal: AbortSignal.any([overall, AbortSignal.timeout(15_000), ...[options?.signal].filter(Boolean)]),
  });
  const mime = response.headers.get('content-type')?.split(';')[0];
  const readable = sabr ? mime === 'application/vnd.yt-ump'
    : mime?.startsWith('audio/') || mime === 'application/octet-stream';
  if (!response.ok || !readable || (!sabr && response.status !== 200) || !response.body) {
    await response.body?.cancel();
    const type = mime === 'text/html' ? 'HTML' : 'resposta sem mídia compatível';
    throw new SourceError(`O servidor de mídia retornou HTTP ${response.status} (${type}).`);
  }
  return response;
}

async function writeAudio(stream, path, signal) {
  let bytes = 0;
  const limit = new Transform({
    transform(chunk, encoding, done) {
      bytes += chunk.length;
      if (bytes > MAX_BYTES) {
        done(new SourceError('O áudio excedeu o limite de 32 MiB deste teste.'));
      } else {
        done(null, chunk);
      }
    },
  });
  await pipeline(Readable.fromWeb(stream), limit, createWriteStream(path, { flags: 'wx' }), { signal });
  if (bytes === 0) throw new SourceError('O servidor de mídia entregou um arquivo vazio.');
}

async function readSabr(page, player, videoId, formats, path, fetcher, overall) {
  const clientJson = page.match(/ytcfg\.set\((\{[^\n]+?\})\);/)?.[1];
  const client = JSON.parse(clientJson || '{}');
  const streamingUrl = player.streamingData.serverAbrStreamingUrl;
  const config = player.playerConfig?.mediaCommonConfig?.mediaUstreamerRequestConfig?.videoPlaybackUstreamerConfig;
  if (!streamingUrl || !config || !client.INNERTUBE_CONTEXT_CLIENT_NAME || !client.INNERTUBE_CLIENT_VERSION) {
    throw new SourceError('O player não forneceu os dados necessários para ler o stream SABR.');
  }
  let mediaError;
  const stream = new SabrStream({
    videoId,
    serverAbrStreamingUrl: (await resolveMediaUrl(streamingUrl, page, fetcher, overall)).href,
    videoPlaybackUstreamerConfig: config,
    formats: formats.map(buildSabrFormat),
    clientInfo: {
      clientName: client.INNERTUBE_CONTEXT_CLIENT_NAME,
      clientVersion: client.INNERTUBE_CLIENT_VERSION,
    },
    fetchFunction: async (url, options) => {
      try {
        const response = await mediaResponse(fetcher, url, options, overall, true);
        mediaError = undefined;
        return response;
      } catch (error) {
        mediaError = error;
        throw error;
      }
    },
  });
  try {
    const { audioStream } = stream.start({
      audioPreferences: { dynamicRangeCompression: false, voiceBoost: false },
      enabledTrackTypes: EnabledTrackTypes.AUDIO_ONLY,
      isPostLiveDvr: false,
      maxRetries: 0,
      stallDetectionMs: 12_000,
    });
    await writeAudio(audioStream, path, overall);
  } catch (error) {
    throw mediaError || error;
  } finally {
    await stream.abort({ snapshot: false });
  }
}

export async function extract(videoId, path, fetcher = fetch) {
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) throw new SourceError('ID de vídeo inválido.');
  const overall = AbortSignal.timeout(90_000);
  const pageResponse = await fetcher(`https://www.youtube.com/watch?v=${videoId}`, {
    headers: WATCH_HEADERS,
    redirect: 'manual',
    signal: AbortSignal.any([overall, AbortSignal.timeout(15_000)]),
  });
  if (pageResponse.status >= 300 && pageResponse.status < 400) {
    const location = pageResponse.headers.get('location');
    const target = location && new URL(location, 'https://www.youtube.com');
    await pageResponse.body?.cancel();
    if (target?.hostname === 'www.google.com' && target.pathname.startsWith('/sorry/')) {
      throw new SourceError(`O Google redirecionou a página do vídeo para a verificação de tráfego (HTTP ${pageResponse.status}).`);
    }
    throw new SourceError(`A página do vídeo redirecionou a requisição (HTTP ${pageResponse.status}).`);
  }
  if (!pageResponse.ok) throw new SourceError(`A página do vídeo retornou HTTP ${pageResponse.status}.`);
  const page = await pageResponse.text();
  const player = readPlayer(page);
  const details = player.videoDetails;
  const status = player.playabilityStatus?.status;
  if (status !== 'OK') {
    const code = /^[A-Z_]{1,40}$/.test(status || '') ? status : 'SEM_STATUS';
    throw new SourceError(`O player do YouTube não liberou a reprodução (${code}).`);
  }
  if (details?.videoId !== videoId) {
    throw new SourceError('A resposta do player pertence a outro vídeo.');
  }
  const duration = Number(details.lengthSeconds);
  if (details.isLive || !Number.isFinite(duration) || duration <= 0 || duration > MAX_SECONDS) {
    throw new SourceError('O teste aceita vídeos gravados de até 15 minutos.');
  }
  const formats = (player.streamingData?.adaptiveFormats || []).filter(
    format => /^audio\/(webm|mp4)\b/.test(format.mimeType || '')
      && format.audioChannels === 2 && !format.drmFamilies?.length,
  );
  const direct = formats.filter(format => format.url).sort((a, b) => b.bitrate - a.bitrate)[0];
  if (!formats.length) throw new SourceError('O player não forneceu faixas de áudio compatíveis.');
  if (direct) {
    const url = await resolveMediaUrl(direct.url, page, fetcher, overall);
    const response = await mediaResponse(fetcher, url, undefined, overall);
    await writeAudio(response.body, path, overall);
  } else {
    await readSabr(page, player, videoId, formats, path, fetcher, overall);
  }
  return { duration, title: details.title || 'Música do YouTube', mode: direct ? 'direct' : 'sabr' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [, , videoId, path] = process.argv;
    if (!path) throw new SourceError('Informe o destino do áudio.');
    console.log(JSON.stringify(await extract(videoId, path)));
  } catch (error) {
    console.error(error instanceof SourceError ? error.message : 'Falha de rede ou de leitura do stream do YouTube.');
    process.exitCode = 1;
  }
}
