"""Authenticated HTTP relay for the GitHub Pages interface."""
from contextlib import asynccontextmanager
from functools import partial
import hashlib
import hmac
import os
import re
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field


SESSION_SECONDS = 3 * 60 * 60
LOGIN_NONCE_SECONDS = 10 * 60
SESSION_HEADER = 'x-faixalab-pages-session'
ACCOUNT_ID = re.compile(r'[a-f0-9]{64}')
MEDIA_ROUTE = re.compile(r'/api/projects/[a-f0-9]{32}/(?:audio|backing|export)/[a-zA-Z0-9_-]+')
REQUEST_HEADERS = {'accept', 'content-length', 'content-type', 'if-range', 'range'}
RESPONSE_HEADERS = {'accept-ranges', 'content-disposition', 'content-length', 'content-range', 'content-type'}


def pages_origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.github.io')
            or parsed.port is not None or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not parsed.path.endswith('/')):
        raise ValueError('Configure FAIXALAB_PAGES_URL com a URL HTTPS do projeto no GitHub Pages.')
    return f'https://{parsed.netloc}'


def issue_token(key, scope, expires, account=''):
    value = f'{expires}.{account}' if account else str(expires)
    signature = hmac.new(key.encode(), f'{scope}:{value}'.encode(), hashlib.sha256).hexdigest()
    return f'{value}.{signature}'


def token_account(token, key, scope):
    """Return a Google account ID, '' for the owner session, or None if invalid."""
    parts = token.split('.')
    if len(parts) not in {2, 3}:
        return None
    expiry, signature = parts[0], parts[-1]
    account = parts[1] if len(parts) == 3 else ''
    if (not key or not re.fullmatch(r'[0-9]{1,12}', expiry) or not ACCOUNT_ID.fullmatch(signature)
            or int(expiry) <= time.time() or (len(parts) == 3 and not ACCOUNT_ID.fullmatch(account))):
        return None
    return account if hmac.compare_digest(token, issue_token(key, scope, expiry, account)) else None


class GoogleCredential(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)
    nonce: str = Field(pattern=r'^[a-zA-Z0-9_-]{43}$')


def verify_google_identity(credential, audience):
    from google.auth.exceptions import GoogleAuthError, TransportError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import verify_oauth2_token
    from requests import Session

    with Session() as client:
        # Use the library's certificate and claims checks with a bounded HTTP request.
        request = partial(GoogleRequest(session=client), timeout=8)
        try:
            return verify_oauth2_token(credential, request, audience)
        except TransportError as exc:
            raise RuntimeError('Serviço de identidade do Google indisponível.') from exc
        except GoogleAuthError as exc:
            raise ValueError('Identidade do Google inválida.') from exc


def create_app(upstream, url, key, backend_key='', google_client_id='', *,
               owner_google_sub='', check_upstream_health=False):
    if not key:
        raise ValueError('FAIXALAB_PAGES_KEY precisa estar configurada.')
    origin = pages_origin(url)
    challenges = {}
    challenge_lock = Lock()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(base_url=upstream, follow_redirects=False, trust_env=False,
                                     timeout=httpx.Timeout(240, connect=5)) as client:
            app.state.backend = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=[origin],
                       allow_methods=['GET', 'HEAD', 'POST', 'PATCH', 'DELETE', 'PUT'],
                       allow_headers=['Authorization', 'Content-Type', 'If-Range', 'Range'],
                       expose_headers=['Accept-Ranges', 'Content-Disposition', 'Content-Range'])

    @app.get('/health')
    async def health():
        if check_upstream_health:
            try:
                response = await app.state.backend.get('/api/health', timeout=3)
                response.raise_for_status()
            except httpx.HTTPError:
                return JSONResponse({'service': 'faixalab-pages', 'status': 'unavailable'}, status_code=503)
        return {'service': 'faixalab-pages'}

    @app.get('/auth/google/config')
    def google_config(request: Request):
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'Origem não autorizada.')
        if not google_client_id:
            return JSONResponse({'client_id': None}, headers={'Cache-Control': 'no-store'})
        now = time.monotonic()
        with challenge_lock:
            for stale in [nonce for nonce, deadline in challenges.items() if deadline <= now]:
                del challenges[stale]
            if len(challenges) >= 2048:
                raise HTTPException(429, 'Aguarde um pouco para entrar novamente.')
            nonce = secrets.token_urlsafe(32)
            challenges[nonce] = now + LOGIN_NONCE_SECONDS
        return JSONResponse({'client_id': google_client_id, 'nonce': nonce}, headers={'Cache-Control': 'no-store'})

    @app.post('/auth/google/session')
    def google_session(request: Request, data: GoogleCredential):
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'Origem não autorizada.')
        if not google_client_id:
            raise HTTPException(503, 'Login com Google ainda não configurado.')
        with challenge_lock:
            deadline = challenges.pop(data.nonce, 0)
        if deadline <= time.monotonic():
            raise HTTPException(401, 'Pedido de login expirado. Recarregue o FaixaLab e tente novamente.')
        try:
            identity = verify_google_identity(data.credential, google_client_id)
        except (ValueError, TypeError):
            raise HTTPException(401, 'O Google não confirmou a sessão.') from None
        except (ImportError, RuntimeError):
            raise HTTPException(503, 'O login do Google está indisponível.') from None
        subject = identity.get('sub')
        if identity.get('nonce') != data.nonce or not isinstance(subject, str) or not subject:
            raise HTTPException(401, 'O Google não confirmou esta solicitação de login.')
        account = '' if subject == owner_google_sub else hashlib.sha256(f'google:{subject}'.encode()).hexdigest()
        expires = int(time.time()) + SESSION_SECONDS
        return JSONResponse({'api': issue_token(key, 'api', expires, account),
                             'media': issue_token(key, 'media', expires, account)},
                            headers={'Cache-Control': 'no-store'})

    @app.api_route('/api/{path:path}', methods=['GET', 'HEAD', 'POST', 'PATCH', 'DELETE', 'PUT'])
    async def relay(request: Request, path: str):
        if request.headers.get('origin', origin) != origin or any(part in {'.', '..'} for part in path.split('/')):
            return JSONResponse({'detail': 'Origem não autorizada.'}, status_code=403)
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        account = token_account(token, key, 'api')
        if account is None and request.method in {'GET', 'HEAD'} and MEDIA_ROUTE.fullmatch(request.url.path):
            token = request.query_params.get('access', '')
            account = token_account(token, key, 'media')
        if account is None:
            return JSONResponse({'detail': 'Entre novamente para acessar o FaixaLab.'}, status_code=401)

        headers = {name: value for name, value in request.headers.items() if name in REQUEST_HEADERS}
        headers['accept-encoding'] = 'identity'
        if backend_key:
            headers['x-faixalab-key'] = backend_key
        if account:
            headers[SESSION_HEADER] = token
        params = [(name, value) for name, value in request.query_params.multi_items() if name != 'access']
        body = None if request.method in {'GET', 'HEAD'} else request.stream()
        backend = request.app.state.backend
        upstream_request = backend.build_request(request.method, request.url.path, params=params, headers=headers, content=body)
        try:
            response = await backend.send(upstream_request, stream=True)
        except httpx.RequestError:
            return JSONResponse({'detail': 'O servidor de processamento está indisponível.'}, status_code=503)
        forwarded = {name: value for name, value in response.headers.items() if name in RESPONSE_HEADERS}
        forwarded.update({'cache-control': 'private, no-store', 'referrer-policy': 'no-referrer',
                          'x-content-type-options': 'nosniff'})
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers=forwarded,
                                 background=BackgroundTask(response.aclose))

    return app


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(create_app(f'http://127.0.0.1:{os.environ.get("PORT", "7860")}',
                           os.environ['FAIXALAB_PAGES_URL'], os.environ['FAIXALAB_PAGES_KEY'],
                           os.environ.get('FAIXALAB_API_KEY', ''), os.environ.get('FAIXALAB_GOOGLE_CLIENT_ID', '').strip(),
                           owner_google_sub=os.environ.get('FAIXALAB_OWNER_GOOGLE_SUB', '').strip(),
                           check_upstream_health=os.environ.get('FAIXALAB_REMOTE') == '1'
                           or os.environ.get('CODESPACES') == 'true'),
                host='0.0.0.0', port=7861, access_log=False)
