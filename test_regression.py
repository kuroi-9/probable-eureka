#!/usr/bin/env python3
"""Suite de non-régression exhaustive pour dac_correction_toolkit.py.
Génère des signaux synthétiques (aucun matériel requis), exécute chaque
sous-commande via subprocess (comme un vrai utilisateur), et vérifie les
résultats numériques et le comportement attendu (y compris les cas limites
et les erreurs volontaires)."""

import csv
import json
import os
import subprocess
import sys
import shutil
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

import argparse

# Par défaut, cherche dac_correction_toolkit.py dans le même dossier que ce
# script de test (donc ça marche tel quel si les deux fichiers sont
# côte à côte, où que tu les aies placés). Override possible avec --toolkit.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--toolkit", default=os.path.join(_SCRIPT_DIR, "dac_correction_toolkit.py"))
_args, _ = _parser.parse_known_args()
TOOLKIT = os.path.abspath(_args.toolkit)
if not os.path.exists(TOOLKIT):
    sys.exit(f"Erreur: dac_correction_toolkit.py introuvable à '{TOOLKIT}'.\n"
             f"Place ce script à côté de dac_correction_toolkit.py, ou précise "
             f"--toolkit /chemin/vers/dac_correction_toolkit.py")
WORKDIR = os.path.join(_SCRIPT_DIR, "regression_work")

results = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and status == "FAIL" else ""))
    return condition


def run(*args, expect_fail=False):
    cmd = ["python3", TOOLKIT] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=WORKDIR)
    if expect_fail:
        return r
    if r.returncode != 0:
        print(f"  >>> COMMANDE ECHOUEE: {' '.join(args)}")
        print(f"  stdout: {r.stdout[-500:]}")
        print(f"  stderr: {r.stderr[-500:]}")
    return r


def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def make_wav(path, sr, data):
    wavfile.write(path, sr, data.astype(np.float32))


# ----------------------------------------------------------------------------
os.makedirs(WORKDIR, exist_ok=True)
os.chdir(WORKDIR)
print("=" * 70)
print("1. GENERATE-SWEEP")
print("=" * 70)

r = run("generate-sweep", "-o", "sweep.wav", "--duration", "15")
check("generate-sweep: exécution réussie", r.returncode == 0, r.stderr)
check("generate-sweep: fichier créé", os.path.exists("sweep.wav"))
check("generate-sweep: .meta.json créé", os.path.exists("sweep.wav.meta.json"))
with open("sweep.wav.meta.json") as f:
    meta = json.load(f)
check("generate-sweep: meta f_min/f_max corrects", meta["f_min"] == 20.0 and meta["f_max"] == 20000.0,
      str(meta))
check("generate-sweep: marge appliquée (f_max_generated > f_max)", meta["f_max_generated"] > meta["f_max"])

r = run("generate-sweep", "-o", "sweep_custom.wav", "--duration", "10", "--f-min", "50",
        "--f-max", "15000", "--margin-ratio", "0.2", "--samplerate", "48000")
check("generate-sweep: bande personnalisée OK", r.returncode == 0, r.stderr)
with open("sweep_custom.wav.meta.json") as f:
    meta_c = json.load(f)
check("generate-sweep: bande personnalisée propagée dans meta",
      meta_c["f_min"] == 50.0 and meta_c["f_max"] == 15000.0)

r = run("generate-sweep", "-o", "sweep_L.wav", "--duration", "8", "--mono-channel", "L")
sr, data = wavfile.read("sweep_L.wav")
check("generate-sweep --mono-channel L: canal R bien silencieux", np.abs(data[:, 1]).max() < 1e-6)
check("generate-sweep --mono-channel L: canal L bien actif", np.abs(data[:, 0]).max() > 0.1)

r = run("generate-sweep", "-o", "sweep_hires.wav", "--duration", "10", "--f-min", "15",
        "--f-max", "45000", "--samplerate", "192000", "--margin-ratio", "0.1")
check("generate-sweep: sample rate élevé (192kHz, bande large) OK", r.returncode == 0, r.stderr)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("2. GENERATE-NOISE")
print("=" * 70)

r = run("generate-noise", "-o", "noise.wav", "--duration", "20", "--seed", "42")
check("generate-noise: exécution réussie", r.returncode == 0, r.stderr)
check("generate-noise: fichier + meta créés", os.path.exists("noise.wav") and os.path.exists("noise.wav.meta.json"))

r = run("generate-noise", "-o", "noise_R.wav", "--duration", "8", "--mono-channel", "R", "--seed", "1")
sr, data = wavfile.read("noise_R.wav")
check("generate-noise --mono-channel R: canal L bien silencieux", np.abs(data[:, 0]).max() < 1e-6)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("3. MEASURE (stéréo classique) + dérive d'horloge + cohérence")
print("=" * 70)

sr, sw = wavfile.read("sweep.wav")
sig = sw.astype(np.float64)

# Cas simple: gain constant connu, pas de dérive
rec_simple = sig * 10 ** (0.3 / 20)
make_wav("rec_simple.wav", sr, rec_simple)
r = run("measure", "sweep.wav", "rec_simple.wav", "-o", "mesure_simple.csv")
check("measure: exécution réussie (cas simple)", r.returncode == 0, r.stderr)
rows = read_csv("mesure_simple.csv")
diffs = [float(row["diff_R_moins_L_dB"]) for row in rows]
check("measure: diff proche de 0 (même gain L/R)", abs(np.mean(diffs)) < 0.01, f"moyenne={np.mean(diffs):.4f}")
check("measure: colonnes cohérence présentes", "coherence_L" in rows[0] and "coherence_R" in rows[0])
cohs = [float(row["coherence_L"]) for row in rows]
check("measure: cohérence quasi 1 (signal propre)", np.mean(cohs) > 0.99, f"moyenne={np.mean(cohs):.4f}")

# Cas avec déséquilibre L/R + dérive d'horloge connue
recL = sig[:, 0] * 10 ** (-0.10 / 20)
recR = sig[:, 1] * 10 ** (0.25 / 20)
rec_drift = np.column_stack([recL, recR])
up, down = 1_000_000 + 400, 1_000_000
rec_drift = resample_poly(rec_drift, up, down, axis=0)
rec_drift += np.random.normal(0, 0.0005, rec_drift.shape)
make_wav("rec_drift.wav", sr, rec_drift)

r = run("measure", "sweep.wav", "rec_drift.wav", "-o", "mesure_drift.csv")
check("measure: exécution avec dérive OK", r.returncode == 0, r.stderr)
check("measure: dérive détectée et affichée", "Dérive d'horloge estimée" in r.stdout)
rows = read_csv("mesure_drift.csv")
diffs = [float(row["diff_R_moins_L_dB"]) for row in rows]
expected = 0.25 - (-0.10)
check("measure: diff L/R retrouvée malgré la dérive", abs(np.mean(diffs) - expected) < 0.02,
      f"attendu={expected}, obtenu={np.mean(diffs):.4f}")

r_nodrift = run("measure", "sweep.wav", "rec_drift.wav", "-o", "mesure_nodrift.csv", "--no-drift-correction")
check("measure --no-drift-correction: exécution OK", r_nodrift.returncode == 0, r_nodrift.stderr)
check("measure --no-drift-correction: pas de dérive affichée", "Dérive d'horloge estimée" not in r_nodrift.stdout)

# Cas: sample rates différents -> doit échouer proprement
r = run("measure", "sweep.wav", "sweep_hires.wav", "-o", "should_fail.csv", expect_fail=True)
check("measure: rejette proprement des sample rates différents", r.returncode != 0 and "Sample rates" in r.stdout + r.stderr)

# Cas: fichier mono passé à measure (nécessite stéréo) -> doit échouer proprement
r = run("measure", "sweep_L.wav", "noise_R.wav", "-o", "should_fail2.csv", expect_fail=True)
# sweep_L et noise_R ont des durées différentes mais tous deux "stereo" (2 canaux, un silencieux) donc ne devrait pas échouer sur ce critère precis
# Test plus pertinent : fichier réellement mono
data_mono = sig[:, 0]
wavfile.write("truly_mono.wav", sr, data_mono.astype(np.float32))
r = run("measure", "sweep.wav", "truly_mono.wav", "-o", "should_fail3.csv", expect_fail=True)
check("measure: rejette proprement un enregistrement mono", r.returncode != 0 and "stéréo" in (r.stdout + r.stderr))

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("4. MEASURE-MONO + auto-détection f-min/f-max via .meta.json")
print("=" * 70)

recA = (sig[:, 0] * 10 ** (-0.10 / 20)).astype(np.float32)
recB = (sig[:, 0] * 10 ** (0.25 / 20)).astype(np.float32)
make_wav("mono_A.wav", sr, recA)
make_wav("mono_B.wav", sr, recB)

r = run("measure-mono", "sweep.wav", "mono_A.wav", "-o", "mesA.csv")
check("measure-mono: exécution OK", r.returncode == 0, r.stderr)
check("measure-mono: bande auto-détectée depuis meta.json", "métadonnées de sweep.wav.meta.json" in r.stdout)
rows = read_csv("mesA.csv")
check("measure-mono: colonne gain_dB présente", "gain_dB" in rows[0])
check("measure-mono: colonne coherence présente", "coherence" in rows[0])
gains = [float(row["gain_dB"]) for row in rows]
check("measure-mono: gain retrouvé (-0.10dB)", abs(np.mean(gains) - (-0.10)) < 0.01, f"obtenu={np.mean(gains):.4f}")

run("measure-mono", "sweep.wav", "mono_B.wav", "-o", "mesB.csv")

# Override explicite de f-min/f-max
r = run("measure-mono", "sweep.wav", "mono_A.wav", "-o", "mesA_explicit.csv", "--f-min", "100", "--f-max", "5000")
check("measure-mono: override explicite prioritaire", "valeurs explicites" in r.stdout)
rows = read_csv("mesA_explicit.csv")
freqs = [float(row["freq_hz"]) for row in rows]
check("measure-mono: bande explicite respectée", min(freqs) >= 100 and max(freqs) <= 5000)

# Sans meta.json (renommé/déplacé) -> repli sur 20-20000 avec avertissement
shutil.copy("sweep.wav", "sweep_nometa.wav")
r = run("measure-mono", "sweep_nometa.wav", "mono_A.wav", "-o", "mes_nometa.csv")
check("measure-mono: repli 20-20000Hz si pas de meta.json", "défaut 20-20000" in r.stdout)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("5. DIFF (calibration en boucle classique)")
print("=" * 70)

r = run("diff", "mesure_drift.csv", "mesure_drift.csv", "-o", "diff_self.csv")
check("diff: exécution OK (auto-calibration = soi-même)", r.returncode == 0, r.stderr)
rows = read_csv("diff_self.csv")
diffs = [float(row["diff_nette_dB"]) for row in rows]
check("diff: soustraction de soi-même donne ~0", abs(np.mean(diffs)) < 1e-6, f"obtenu={np.mean(diffs)}")

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("6. SWAP-DIFF (élimination algébrique de la coloration carte son)")
print("=" * 70)

# Simule diff1 = deltaD + deltaM, diff2 = -deltaD + deltaM
freqs_sim = np.linspace(20, 20000, 200)
delta_D_true = 0.30
delta_M_true = 0.6 * np.sin(np.log10(freqs_sim))
diff1 = delta_D_true + delta_M_true
diff2 = -delta_D_true + delta_M_true

for fname, diffv in [("swap_normal.csv", diff1), ("swap_inv.csv", diff2)]:
    with open(fname, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "gain_L_dB", "gain_R_dB", "diff_R_moins_L_dB"])
        for fr, d in zip(freqs_sim, diffv):
            w.writerow([f"{fr:.3f}", "0", f"{d:.4f}", f"{d:.4f}"])

r = run("swap-diff", "swap_normal.csv", "swap_inv.csv", "-o", "swap_result.csv")
check("swap-diff: exécution OK", r.returncode == 0, r.stderr)
rows = read_csv("swap_result.csv")
recovered = np.array([float(row["diff_nette_dB"]) for row in rows])
check("swap-diff: deltaD retrouvé exactement (M annulée)", np.allclose(recovered, delta_D_true, atol=1e-6),
      f"max erreur={np.abs(recovered-delta_D_true).max()}")

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("7. MONO-DIFF (les deux formats d'entrée)")
print("=" * 70)

r = run("mono-diff", "mesA.csv", "mesB.csv", "-o", "monodiff_result.csv")
check("mono-diff (format measure-mono): exécution OK", r.returncode == 0, r.stderr)
rows = read_csv("monodiff_result.csv")
diffs = [float(row["diff_nette_dB"]) for row in rows]
expected = 0.25 - (-0.10)
check("mono-diff (format measure-mono): résultat correct", abs(np.mean(diffs) - expected) < 0.01,
      f"attendu={expected}, obtenu={np.mean(diffs):.4f}")

# Format measure classique + --channel
r = run("mono-diff", "mesure_simple.csv", "mesure_drift.csv", "-o", "should_fail4.csv", expect_fail=True)
check("mono-diff: échoue proprement sans --channel sur un CSV measure classique",
      r.returncode != 0 and "--channel" in (r.stdout + r.stderr))

r = run("mono-diff", "mesure_simple.csv", "mesure_drift.csv", "-o", "monodiff_channel.csv", "--channel", "L")
check("mono-diff --channel L: exécution OK", r.returncode == 0, r.stderr)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("8. MULTIVOLUME (gain fixe vs dépendant du volume)")
print("=" * 70)

# Cas gain fixe: même courbe à 2 "volumes"
with open("vol1.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["freq_hz", "diff_nette_dB"])
    for fr in np.geomspace(20, 20000, 100):
        w.writerow([f"{fr:.2f}", "0.350"])
with open("vol2.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["freq_hz", "diff_nette_dB"])
    for fr in np.geomspace(20, 20000, 100):
        w.writerow([f"{fr:.2f}", "0.352"])
r = run("multivolume", "vol1.csv", "vol2.csv")
check("multivolume: exécution OK", r.returncode == 0, r.stderr)
check("multivolume: détecte correctement 'GAIN FIXE'", "GAIN FIXE" in r.stdout)

# Cas dépendant du volume: courbes très différentes
with open("vol3.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["freq_hz", "diff_nette_dB"])
    for fr in np.geomspace(20, 20000, 100):
        w.writerow([f"{fr:.2f}", "1.200"])
r = run("multivolume", "vol1.csv", "vol3.csv")
check("multivolume: détecte correctement 'DÉPEND DU VOLUME'", "DÉPEND DU VOLUME" in r.stdout)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("9. BUILD-EQ (lissage, anti-écrêtage, bande non plafonnée)")
print("=" * 70)

r = run("build-eq", "monodiff_result.csv", "-o", "profile_std.json", "--points", "20")
check("build-eq: exécution OK (cas standard)", r.returncode == 0, r.stderr)
with open("profile_std.json") as f:
    profile_std = json.load(f)
freqs_p = [p["freq"] for p in profile_std["points"]]
check("build-eq: plage standard ~20-20000Hz", freqs_p[0] < 25 and freqs_p[-1] > 19000)

# Vérifie la protection anti-écrêtage (jamais de boost net)
gains_L = [p["gain_L_dB"] for p in profile_std["points"]]
gains_R = [p["gain_R_dB"] for p in profile_std["points"]]
check("build-eq: pas de boost net (anti-écrêtage)", max(max(gains_L), max(gains_R)) <= 1e-9,
      f"max={max(max(gains_L), max(gains_R))}")

# Test bande large (au-delà de 20kHz) -> ne doit plus être plafonné
with open("wideband.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["freq_hz", "diff_nette_dB"])
    for fr in np.geomspace(15, 40000, 300):
        w.writerow([f"{fr:.2f}", "0.2"])
r = run("build-eq", "wideband.csv", "-o", "profile_wide.json", "--points", "20")
check("build-eq: exécution OK (bande large)", r.returncode == 0, r.stderr)
with open("profile_wide.json") as f:
    profile_wide = json.load(f)
freqs_pw = [p["freq"] for p in profile_wide["points"]]
check("build-eq: plage large bande NON plafonnée à 20kHz", freqs_pw[-1] > 30000, f"max freq={freqs_pw[-1]}")

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("10. APPLY (fichier unique)")
print("=" * 70)

t = np.linspace(0, 2, 44100 * 2)
test_sig = 0.3 * np.sin(2 * np.pi * 440 * t)
make_wav("morceau_test.wav", 44100, np.column_stack([test_sig, test_sig]))

r = run("apply", "profile_std.json", "morceau_test.wav", "-o", "morceau_corrige.wav")
check("apply: exécution OK", r.returncode == 0, r.stderr)
check("apply: fichier de sortie créé et non vide", os.path.exists("morceau_corrige.wav")
      and os.path.getsize("morceau_corrige.wav") > 1000)

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("11. BATCH-APPLY (arborescence récursive, unicode, annexes, reprise, erreurs, parallèle)")
print("=" * 70)

lib_in = os.path.join(WORKDIR, "bib_input")
lib_out = os.path.join(WORKDIR, "bib_output")
os.makedirs(os.path.join(lib_in, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !"), exist_ok=True)
os.makedirs(os.path.join(lib_in, "高橋諒", "album test"), exist_ok=True)

def gen_flac(relpath):
    full = os.path.join(lib_in, relpath)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                     "-ac", "2", full, "-loglevel", "error"], check=True)

gen_flac("鈴木雅之 feat. 鈴木愛理/DADDY ! DADDY ! DO !/01 piste.flac")
gen_flac("鈴木雅之 feat. 鈴木愛理/DADDY ! DADDY ! DO !/02 piste.flac")
gen_flac("高橋諒/album test/01 piste.flac")
open(os.path.join(lib_in, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "cover.jpg"), "w").close()
open(os.path.join(lib_in, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "02 piste.lrc"), "w").close()
with open(os.path.join(lib_in, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "03 corrompu.flac"), "w") as f:
    f.write("pas un flac valide")

r = run("batch-apply", "profile_std.json", "bib_input", "bib_output", "--dry-run")
check("batch-apply --dry-run: exécution OK", r.returncode == 0, r.stderr)
check("batch-apply --dry-run: compte correct (4 audio dont 1 invalide compté, 2 annexes)",
      "4 fichier(s) audio" in r.stdout and "2 fichier(s) annexe(s)" in r.stdout, r.stdout)

r = run("batch-apply", "profile_std.json", "bib_input", "bib_output")
check("batch-apply: exécution OK malgré un fichier corrompu", r.returncode == 0, r.stderr)
check("batch-apply: structure unicode préservée",
      os.path.exists(os.path.join(lib_out, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "01 piste.flac")))
check("batch-apply: pochette copiée",
      os.path.exists(os.path.join(lib_out, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "cover.jpg")))
check("batch-apply: .lrc copié",
      os.path.exists(os.path.join(lib_out, "鈴木雅之 feat. 鈴木愛理", "DADDY ! DADDY ! DO !", "02 piste.lrc")))
check("batch-apply: fichier corrompu signalé en échec, pas de crash global", "1 échec" in r.stdout)
check("batch-apply: log d'erreur créé", os.path.exists(os.path.join(lib_out, "_erreurs_batch.log")))
check("batch-apply: autres pistes traitées malgré l'échec d'une seule",
      os.path.exists(os.path.join(lib_out, "高橋諒", "album test", "01 piste.flac")))

# Reprise: relancer sans --overwrite doit tout ignorer (sauf le corrompu qui a échoué)
r = run("batch-apply", "profile_std.json", "bib_input", "bib_output")
check("batch-apply: reprise - fichiers déjà présents ignorés", "0 piste(s) corrigée(s)" in r.stdout or
      "corrigée(s)" in r.stdout and "3 déjà présente" in r.stdout, r.stdout)

# Parallélisme
shutil.rmtree(os.path.join(WORKDIR, "bib_output_par"), ignore_errors=True)
r = run("batch-apply", "profile_std.json", "bib_input", "bib_output_par", "--jobs", "4")
check("batch-apply --jobs 4: exécution OK", r.returncode == 0, r.stderr)
check("batch-apply --jobs 4: résultat cohérent (3 corrigées, 1 échec)",
      "3 piste(s) corrigée(s)" in r.stdout and "1 échec" in r.stdout, r.stdout)

# --skip-others
shutil.rmtree(os.path.join(WORKDIR, "bib_output_noothers"), ignore_errors=True)
r = run("batch-apply", "profile_std.json", "bib_input", "bib_output_noothers", "--skip-others")
check("batch-apply --skip-others: pochette NON copiée",
      not os.path.exists(os.path.join(WORKDIR, "bib_output_noothers", "鈴木雅之 feat. 鈴木愛理",
                                       "DADDY ! DADDY ! DO !", "cover.jpg")))

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("12. MPV-COMMAND")
print("=" * 70)

r = run("mpv-command", "profile_std.json", "--file", "morceau.flac")
check("mpv-command: exécution OK", r.returncode == 0, r.stderr)
check("mpv-command: contient --af=lavfi=", '--af="lavfi=[' in r.stdout)
check("mpv-command: fichier cible présent dans la commande", '"morceau.flac"' in r.stdout)

r = run("mpv-command", "profile_std.json", "--conf")
check("mpv-command --conf: format mpv.conf correct", r.stdout.strip().startswith("af=lavfi=["))

r = run("mpv-command", "profile_std.json", "-o", "lecture.sh")
check("mpv-command -o: script créé", os.path.exists("lecture.sh"))
check("mpv-command -o: script exécutable", os.access("lecture.sh", os.X_OK))

# Validation finale: le graphe généré est un graphe libavfilter valide
# (même parseur que le pont lavfi de mpv), via ffmpeg -af
with open("profile_std.json") as f:
    p = json.load(f)
sys.path.insert(0, os.path.dirname(TOOLKIT))
import importlib.util
spec = importlib.util.spec_from_file_location("toolkit", TOOLKIT)
toolkit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolkit)
graph = toolkit.build_lavfi_graph(p)
r_ff = subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-af", graph, "-f", "null", "-"], capture_output=True, text=True)
check("mpv-command: graphe généré valide pour libavfilter (donc pour mpv)", r_ff.returncode == 0,
      r_ff.stderr[-300:])

# ----------------------------------------------------------------------------
print()
print("=" * 70)
print("RÉSUMÉ")
print("=" * 70)
n_pass = sum(1 for _, s, _ in results if s == "PASS")
n_fail = sum(1 for _, s, _ in results if s == "FAIL")
print(f"Total: {len(results)} tests — {n_pass} PASS, {n_fail} FAIL")
if n_fail:
    print("\nÉchecs:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
sys.exit(1 if n_fail else 0)
