"""Explicit spell conditions and passive explanations; no commands or client writes."""
import json
import math
import operator
from functools import lru_cache
from pathlib import Path
from combat_wave import plan_wave, facing
from dbv import paths

OPERATORS = {'>': operator.gt, '>=': operator.ge, '=': operator.eq, '<=': operator.le, '<': operator.lt}


@lru_cache(maxsize=1)
def catalogue_aims():
    # Local client metadata is optional. Unknown spells retain targeted behavior.
    try:
        rows = json.loads(paths.SPELL_CATALOG.read_text(encoding='utf-8'))['spells']
        return {r['words'].strip().casefold(): 'self' for r in rows
                if isinstance(r, dict) and r.get('category') == 'Area' and isinstance(r.get('words'), str)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def spell_aim(spell):
    return spell['aim'] if 'aim' in spell else catalogue_aims().get(spell['words'].strip().casefold(), 'target')


def validate_conditions(spell, row):
    row['aim'] = spell_aim(spell)
    if not isinstance(row['aim'], str) or row['aim'] not in {'self', 'target'}:
        raise ValueError('Nieprawidłowy obszar warunku czaru.')
    for field, default in [('ki', '>='), ('hp', '<='), ('range', '<='), ('count', '>=')]:
        op = spell.get(field+'_op', default)
        if not isinstance(op, str) or op not in OPERATORS:
            raise ValueError('Nieprawidłowy operator warunku czaru.')
        row[field+'_op'] = op
    for field, default in [('ki', 0), ('hp', None)]:
        unit = spell.get(field+'_unit', 'percent')
        if not isinstance(unit, str) or unit not in {'percent', 'value'}:
            raise ValueError('Jednostka HP/Ki: procent lub wartość.')
        value = spell.get(field, default)
        limit = (100 if field == 'hp' else 1000) if unit == 'percent' else 2**31-1
        if value is None and field == 'hp':
            pass
        elif type(value) is not int or not 0 <= value <= limit:
            raise ValueError(f'Nieprawidłowy próg {field.upper()}.')
        row[field], row[field+'_unit'] = value, unit
    for field, low, high in [('range', 1, 7), ('count', 0, 12)]:
        if type(row[field]) is not int or not low <= row[field] <= high:
            raise ValueError(f'Nieprawidłowe ustawienie czaru: {field}.')
    return row


def spell_reaches(spell, target):
    return spell.get('trigger', 'distance') == 'visible' or OPERATORS[spell.get('range_op', '<=')](target['distance'], spell['range'])


def resource_check(spell, gs, field):
    threshold = spell.get(field, 0 if field == 'ki' else None)
    if threshold is None:
        return None
    current = getattr(gs, 'mana' if field == 'ki' else 'hp', 0)
    maximum = getattr(gs, 'max_mana' if field == 'ki' else 'max_hp', 0)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (current, maximum)) or maximum <= 0:
        return f'Brak świeżego odczytu {field.upper()}'
    unit = spell.get(field+'_unit', 'percent')
    # DBVictory sends 0..100 with max=100 in its percentage display mode.
    # Never pretend those are actual HP/Ki points.
    if unit == 'value' and maximum == 100:
        return f'{field.upper()}: klient udostępnia procenty — przełącz pasek na wartości'
    value = current/maximum*100 if unit == 'percent' else current
    op = spell.get(field+'_op', '>=' if field == 'ki' else '<=')
    if not OPERATORS[op](value, threshold):
        return f'{field.upper()} {value:g}{"%" if unit == "percent" else ""} nie spełnia {op} {threshold}'
    return None


def evaluate_spell(spell, gs, candidates, target, now=0, deadlines=None, cast_after=0, sample=None):
    wave = plan_wave(spell, gs, candidates, target, sample)
    count = sum(spell_reaches(spell, c) and (wave is None or c['id'] in wave['hit_ids']) for c in candidates)
    remaining = max(0, (deadlines or {}).get(spell['words'].strip().casefold(), 0)-now, cast_after-now)
    reason = None
    if target is None:
        reason = 'Brak pasującego celu'
    elif spell_aim(spell) == 'target' and not spell_reaches(spell, target):
        reason = f'Cel: {target["distance"]} SQM — poza warunkiem odległości'
    if reason is None:
        reason = resource_check(spell, gs, 'ki') or resource_check(spell, gs, 'hp')
    if reason is None and wave is not None:
        reason = wave['reason']
    if reason is None and not OPERATORS[spell.get('count_op', '>=')](count, spell['count']):
        reason = f'Moby: {count} — wymagane {spell.get("count_op", ">=")} {spell["count"]}'
    conditions_met = reason is None
    if reason is None and remaining > 0:
        reason = f'Odstęp: jeszcze {remaining:.1f} s'
    return dict(words=spell['words'], aim=spell_aim(spell), matched_count=count,
                ready=reason is None, conditions_met=conditions_met, remaining=round(remaining, 2),
                reason=reason or 'Gotowy', selected=False,
                wave_direction=wave['direction'] if wave else None,
                wave_note=wave['note'] if wave else None,
                turn_required=bool(wave and wave['direction'] is not None and facing(gs)!=wave['direction']))


def evaluate_spells(spells, gs, candidates, target, now=0, deadlines=None, cast_after=0, sample=None):
    rows = [evaluate_spell(s, gs, candidates, target, now, deadlines, cast_after, sample) for s in spells]
    selected = next((row for row in rows if row['ready']), None)
    for row in rows:
        if row is selected:
            row.update(selected=True, reason='Obrót w stronę celu' if row['turn_required'] else 'Pierwszy gotowy — priorytet')
        elif row['ready']:
            row['reason'] = 'Czeka na czar wyżej na liście'
    return rows
