# Windows studio setup

BeatForge is a local Windows application. Generation, corpus import, and
headset launch all assume this machine has Beat Saber installed and a Python
3.11+ venv at the repo root.

## Runtime

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python skills\beat-saber-mapping\scripts\bootstrap.py --tier core
```

Optional GPU: install a CUDA PyTorch wheel **into `.venv`**. If `torch_python.dll`
fails to load from the bootstrap `--target` cache, use the CPU index instead.
Do not loosen the 10 ms / 20 ms timing gates because a wheel failed.

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
python skills\beat-saber-mapping\scripts\bootstrap.py --tier models
```

Demucs is optional. When present, use **`htdemucs_6s`** only as the required
model (`demucs==4.0.1`). Missing stem separation leaves full-mix consensus in
charge; it cannot impersonate Beat This and BeatNet+.

## Beat Saber and the official corpus

The studio looks for CustomLevels at `BEATSABER_CUSTOM_LEVELS`, then Steam,
then the Oculus Beat Saber install. Sync the private first-party index:

```powershell
python skills\beat-saber-mapping\scripts\official_corpus.py sync
python skills\beat-saber-mapping\scripts\official_corpus.py report
```

The sqlite database stays under `%LOCALAPPDATA%\Codex\beat-saber-mapping\`
unless `BEATFORGE_CORPUS_DB` is set. Never commit it. Unexplained first-party
extraction failures leave generation in `corpus_incomplete`.

Installing a generated map into `Program Files` CustomLevels may prompt for
Windows administrator approval. Versioned folders such as
`Pacific_Coast_Highway_8` must not overwrite `Pacific_Coast_Highway` (V5).

## Codex review key

Store the Codex key only in Windows Credential Manager as `BeatForge:codex`.
The studio setup dialog writes it. It is never returned to the browser, written
to disk, or logged. A live review still needs a hash-bound consent and a rights
attestation for that exact zip.

## Tests

Local default (live markers skip themselves when hardware or keys are missing):

```powershell
$env:PYTHONPATH = "src;tools"
python -m pytest tests -p no:cacheprovider --basetemp test-tmp-app -q
$env:PYTHONPATH = "skills/beat-saber-mapping/scripts"
python -m pytest skills\beat-saber-mapping\tests -p no:cacheprovider --basetemp test-tmp-skill -q
```

CI runs the same suites with `-m "not network and not hardware and not codex"`
and skill `-m "not corpus"` so GitHub runners do not pretend to own Beat Saber
or API accounts.

After skill edits, update both `skills/beat-saber-mapping` and
`.codex/skills/beat-saber-mapping`, then run the skill tests.
