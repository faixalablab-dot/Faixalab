import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.dirname(here);
const [destination, server, signIn] = process.argv.slice(2);
if (!destination || !server) {
  throw new Error('Uso: node online/build-pages.mjs DIRETORIO NOME_DO_CODESPACE|ORIGEM_HTTPS_DA_API [--google-only]');
}
const codespace = /^[a-z0-9-]+$/.test(server) ? server : null;
const ownerLogin = codespace && signIn !== '--google-only';
const backendUrl = codespace ? null : new URL(server);
if (backendUrl && (backendUrl.protocol !== 'https:' || backendUrl.pathname !== '/' ||
                    backendUrl.username || backendUrl.password || backendUrl.search || backendUrl.hash)) {
  throw new Error('Configure a origem HTTPS da API, sem caminho, credenciais, parâmetros ou fragmento.');
}
const processingHost = codespace ? 'Codespace' : 'servidor do FaixaLab';
const web = path.join(repo, 'app/faixalab/web');
const output = path.resolve(destination);
mkdirSync(output, { recursive: true });
const version = content => createHash('sha256').update(content).digest('hex').slice(0, 12);
for (const asset of ['style.css', 'studio.css', 'mobile.css', 'app.js', 'guitar.js', 'icon.svg']) {
  copyFileSync(path.join(web, asset), path.join(output, asset));
}
const connectionScript = readFileSync(path.join(here, 'pages-connection.js'));
const connectionVersion = version(connectionScript);
writeFileSync(path.join(output, 'pages-connection.js'), connectionScript);
const config = codespace
  ? { backend: `https://${codespace}-7861.app.github.dev` }
  : { backend: backendUrl.origin };
if (ownerLogin) config.connect = `https://${codespace}-7860.app.github.dev/connect-pages`;
const configScript = 'window.faixaLabPagesConfig = ' + JSON.stringify(config) + ';\n';
writeFileSync(path.join(output, 'pages-config.js'), configScript);

const html = readFileSync(path.join(web, 'index.html'), 'utf8')
  .replace(/\b(href|src)="\//g, '$1="./')
  .replace('href="./style.css"', 'href="./style.css?v=' + version(readFileSync(path.join(web, 'style.css'))) + '"')
  .replace('<script src="./app.js" defer></script>',
    '<meta name="referrer" content="no-referrer" />\n    <script src="./pages-config.js?v=' + version(configScript) + '" defer></script>\n    <script src="./pages-connection.js?v=' + connectionVersion + '" defer></script>\n    <script src="./app.js?v=' + version(readFileSync(path.join(web, 'app.js'))) + '" defer></script>')
  .replace('Processamento local', `Processamento no ${processingHost}`)
  .replace('Suas músicas ficam salvas neste computador.', `Suas músicas ficam salvas no ${processingHost}.`)
  .replace('no seu computador. O link', `no ${processingHost}. O link`)
  .replace('<main>', `<main>
      <aside id="pages-connection" role="status" hidden>
        <span></span>
        <div id="pages-google" hidden></div>
        ${ownerLogin ? `<button class="button primary" id="pages-owner-connect" type="button" data-resume-url="https://${codespace}.github.dev">Conectar ao FaixaLab</button>` : ''}
      </aside>`)
  .replace('<div class="top-actions">', '<div class="top-actions"><button id="pages-signout" class="button secondary" hidden>Sair</button>')
  .replace('</head>', '<style>#pages-connection:not([hidden]){display:flex;gap:12px;align-items:center;justify-content:space-between;padding:12px 16px;background:#1d272a;color:#dde7ea;border-bottom:1px solid #3b4950;flex-wrap:wrap}#pages-connection [hidden],#pages-signout[hidden]{display:none}</style>\n  </head>');
writeFileSync(path.join(output, 'index.html'), html);
const manifest = JSON.parse(readFileSync(path.join(web, 'manifest.webmanifest'), 'utf8'));
manifest.start_url = './';
manifest.scope = './';
manifest.icons.forEach(icon => { icon.src = './' + icon.src.replace(/^\//, ''); });
writeFileSync(path.join(output, 'manifest.webmanifest'), JSON.stringify(manifest) + '\n');
const catalog = JSON.parse(execFileSync('python3', ['-c',
  'import json; from faixalab.chord_dictionary import load_dictionary; from faixalab.instrument_catalog import public_catalog; print(json.dumps([load_dictionary(),public_catalog()]))'],
  { encoding: 'utf8', cwd: repo, env: { ...process.env, PYTHONPATH: path.join(repo, 'app') } }));
writeFileSync(path.join(output, 'chord-dictionary.json'), JSON.stringify(catalog[0]) + '\n');
writeFileSync(path.join(output, 'instruments.json'), JSON.stringify(catalog[1]) + '\n');
writeFileSync(path.join(output, '.nojekyll'), '');
