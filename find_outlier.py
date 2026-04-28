"""
find_outlier.py
Trova la coordinata outlier che fa esplodere la scala della planimetria.
Usa lo stesso parser di generate_d1_report.py per leggere gli oggetti.
"""
import sys
import os
import math
import re

# Aggiungi il path del progetto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importa il parser già scritto in generate_d1_report
from generate_d1_report import parse_xml

TARGET_FILES = [
    'D1_127238_84001610652.xml',
    'D3_193930_02599130651.xml',
    'D1_12345678901.xml',
    'D2_156381_02599130651.xml',
]

for fname in TARGET_FILES:
    if not os.path.exists(fname):
        continue
    print(f"\n{'='*60}")
    print(f"FILE: {fname}")
    print('='*60)

    try:
        result = parse_xml(fname)
        objects = result.get('objects', {})
    except Exception as ex:
        print(f"  ERRORE parse_xml: {ex}")
        continue

    if not objects:
        print("  Nessun oggetto trovato.")
        continue

    # Raccogli tutti i punti con riferimento all'oggetto e all'indice
    all_pts = []   # (E, N, object_key, point_index)
    for key, obj in objects.items():
        pts = obj.get('points', [])
        for i, p in enumerate(pts):
            e_val = p[0]
            n_val = p[1]
            all_pts.append((e_val, n_val, key, i+1))

    if not all_pts:
        print("  Nessun punto trovato negli oggetti.")
        continue

    es = [p[0] for p in all_pts]
    ns = [p[1] for p in all_pts]

    e_min, e_max = min(es), max(es)
    n_min, n_max = min(ns), max(ns)
    dx = e_max - e_min
    dy = n_max - n_min

    print(f"  Totale punti: {len(all_pts)}")
    print(f"  E (Est):  {e_min:.3f} -> {e_max:.3f}   delta={dx:.1f} m")
    print(f"  N (Nord): {n_min:.3f} -> {n_max:.3f}   delta={dy:.1f} m")

    if dx > 50000 or dy > 50000:
        print(f"\n  ⚠️  ANOMALIA RILEVATA! Delta {'E' if dx>dy else 'N'} = {max(dx,dy):.0f} m  (> 50 km!)")
        print("  Questa è la causa della scala esagerata.\n")
    else:
        print(f"\n  Scala attesa: ~1:{max(dx,dy)*1.07/0.170:.0f}")

    # Calcola mediana per trovare outlier
    es_sorted = sorted(es)
    ns_sorted = sorted(ns)
    e_med = es_sorted[len(es_sorted)//2]
    n_med = ns_sorted[len(ns_sorted)//2]

    # Distanza dalla mediana per ogni punto
    distances = []
    for (e, n, key, idx) in all_pts:
        d = math.sqrt((e - e_med)**2 + (n - n_med)**2)
        distances.append((d, e, n, key, idx))

    distances.sort(reverse=True)

    print(f"  Mediana cluster: E={e_med:.3f}, N={n_med:.3f}")
    print(f"\n  TOP 10 punti più lontani dalla mediana:")
    print(f"  {'Rango':<6} {'Oggetto':<12} {'Punto#':<8} {'E':>15} {'N':>15} {'Distanza':>12}")
    print(f"  {'-'*70}")
    for rank, (d, e, n, key, idx) in enumerate(distances[:10], 1):
        flag = "  ← OUTLIER PRINCIPALE" if rank == 1 and d > 1000 else ""
        print(f"  {rank:<6} {key:<12} {idx:<8} {e:>15.3f} {n:>15.3f} {d:>12.1f} m{flag}")

    # Mostra cosa succede se rimuoviamo il punto più anomalo
    if len(distances) > 1 and distances[0][0] > 100:
        print(f"\n  SIMULAZIONE: rimuovendo il punto più anomalo ({distances[0][3]}, pt#{distances[0][4]}):")
        remaining = [(e, n) for (d, e, n, key, idx) in distances[1:]]
        r_es = [p[0] for p in remaining]
        r_ns = [p[1] for p in remaining]
        r_dx = max(r_es) - min(r_es)
        r_dy = max(r_ns) - min(r_ns)
        print(f"    E delta: {r_dx:.1f} m   N delta: {r_dy:.1f} m")
        print(f"    Scala simulata: ~1:{max(r_dx, r_dy)*1.07/0.170:.0f}")

print("\nFine analisi.")
