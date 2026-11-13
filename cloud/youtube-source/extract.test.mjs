import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { extract } from './extract.mjs';

const id = 'Fixture0001';
const watch = `https://www.youtube.com/watch?v=${id}`;
const media = 'https://fixture.googlevideo.com/videoplayback';
const cookie = 'SESSION=fixture';

function playerPage(player) {
  return new Response(`<script>var ytInitialPlayerResponse = ${JSON.stringify(player)};</script>`);
}

test('uses the dedicated session only on the watch page after a traffic challenge', async (t) => {
  const temp = await mkdtemp(join(tmpdir(), 'faixalab-youtube-test-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const output = join(temp, 'audio');
  const calls = [];
  const audio = Buffer.from('audio payload');

  async function fetchFixture(url, options) {
    const address = String(url);
    const session = new Headers(options.headers).get('Cookie');
    calls.push([address, session]);
    if (address === media) return new Response(audio, { headers: { 'Content-Type': 'audio/webm' } });
    assert.equal(address, watch);
    assert.equal(options.redirect, 'manual');
    return session ? playerPage({
      playabilityStatus: { status: 'OK' },
      videoDetails: { videoId: id, title: 'Faixa de teste', lengthSeconds: '10' },
      streamingData: { adaptiveFormats: [{ mimeType: 'audio/webm', audioChannels: 2, url: media }] },
    }) : playerPage({
      playabilityStatus: { status: 'LOGIN_REQUIRED', reason: 'Sign in to confirm you’re not a bot' },
    });
  }

  assert.deepEqual(await extract(id, output, fetchFixture, cookie), {
    duration: 10, title: 'Faixa de teste', mode: 'direct',
  });
  assert.deepEqual(await readFile(output), audio);
  assert.deepEqual(calls, [[watch, null], [watch, cookie], [media, null]]);
});

test('keeps the session private for restricted videos and external redirects', async () => {
  const responses = [
    [() => playerPage({ playabilityStatus: { status: 'LOGIN_REQUIRED', reason: 'This video is private' } }),
      /LOGIN_REQUIRED/],
    [() => Response.redirect('https://www.google.com/sorry/index'), /verificação de tráfego/],
  ];
  for (const [response, error] of responses) {
    let requests = 0;
    await assert.rejects(extract(id, 'unused', async (url, options) => {
      assert.equal(String(url), watch);
      assert.equal(new Headers(options.headers).get('Cookie'), null);
      requests += 1;
      return response();
    }, cookie), error);
    assert.equal(requests, 1);
  }
});
