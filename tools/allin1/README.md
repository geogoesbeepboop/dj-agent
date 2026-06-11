# allin1 install for the CLI bridge (ADR 0012)

`dj/audio/segment.py` drives **allin1** through its CLI when the Python package
isn't importable from the project venv (torch/NATTEN pins make it hard to keep
in-venv). The working install lives in the python.org framework Python 3.14:

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14
$PY -m pip install allin1 natten torchcodec
# macOS framework Pythons ship without SSL certs — model downloads fail without:
bash "/Applications/Python 3.14/Install Certificates.command"
```

## The dinat.py patch (REQUIRED on this setup)

allin1 1.1.0 targets NATTEN ≤0.17. NATTEN 0.20+ replaced the functional API,
and its **only CPU backend is torch Flex Attention, uncompiled** — which
materializes full T×T attention matrices: **~11 GB peak RSS for a 30-second
clip**; a full track OOM-killed a 16 GB M1 Pro (hard system crash, 2026-06-11).

[dinat.py](dinat.py) is the fixed model file. Copy it over the installed one:

```bash
cp tools/allin1/dinat.py \
  /Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages/allin1/models/dinat.py
```

What it does instead:
- **windowed neighborhood attention in pure torch** — each query gathers its
  `kernel_size=5` neighbors directly, so memory is O(T·k), not O(T²). Full
  track: ~5 GB peak (vs OOM), 30 s clip: 2.1 GB (vs 11.3 GB).
- **restores the trained relative positional bias (rpb)** that the NATTEN 0.20+
  functional API dropped — measurably better outputs (a 30 s clip's BPM went
  from `None` to a real estimate after the fix).
- dilation handled by exact regrouping (two batched calls, no padding); verified
  numerically against `natten.functional.na1d/na2d` to float32 epsilon at
  dilations 1/2/4/8 (1D) and 5×5 (2D).

After patching, NATTEN itself is no longer imported at runtime (the file has no
`import natten`), but leave it installed so `pip check` stays quiet.

## Memory notes for bulk ingest (16 GB machine)

- allin1's demucs step chunks internally (~8 s windows) — it is NOT the memory
  hog; the transformer inference was.
- One full track ≈ 5 GB peak in the subprocess + CLAP (~2 GB) resident in the
  curator process. Fine on 16 GB, but close Chrome/DAWs for big batches.
- Results cache as JSON under `~/.cache/dj-agent/allin1` (`DJ_ALLIN1_CACHE`);
  re-ingest never re-runs the model.
