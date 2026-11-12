const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../faixalab/web/app.js'), 'utf8');
const apiSource = source.slice(source.indexOf('async function api('), source.indexOf('function options()'));

function callApi(response) {
  const api = vm.runInNewContext(`${apiSource}\napi`, {window: {}, fetch: async () => response});
  return api('/api/import/youtube', {method: 'POST'});
}

test('retains the original text of a plain-text rejection', async () => {
  const response = new Response('Origem não autorizada.', {status: 403});
  await assert.rejects(callApi(response), {message: 'Origem não autorizada.'});
  assert.equal(response.bodyUsed, true);
});

test('retains JSON error details and validation messages', async () => {
  await assert.rejects(callApi(Response.json({detail: 'Link inválido.'}, {status: 422})), {message: 'Link inválido.'});
  await assert.rejects(callApi(Response.json({detail: [{msg: 'Campo obrigatório.'}]}, {status: 422})), {message: 'Campo obrigatório.'});
});

test('reports the HTTP status of an HTML or empty gateway error', async () => {
  await assert.rejects(callApi(new Response('<!doctype html><h1>Bad gateway</h1>', {status: 502})), {message: /indisponível \(HTTP 502\)/});
  await assert.rejects(callApi(new Response(null, {status: 401})), {message: /HTTP 401/});
});

test('handles accepted JSON imports and detects a redirected HTML response', async () => {
  assert.deepEqual(await callApi(Response.json({id: 'queued'}, {status: 202})), {id: 'queued'});
  const loginPage = new Response('<html>Entre no GitHub</html>');
  Object.defineProperty(loginPage, 'redirected', {value: true});
  await assert.rejects(callApi(loginPage), {message: /sessão está ativa/});
});
