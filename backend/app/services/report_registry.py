"""Registar izvezenih izveštaja — provera da li je dokument naknadno menjan.

Nacrtani potpis analitičara je IZJAVA ("ja sam izradio ovaj izveštaj"), a ne dokaz o
nepromenjenosti: potpis je slika u PDF-u i ostaje netaknut i kada neko izmeni sadržaj.

Ono što stvarno omogućava proveru je otisak sadržaja (SHA-256) izračunat nad podacima na
kojima izveštaj počiva — slučaj, izvori, procenti, heševi evidencije. Otisak se registruje
na serveru pre generisanja dokumenta, a kontrolni broj se štampa u sam izveštaj. Kasnija
provera poredi ono što piše u dokumentu sa onim što je zabeleženo u trenutku izvoza.

Granica koju treba znati: ovo dokazuje da se **podaci** poklapaju sa registrovanim, ne da
je PDF fajl bajt-po-bajt isti. Za to bi bio potreban kriptografski potpis dokumenta
(PAdES i sl.), što je van obima ovog rada.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR


def _registry_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / 'report_registry.json'


def _load() -> list[dict[str, Any]]:
    path = _registry_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _save(entries: list[dict[str, Any]]) -> None:
    _registry_path().write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Otisak sadržaja izveštaja.

    Ključevi se sortiraju i separatori fiksiraju da bi isti podaci uvek dali isti otisak,
    bez obzira na redosled polja u zahtevu — inače provera ne bi bila ponovljiva.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _generate_code() -> str:
    """Kontrolni broj u obliku LUSI-2026-A7F3-9K2M.

    Bez znakova koji se lako mešaju pri prepisivanju (0/O, 1/I), jer se broj prepisuje
    rukom sa odštampanog izveštaja.
    """
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    year = datetime.now(timezone.utc).year
    blocks = ['' .join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)]
    return f'LUSI-{year}-{blocks[0]}-{blocks[1]}'


def register_report(
    *,
    case_id: str,
    case_name: str,
    content_hash: str,
    analyst: str,
    declaration: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    entries = _load()

    # A collision would silently overwrite an earlier report's record, so the code is
    # regenerated until it is unique rather than trusted to be.
    existing_codes = {entry['verification_code'] for entry in entries}
    code = _generate_code()
    while code in existing_codes:
        code = _generate_code()

    entry = {
        'verification_code': code,
        'content_hash': content_hash,
        'case_id': case_id,
        'case_name': case_name,
        'analyst': analyst,
        'declaration': declaration,
        'summary': summary,
        'registered_at': datetime.now(timezone.utc).isoformat(),
    }
    entries.append(entry)
    _save(entries)
    return entry


def verify_report(*, verification_code: str, content_hash: str | None = None) -> dict[str, Any]:
    """Traži registrovani izveštaj i, ako je dat otisak, poredi ga sa zabeleženim."""
    code = verification_code.strip().upper()
    entry = next((item for item in _load() if item['verification_code'] == code), None)

    if entry is None:
        return {
            'found': False,
            'matches': None,
            'message': 'Kontrolni broj ne postoji u registru. Izveštaj nije izvezen iz ove instalacije Lusi-ja, '
                       'ili je broj pogrešno prepisan.',
            'entry': None,
        }

    if content_hash is None:
        return {
            'found': True,
            'matches': None,
            'message': 'Izveštaj je pronađen u registru. Za proveru sadržaja unesite i otisak sa izveštaja.',
            'entry': entry,
        }

    matches = content_hash.strip().lower() == entry['content_hash']
    return {
        'found': True,
        'matches': matches,
        'message': (
            'Otisak se poklapa sa registrovanim — podaci u izveštaju odgovaraju onome što je zabeleženo pri izvozu.'
            if matches
            else 'OTISAK SE NE POKLAPA. Sadržaj izveštaja se razlikuje od onoga što je zabeleženo pri izvozu.'
        ),
        'entry': entry,
    }
