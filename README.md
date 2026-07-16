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

Pentru prototipul actual, copiați `.env.example` ca `.env` și completați
datele proiectului Tuya. Fișierul `.env` este ignorat de Git și nu trebuie
publicat.

În timpul migrării interfeței, comanda veche `python main.py` rămâne
funcțională. Detaliile produsului și ordinea implementării se află în `docs/`.
