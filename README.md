# TataTuya

TataTuya este o aplicație desktop în limba română pentru salvarea indexurilor
cumulative ale contoarelor de energie Tuya și calcularea exactă a costului dintre
două citiri. Aplicația citește date din Tuya, dar nu trimite comenzi și nu
modifică dispozitivele.

## Instalare pe Apple Silicon

1. Descarcă fișierul `TataTuya-<versiune>-arm64.dmg` din pagina GitHub Releases.
2. Deschide imaginea DMG și trage `TataTuya.app` peste scurtătura `Applications`.
3. Ejectează imaginea DMG, apoi deschide TataTuya din dosarul Applications.
4. Deschide `Setări`, completează Client ID, Client Secret și regiunea Tuya,
   testează conexiunea și salvează.

Versiunea inițială este distribuită fără semnătură Apple și fără notarizare. La
prima pornire, macOS poate bloca deschiderea obișnuită. În Finder, deschide
`Applications`, fă Control-click sau click dreapta pe TataTuya, alege `Open`,
apoi confirmă din nou `Open`. Această excepție este necesară o singură dată.

Datele locale sunt salvate în
`~/Library/Application Support/TataTuya/tatatuya.sqlite3`. Citirile și calculele
nu sunt șterse la închiderea sau actualizarea aplicației.

## Dezvoltare

Este necesar Python 3.11 sau mai nou.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,package]"
python -m pytest
python -m tatatuya
```

Pentru a construi distribuția este necesar un Mac Apple Silicon:

```bash
./scripts/build_macos.sh
./scripts/create_dmg.sh 0.1.0
```

Primul script creează `dist/TataTuya.app`, verifică arhitectura executabilului și
rulează un test al resurselor și migrărilor incluse. Al doilea creează
`dist/TataTuya-0.1.0-arm64.dmg`, cu aplicația și o scurtătură spre Applications.

Pregătirea unui release este declanșată de o etichetă Git care corespunde exact
versiunii din `pyproject.toml`, de exemplu `v0.1.0`. Workflow-ul ARM64 rulează
verificările, construiește DMG-ul și îl atașează la un GitHub Release în stare
draft. Release-ul rămâne nepublicat până când repetiția Phase 12 pe un Mac curat
confirmă instalarea, inițializarea bazei de date, deschiderea Settings și testul
de conexiune. Nu introduce credențiale reale în surse, fișiere de configurare
sau artefacte de release.

## Depanare

- Dacă macOS raportează că dezvoltatorul nu poate fi verificat, folosește pașii
  Control-click → `Open` de mai sus; nu dezactiva Gatekeeper global.
- Dacă lipsește configurația, intră în `Setări`; aplicația nu are wizard la
  prima pornire.
- Jurnalul local este în
  `~/Library/Application Support/TataTuya/tatatuya.log` și nu include secretele
  Tuya.
