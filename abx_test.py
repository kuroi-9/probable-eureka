#!/usr/bin/env python3
"""
abx_test.py
============
Génère et note un test d'écoute en aveugle (ABX) pour vérifier objectivement
si une différence entre deux fichiers audio (ex: original vs corrigé par
dac_correction_toolkit.py) est réellement perceptible, plutôt que de se fier
à l'impression subjective en sachant à l'avance quel fichier est lequel
(effet d'attente / auto-persuasion, très documenté en écoute audio).

PRINCIPE
--------
Pour chaque essai, les deux fichiers sont copiés sous des noms neutres
("1" et "2"), dans un ORDRE TIRÉ AU HASARD, différent à chaque essai. La
correspondance réelle (quel numéro = quel fichier) est écrite dans un
fichier séparé, hors du dossier d'écoute, que tu ne dois PAS ouvrir avant
d'avoir noté tes réponses. Tu écoutes chaque essai, notes lequel des deux tu
perçois comme "corrigé" (ou "préféré", selon ce que tu testes), puis
`score` compare tes réponses à la vraie correspondance et calcule si ton
taux de réussite dépasse ce qu'on obtiendrait par pur hasard (test
statistique binomial).

Avec seulement quelques essais, même une vraie perception à 100% de réussite
n'est pas forcément statistiquement significative (un score parfait sur 3
essais arrive par hasard 1 fois sur 8). Plus il y a d'essais, plus la
conclusion est fiable — 10 essais minimum est un bon point de départ, 20
donne une conclusion plus solide.

WORKFLOW
--------
1) Créer le test (un seul couple de fichiers, répété plusieurs fois pour
   avoir assez d'essais à analyser statistiquement) :
       python3 abx_test.py create --original morceau.flac --corrige morceau_corrige.flac \\
           --trials 12 -o test_abx/

   Ou avec plusieurs morceaux différents (un essai par paire, via un CSV
   `original,corrige` un couple par ligne — utile pour tester sur plusieurs
   morceaux représentatifs plutôt que le même morceau répété) :
       python3 abx_test.py create --pairs-csv mes_paires.csv -o test_abx/

2) Écouter chaque essai dans test_abx/essais/ (fichiers nommés
   essai01_1.flac, essai01_2.flac, etc.) SANS ouvrir le fichier
   test_abx/cle_reponse.json. Note tes réponses dans
   test_abx/mes_reponses.txt (un template est généré automatiquement).

3) Noter le résultat :
       python3 abx_test.py score test_abx/
"""

import argparse
import csv
import json
import os
import random
import shutil
import secrets

from scipy import stats


# ----------------------------------------------------------------------------
# create
# ----------------------------------------------------------------------------

def _copy_pair_as_trial(original_path, corrige_path, trial_dir, trial_label, rng):
    """Copie les deux fichiers d'un essai sous des noms neutres (1/2), dans
    un ordre tiré au hasard. Retourne le mapping {"1": "original"|"corrige", "2": ...}.
    Copie brute (shutil.copy2), aucun ré-encodage, aucune perte de qualité."""
    ext_orig = os.path.splitext(original_path)[1]
    ext_corr = os.path.splitext(corrige_path)[1]
    os.makedirs(trial_dir, exist_ok=True)

    order = ["original", "corrige"]
    rng.shuffle(order)  # ordre aléatoire indépendant à chaque essai

    mapping = {}
    for slot, which in zip(["1", "2"], order):
        src = original_path if which == "original" else corrige_path
        ext = ext_orig if which == "original" else ext_corr
        dst = os.path.join(trial_dir, f"{trial_label}_{slot}{ext}")
        shutil.copy2(src, dst)
        mapping[slot] = which
    return mapping


def cmd_create(args):
    rng = random.Random(secrets.randbits(64))  # graine imprévisible, pas de biais
    out_dir = args.output
    trials_dir = os.path.join(out_dir, "essais")
    os.makedirs(trials_dir, exist_ok=True)

    answer_key = {}

    if args.pairs_csv:
        with open(args.pairs_csv) as f:
            reader = csv.DictReader(f)
            pairs = [(row["original"], row["corrige"]) for row in reader]
        if not pairs:
            raise SystemExit("Le CSV --pairs-csv ne contient aucune ligne valide (colonnes attendues: original,corrige)")
        for i, (orig, corr) in enumerate(pairs, start=1):
            label = f"essai{i:02d}"
            mapping = _copy_pair_as_trial(orig, corr, trials_dir, label, rng)
            answer_key[label] = {"mapping": mapping, "original_file": orig, "corrige_file": corr}
        n_trials = len(pairs)
    else:
        if not args.original or not args.corrige:
            raise SystemExit("Précise soit --pairs-csv, soit --original ET --corrige.")
        for i in range(1, args.trials + 1):
            label = f"essai{i:02d}"
            mapping = _copy_pair_as_trial(args.original, args.corrige, trials_dir, label, rng)
            answer_key[label] = {"mapping": mapping, "original_file": args.original, "corrige_file": args.corrige}
        n_trials = args.trials

    # La clé de réponse est écrite HORS du dossier d'essais, pour réduire le
    # risque de l'ouvrir par mégarde en parcourant les fichiers audio.
    answer_key_path = os.path.join(out_dir, "cle_reponse.json")
    with open(answer_key_path, "w") as f:
        json.dump(answer_key, f, indent=2)

    # Template de réponses à remplir par l'auditeur.
    responses_path = os.path.join(out_dir, "mes_reponses.txt")
    with open(responses_path, "w") as f:
        f.write("# Pour chaque essai, écris 1 ou 2 : celui que tu penses être la version CORRIGÉE.\n")
        f.write("# Ne consulte cle_reponse.json qu'APRES avoir rempli ce fichier.\n")
        f.write("# Format: essai01=1  (un par ligne)\n\n")
        for label in answer_key:
            f.write(f"{label}=\n")

    print(f"Test ABX créé: {n_trials} essai(s) dans {trials_dir}")
    print(f"Réponses à remplir dans: {responses_path}")
    print(f"Clé de réponse (À NE PAS OUVRIR AVANT D'AVOIR REPONDU): {answer_key_path}")
    print()
    print("Pour chaque essai, écoute les deux fichiers (ex: essai01_1.flac et")
    print("essai01_2.flac) et note dans mes_reponses.txt lequel des deux tu")
    print("penses être la version corrigée. Puis lance `score`.")


# ----------------------------------------------------------------------------
# score
# ----------------------------------------------------------------------------

def cmd_score(args):
    test_dir = args.test_dir
    answer_key_path = os.path.join(test_dir, "cle_reponse.json")
    responses_path = os.path.join(test_dir, "mes_reponses.txt")

    with open(answer_key_path) as f:
        answer_key = json.load(f)

    responses = {}
    with open(responses_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            label, val = line.split("=", 1)
            val = val.strip()
            if val:
                responses[label.strip()] = val

    missing = [label for label in answer_key if label not in responses]
    if missing:
        print(f"Attention: {len(missing)} essai(s) sans réponse ({', '.join(missing)}), ignorés dans le score.")

    correct = 0
    total = 0
    details = []
    for label, entry in answer_key.items():
        if label not in responses:
            continue
        guess = responses[label]
        true_slot_for_corrige = [slot for slot, which in entry["mapping"].items() if which == "corrige"][0]
        is_correct = (guess == true_slot_for_corrige)
        correct += int(is_correct)
        total += 1
        details.append((label, guess, true_slot_for_corrige, is_correct))

    if total == 0:
        raise SystemExit("Aucune réponse trouvée dans mes_reponses.txt.")

    print(f"{'Essai':<10} {'Ta réponse':<12} {'Vraie corrigée':<16} {'Résultat'}")
    for label, guess, truth, ok in details:
        print(f"{label:<10} {guess:<12} {truth:<16} {'✓' if ok else '✗'}")

    print()
    print(f"Score: {correct}/{total} correct ({100*correct/total:.1f}%)")

    # Test binomial: sous l'hypothèse nulle "pas de vraie perception", chaque
    # réponse est un coup de pile ou face (p=0.5). On calcule la probabilité
    # d'obtenir un score au moins aussi extrême par pur hasard.
    result = stats.binomtest(correct, total, p=0.5, alternative="greater")
    p_value = result.pvalue
    print(f"P-value (test binomial, H0: réponses aléatoires): {p_value:.4f}")

    if p_value < 0.05:
        print("=> Résultat statistiquement significatif (p<0.05): la différence semble bien perceptible.")
    elif p_value < 0.1:
        print("=> Résultat à la limite (0.05<p<0.1): tendance possible mais pas concluant, plus d'essais aideraient.")
    else:
        print("=> Pas de résultat statistiquement significatif: rien ne distingue ce score du pur hasard.")
        print("   (Ça ne prouve pas l'absence de différence, juste que ce test ne l'a pas détectée.)")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("create", help="Génère un test ABX en aveugle")
    sp.add_argument("--original", help="Fichier original (pour un couple unique répété --trials fois)")
    sp.add_argument("--corrige", help="Fichier corrigé (pour un couple unique répété --trials fois)")
    sp.add_argument("--trials", type=int, default=10, help="Nombre de répétitions du couple unique (défaut: 10)")
    sp.add_argument("--pairs-csv", help="CSV (colonnes: original,corrige) pour tester plusieurs morceaux, "
                                        "un essai par ligne, plutôt qu'un couple unique répété")
    sp.add_argument("-o", "--output", required=True, help="Dossier de sortie du test")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("score", help="Note un test ABX rempli")
    sp.add_argument("test_dir", help="Dossier du test (contenant cle_reponse.json et mes_reponses.txt)")
    sp.set_defaults(func=cmd_score)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
