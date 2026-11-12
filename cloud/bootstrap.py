"""Materialize a pinned release using Codespaces' repository-scoped identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]


def download_archive(url, destination, manifest):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('O endereço do pacote precisa usar HTTPS sem credenciais na URL.')
    request=Request(url,headers={'User-Agent':'FaixaLab-cloud-setup'})
    total=0
    with urlopen(request,timeout=30) as response,Path(destination).open('wb') as output:
        while chunk:=response.read(1024*1024):
            total+=len(chunk)
            if total>manifest['bytes']:
                raise ValueError('O download excedeu o tamanho do pacote verificado.')
            output.write(chunk)
    if total!=manifest['bytes']:
        raise ValueError('O download do pacote ficou incompleto.')


def install_archive(archive_path, target, manifest):
    archive_path=Path(archive_path);target=Path(target)
    if archive_path.stat().st_size!=manifest['bytes']:
        raise ValueError('Tamanho inesperado do pacote FaixaLab.')
    with archive_path.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=manifest['sha256']:
            raise ValueError('SHA-256 do pacote FaixaLab não confere.')
    target.parent.mkdir(exist_ok=True,parents=True)
    marker=target/'.release-sha256'
    if marker.is_file() and marker.read_text().strip()==manifest['sha256']:return target
    if target.exists():raise RuntimeError('Já existe outro pacote em '+str(target)+'. Preserve-o antes de instalar outra versão.')
    with tempfile.TemporaryDirectory(prefix='faixalab_stage_',dir=target.parent) as temporary:
        stage=Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                path=Path(entry.filename)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=manifest['root']:
                    raise ValueError('Caminho inesperado dentro do pacote.')
                if stat.S_ISLNK(entry.external_attr>>16):raise ValueError('Link simbólico inesperado no pacote.')
            bad=archive.testzip()
            if bad:raise ValueError('CRC inválido: '+bad)
            archive.extractall(stage)
        extracted=stage/manifest['root']
        if not (extracted/'server.py').is_file():raise ValueError('O pacote não contém o servidor.')
        (extracted/'.release-sha256').write_text(manifest['sha256']+'\n')
        extracted.rename(target)
    return target


def main():
    parser=argparse.ArgumentParser()
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--archive',type=Path,help='ZIP local, para validar sem acesso à rede')
    source.add_argument('--url',help='URL HTTPS do mesmo ZIP verificado pelo manifesto')
    source.add_argument('--public-release',action='store_true',help='Baixar o release público identificado no manifesto')
    parser.add_argument('--target',type=Path,default=ROOT/'.app/FaixaLab')
    args=parser.parse_args()
    manifest=json.loads((ROOT/'release.json').read_text())
    marker=args.target/'.release-sha256'
    if marker.is_file() and marker.read_text().strip()==manifest['sha256']:
        print('FaixaLab '+manifest['tag']+' já está preparado.');return
    if args.archive:install_archive(args.archive,args.target,manifest)
    elif args.url or args.public_release:
        url=args.url
        if args.public_release:
            tag=quote(manifest['tag'],safe='')
            asset=quote(manifest['asset'],safe='')
            url=f"https://github.com/{manifest['repository']}/releases/download/{tag}/{asset}"
        with tempfile.TemporaryDirectory(prefix='faixalab_download_') as temporary:
            archive=Path(temporary)/manifest['asset']
            download_archive(url,archive,manifest)
            install_archive(archive,args.target,manifest)
    else:
        repository=os.environ.get('GITHUB_REPOSITORY') or manifest['repository']
        with tempfile.TemporaryDirectory(prefix='faixalab_download_') as temporary:
            subprocess.run(['gh','release','download',manifest['tag'],'--repo',repository,'--pattern',manifest['asset'],'--dir',temporary],check=True)
            install_archive(Path(temporary)/manifest['asset'],args.target,manifest)
    print('Pacote FaixaLab verificado e extraído.')

if __name__=='__main__':main()
