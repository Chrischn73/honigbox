#!/usr/bin/env python3
"""Vater-Sohn-Prinzip (Grandfather-Father-Son) Rotation fuer HonigBox-Backups.

Haelt automatisch eine sinnvolle Mischung aus taeglichen, woechentlichen,
monatlichen und jaehrlichen Archiven statt einfach nur die letzten N -
so bleibt auch aeltere Historie erhalten, ohne dass Zeitplan oder Stufen
manuell konfiguriert werden muessen. Einzige Stellschraube ist die
Gesamtanzahl (siehe backup.conf / Backup-Seite).

Aufruf: honigbox-backup-rotate.py <verzeichnis> <max_gesamt>
"""
import sys
import os
import re
import datetime

NAME_RE = re.compile(r"^honigbox-backup-(\d{4})-(\d{2})-(\d{2})-(\d{6})\.tar\.gz$")


def parse_dt(name):
    m = NAME_RE.match(name)
    if not m:
        return None
    y, mo, d, hms = m.groups()
    try:
        return datetime.datetime(int(y), int(mo), int(d), int(hms[0:2]), int(hms[2:4]), int(hms[4:6]))
    except ValueError:
        return None


def backups_to_delete(directory, keep_total):
    """Liste der Dateinamen, die geloescht werden sollen (alles, was in
    keine der vier Stufen faellt). Reihenfolge/Inhalt der behaltenen
    Dateien ist deterministisch bei gleichem Eingabestand."""
    entries = []
    for name in os.listdir(directory):
        dt = parse_dt(name)
        if dt:
            entries.append((dt, name))
    entries.sort(reverse=True)  # neueste zuerst
    if len(entries) <= keep_total:
        return []

    now = entries[0][0]
    daily_cutoff = now - datetime.timedelta(days=14)
    keep = set()

    # Feste Kontingente je Stufe statt eines gemeinsamen Topfes - sonst
    # wuerden Taeglich+Woechentlich bei einem knappen Budget das komplette
    # Kontingent aufbrauchen und fuer Monats-/Jahres-Archive nichts uebrig
    # lassen, was dem eigentlichen Sinn der Langzeit-Historie widerspraeche.
    # Jahres-Kontingent wird VOR dem (flexiblen) Monats-Rest reserviert,
    # damit auch bei einem knappen Gesamtbudget mindestens ein Jahres-Stand
    # sicher erhalten bleibt.
    daily_budget = min(14, keep_total)
    remaining = keep_total - daily_budget
    weekly_budget = min(4, remaining)
    remaining -= weekly_budget
    yearly_budget = min(3, remaining)
    remaining -= yearly_budget
    monthly_budget = remaining  # Rest - waechst mit einem groesseren Gesamtbudget

    # Stufe 1: taeglich - jedes Archiv der letzten 14 Tage einzeln behalten.
    for dt, name in entries:
        if dt >= daily_cutoff and daily_budget > 0:
            keep.add(name)
            daily_budget -= 1

    # Stufe 2: woechentlich - je Kalenderwoche das neueste (aeltere als 7 Tage).
    seen_weeks = set()
    for dt, name in entries:
        if name in keep or dt >= daily_cutoff:
            continue
        wk = dt.isocalendar()[:2]
        if wk not in seen_weeks and weekly_budget > 0:
            seen_weeks.add(wk)
            keep.add(name)
            weekly_budget -= 1

    # Stufe 3: jaehrlich - je Kalenderjahr (ausser dem laufenden - das ist
    # durch Taeglich/Woechentlich/Monatlich ohnehin schon abgedeckt) das
    # neueste noch nicht behaltene Archiv.
    seen_years = set()
    for dt, name in entries:
        if name in keep or dt.year == now.year:
            continue
        if dt.year not in seen_years and yearly_budget > 0:
            seen_years.add(dt.year)
            keep.add(name)
            yearly_budget -= 1

    # Stufe 4: monatlich - je Kalendermonat das neueste (noch nicht behalten).
    seen_months = set()
    for dt, name in entries:
        if name in keep:
            continue
        ym = (dt.year, dt.month)
        if ym not in seen_months and monthly_budget > 0:
            seen_months.add(ym)
            keep.add(name)
            monthly_budget -= 1

    return [name for dt, name in entries if name not in keep]


def main():
    if len(sys.argv) != 3:
        print("Aufruf: honigbox-backup-rotate.py <verzeichnis> <max_gesamt>", file=sys.stderr)
        return 1
    directory, max_total = sys.argv[1], int(sys.argv[2])
    if not os.path.isdir(directory):
        return 0
    for name in backups_to_delete(directory, max_total):
        path = os.path.join(directory, name)
        try:
            os.remove(path)
            print(f"Altes Backup geloescht (Vater-Sohn-Rotation): {path}")
        except OSError as e:
            print(f"Konnte {path} nicht loeschen: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
