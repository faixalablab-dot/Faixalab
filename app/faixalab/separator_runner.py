"""Demucs workers: tuned ensemble, harmonic refinement, shared-gain WAVs."""
import argparse
import gc
import json
from pathlib import Path
import random
from urllib.error import URLError
import torch
import soundfile as sf
from demucs.pretrained import get_model
from demucs.apply import apply_model, BagOfModels
from .compute import configure_torch


DEMUCS_MIRROR = ('https://huggingface.co/iBoostAI/Demucs-v4/resolve/'
                 'bffc1c135c7797974bfcb940f3a0ffe6ca793e35/')
DEMUCS_CHECKPOINTS = {
    'htdemucs': (
        ('955717e8-8726e21a.th', '8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4'),
    ),
    'htdemucs_6s': (
        ('5c90dfd2-34c22ccb.th', '34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd'),
    ),
    'htdemucs_ft': (
        ('f7e0c4bc-ba3fe64a.th', 'ba3fe64ae8ef66ac9a4857222ce48efbdc5eb3ad375cb79dd13debee5aaa4066'),
        ('d12395a8-e57c48e6.th', 'e57c48e6b0e38af4f7118d7bd08c49f0a0c0edf7d09143bdd902ea0d237303e6'),
        ('92cfc3b6-ef3bcb9c.th', 'ef3bcb9c8b40d14ae5d51b6db2587339cc12c6b77c0be151ce6d69002e087bf2'),
        ('04573f0d-f3cf25b2.th', 'f3cf25b222c4eed7cd49dd8b2c9597d50c18bd154090f7b919cfa5f93cf22c49'),
    ),
}


def load_demucs(name):
    try:
        return get_model(name)
    except (URLError, TimeoutError):
        checkpoints = DEMUCS_CHECKPOINTS.get(name)
        if checkpoints is None:
            raise
    cache = Path(torch.hub.get_dir()) / 'checkpoints'
    cache.mkdir(parents=True, exist_ok=True)
    for filename, checksum in checkpoints:
        destination = cache / filename
        if not destination.is_file():
            print(f'Downloading Demucs checkpoint {filename}', flush=True)
            torch.hub.download_url_to_file(DEMUCS_MIRROR + filename, destination,
                                           hash_prefix=checksum, progress=False)
    return get_model(name)


class ProgressPool:
    """Demucs' serial, lazy executor, with progress after each actual window.

    apply_model submits all overlap-add windows before requesting their results.
    Keeping this contract preserves its padding, shifts and reconstruction.
    """
    def __init__(self, report):
        self.report, self.total, self.completed = report, 0, 0

    def submit(self, function, *args, **kwargs):
        self.total += 1
        owner = self

        class Result:
            def result(self):
                owner.report(owner.completed / owner.total,
                             f'trecho {owner.completed + 1}/{owner.total}')
                value = function(*args, **kwargs)
                owner.completed += 1
                owner.report(owner.completed / owner.total,
                             f'{owner.completed}/{owner.total} trechos concluídos')
                return value

        return Result()


def infer(audio, name, device, shifts, overlap, report=None):
    model = load_demucs(name).eval()
    reference = audio.mean(0)
    mean, scale = reference.mean(), reference.std().clamp_min(1e-8)
    normalized = ((audio - mean) / scale)[None]
    with torch.inference_mode():
        members = list(zip(model.models, model.weights)) if isinstance(model, BagOfModels) else [(model, [1] * len(model.sources))]
        prediction = torch.zeros(len(model.sources), *audio.shape)
        weights = torch.zeros(len(model.sources))
        for index, (member, gains) in enumerate(members):
            def chunk_progress(fraction, text):
                if report:
                    title = f'Refinando instrumentos — etapa {index + 1}/{len(members)}' if len(members) > 1 else 'Separando instrumentos'
                    report((index + fraction) / len(members),
                           f'{title} · {text}')
            pool = ProgressPool(chunk_progress)
            out = apply_model(member, normalized, device=device, shifts=shifts,
                              split=True, overlap=overlap, segment=7,
                              progress=False, num_workers=0, pool=pool)[0].cpu()
            gains = torch.tensor(gains)
            prediction += out * gains[:, None, None]
            weights += gains
            member.to('cpu')
        prediction /= weights[:, None, None]
        prediction = prediction * scale + mean
    sources = {source: prediction[i] for i, source in enumerate(model.sources)}
    del model
    gc.collect()
    if device == 'cuda':
        torch.cuda.empty_cache()
    return sources


def separate(audio, stems=6, quality='quality', device='cpu', report=None):
    random.seed(2026)
    torch.manual_seed(2026)
    shifts = 0 if quality == 'fast' else 1
    overlap = .25 if quality == 'fast' else .5
    if quality == 'quality':
        primary = infer(audio, 'htdemucs_ft', device, shifts, overlap,
                        lambda fraction, text: report(fraction * (.8 if stems == 6 else 1), text) if report else None)
        if stems == 6:
            if report:
                report(.8, 'Isolando guitarra e piano do acompanhamento limpo')
            harmonic = audio - primary['vocals'] - primary['drums'] - primary['bass']
            extra = infer(harmonic, 'htdemucs_6s', device, shifts, overlap,
                          lambda fraction, text: report(.8 + .2 * fraction, text) if report else None)
            primary['guitar'], primary['piano'] = extra['guitar'], extra['piano']
        model_name = 'htdemucs_ft + htdemucs_6s' if stems == 6 else 'htdemucs_ft'
    else:
        name = 'htdemucs_6s' if stems == 6 else 'htdemucs'
        primary = infer(audio, name, device, shifts, overlap, report)
        model_name = name
    # Preserve each original sample and relative gain. Put the reconstruction
    # residual in Other, rather than injecting it into isolated target sources.
    primary['other'] = audio - sum(value for key, value in primary.items() if key != 'other')
    return primary, {'model': model_name, 'quality': quality, 'overlap': overlap,
                     'shifts': shifts, 'mixture_consistent': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio')
    parser.add_argument('output')
    parser.add_argument('--stems', type=int, choices=[4, 6], default=6)
    parser.add_argument('--quality', choices=['fast', 'balanced', 'quality'], default='quality')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    args = parser.parse_args()
    configure_torch()
    data, sr = sf.read(args.audio, dtype='float32', always_2d=True)
    if sr != 44100 or data.shape[1] != 2:
        raise ValueError('O áudio precisa estar em estéreo, a 44.100 Hz.')
    def report(fraction, stage):
        print('FAIXALAB_PROGRESS ' + json.dumps({'fraction': fraction, 'stage': stage}), flush=True)
    sources, metadata = separate(torch.from_numpy(data.T.copy()), args.stems, args.quality, args.device, report)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    for name, stem in sources.items():
        sf.write(out / (name + '.wav'), stem.numpy().T, sr, subtype='FLOAT')
    (out / 'engine.json').write_text(json.dumps(metadata, indent=2), 'utf-8')
    print('FAIXALAB_SEPARATION_COMPLETE', flush=True)


if __name__ == '__main__':
    main()
