# TataTuya

Aplicație desktop în limba română pentru salvarea citirilor cumulative ale
contoarelor Tuya și calcularea exactă a costului dintre două citiri.

## Dezvoltare

Este necesar Python 3.11 sau mai nou.

```bash
python -m pip install -e '.[dev]'
python -m tatatuya
pytest
```

În timpul migrării interfeței, comanda veche `python main.py` rămâne
funcțională. Detaliile produsului și ordinea implementării se află în `docs/`.

