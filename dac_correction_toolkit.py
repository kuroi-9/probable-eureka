#!/usr/bin/env python3
"""
dac_correction_toolkit.py
==========================
Boîte à outils pour mesurer un déséquilibre de gain L/R dépendant de la
fréquence sur une chaîne audio (baladeur, DAC, ampli casque...), isoler ce
défaut de la coloration de la carte son utilisée pour la mesure, et
générer/appliquer un profil de correction EQ sur des fichiers musicaux.

Voir GUIDE.md (à côté de ce fichier) pour une explication pas à pas complète,
le matériel nécessaire, la théorie derrière chaque étape, et un guide de
dépannage. Ce docstring ne couvre que l'aide rapide en ligne de commande.

Dépendances :
    pip install numpy scipy --break-system-packages
    ffmpeg doit être installé et dans le PATH (utilisé pour la conversion de
    formats audio non-WAV, et pour l'application finale du filtre EQ).
    Aucune dépendance audio externe (pas de soundfile/libsndfile) : la lecture
    /écriture WAV passe uniquement par scipy.io.wavfile, tout le reste
    (FLAC, MP3...) est délégué à ffmpeg en sous-processus.

RAPPEL THÉORIQUE RAPIDE
------------------------
On modélise chaque canal de la chaîne comme un système linéaire invariant
dans le temps (LTI) caractérisé par sa fonction de transfert H(f) (gain
complexe par fréquence). Deux systèmes en cascade (ex: baladeur -> carte son)
ont des fonctions de transfert qui se MULTIPLIENT en complexe, donc qui
s'ADDITIONNENT en dB (car dB = 20*log10(magnitude)). C'est cette propriété
qui permet de séparer proprement plusieurs sources de déséquilibre par simple
addition/soustraction de courbes en dB (voir `diff` et `swap-diff` plus bas).

  - `measure`     : mesure H(f) = FFT(sortie)/FFT(entrée) pour un canal donné,
                    via l'estimateur H1 = Pxy/Pxx (cross-spectre / auto-spectre,
                    méthode de Welch), qui moyenne sur plusieurs fenêtres pour
                    réduire le bruit de mesure — bien plus stable qu'une simple
                    division de FFT sur un seul bloc.
  - `diff`        : ΔD_mesuré = ΔD_réel + ΔM_carte_son (les deux s'additionnent
                    en dB). Si on connaît ΔM_carte_son (mesuré séparément en
                    boucle), on le soustrait pour isoler ΔD_réel.
  - `swap-diff`   : méthode plus robuste, n'exige aucune calibration séparée.
                    En inversant le câblage entre deux mesures, ΔD change de
                    signe mais pas ΔM (propriété du montage physique inversé)
                    donc: diff1 = ΔD+ΔM, diff2 = -ΔD+ΔM => ΔD=(diff1-diff2)/2,
                    ΔM=(diff1+diff2)/2. Voir le docstring de `cmd_swap_diff`
                    pour la dérivation complète.
  - `mono-diff`   : méthode RECOMMANDÉE, encore plus simple. Deux mesures
                    MONO capturées via LA MÊME entrée physique de la carte
                    son (une par canal du baladeur, jamais les deux en même
                    temps) : gain_A=D_L+M, gain_B=D_R+M => ΔD=gain_B-gain_A,
                    M s'annule EXACTEMENT (même chemin physique, pas
                    d'algèbre à deux sessions nécessaire). Élimine aussi la
                    diaphonie entre voies de la carte son, que `swap-diff`
                    ne peut pas éliminer (les deux voies sont utilisées
                    simultanément dans `swap-diff`). Voir `cmd_mono_diff`.
  - `multivolume` : le gain d'un système purement résistif/linéaire donne un
                    ΔD constant en dB quel que soit le volume. Si ΔD varie
                    avec le volume, le défaut vient d'un mécanisme non-linéaire
                    (tracking du potentiomètre, non-linéarité du DAC près du
                    zéro, distorsion de croisement...).
  - `build-eq`    : construit un filtre EQ correctif à partir de la courbe de
                    défaut, avec lissage fractionnaire d'octave (pour ne pas
                    figer le bruit de mesure dans le filtre) et protection
                    anti-écrêtage (jamais de boost net, seulement de
                    l'atténuation relative).

DÉRIVE D'HORLOGE ENTRE LECTURE ET ENREGISTREMENT
--------------------------------------------------
Le baladeur (horloge de lecture) et la carte son (horloge d'enregistrement)
sont deux appareils indépendants, sans horloge partagée. Même de bonne
qualité, deux horloges dérivent l'une par rapport à l'autre de l'ordre de
quelques dizaines à quelques centaines de ppm — sur un sweep de 30s, ça
représente plusieurs ms de désynchronisation qui S'ACCUMULENT
progressivement (contrairement à un simple délai constant, sans impact sur
la mesure de gain puisque `measure` ne regarde que le module |H|, pas la
phase). Non corrigée, cette dérive dégrade la précision de la mesure,
surtout dans l'aigu. `measure`/`measure-mono` estiment cette dérive
(corrélation croisée locale en début et fin de signal) et la corrigent par
rééchantillonnage à bande limitée (`scipy.signal.resample_poly` — une
simple interpolation linéaire déformerait le contenu proche de la fréquence
de Nyquist). Voir `estimate_drift_ratio`/`correct_drift` pour le détail.

WORKFLOW COMPLET
-----------------
1) Générer le signal de test (sweep log 20Hz-20kHz, identique sur L et R) :
       python3 dac_correction_toolkit.py generate-sweep -o sweep.wav

2) CALIBRATION de la chaîne d'enregistrement (boucle sortie->entrée du même
   appareil, sans passer par le baladeur) — méthode simple mais moins
   rigoureuse que swap-diff (voir 5bis) :
       python3 dac_correction_toolkit.py measure sweep.wav loopback_rec.wav -o calib.csv

3) MESURE réelle à travers le baladeur (répéter à plusieurs volumes si tu
   veux vérifier la dépendance au volume, ex: vol25.wav, vol50.wav...) :
       python3 dac_correction_toolkit.py measure sweep.wav baladeur_vol70.wav -o mesure_vol70.csv

4) Isoler le vrai défaut du baladeur (soustrait le biais de la carte son) :
       python3 dac_correction_toolkit.py diff mesure_vol70.csv calib.csv -o defaut_reel.csv

5) (optionnel mais recommandé) Vérifier si le défaut dépend du volume, en
   répétant 3+4 à plusieurs volumes puis :
       python3 dac_correction_toolkit.py multivolume defaut_v25.csv defaut_v50.csv defaut_v70.csv defaut_v100.csv

5bis) MÉTHODE ALTERNATIVE (swap-diff) pour ignorer la coloration de la carte
   son sans calibration séparée (annule ΔM quelle que soit sa forme) :
   mesure deux fois avec le câblage de la carte son inversé.
       # Session normale: baladeur-L -> carte-L, baladeur-R -> carte-R
       python3 dac_correction_toolkit.py measure sweep.wav rec_normal.wav -o mesure_normale.csv
       # Session inversée: débranche et rebranche en inversant L/R du baladeur
       # vers la carte (adaptateur inverseur, ou change juste le câblage)
       python3 dac_correction_toolkit.py measure sweep.wav rec_inversee.wav -o mesure_inversee.csv
       python3 dac_correction_toolkit.py swap-diff mesure_normale.csv mesure_inversee.csv -o defaut_reel.csv

5ter) MÉTHODE RECOMMANDÉE (mono-diff) : encore plus simple et plus robuste
   que swap-diff (élimine aussi la diaphonie entre voies de la carte son).
   Isole au niveau du câble (adaptateur/breakout) un seul pôle de sortie du
   baladeur à la fois, capture en MONO (1 seul canal), toujours via LA MÊME
   entrée physique de la carte son entre les deux sessions.
       python3 dac_correction_toolkit.py generate-sweep -o sweep.wav   # sweep normal, PAS --mono-channel
       # Session A: isole le pôle L du baladeur au câble, capture en mono
       python3 dac_correction_toolkit.py measure-mono sweep.wav rec_L_mono.wav -o mesure_L.csv
       # Session B: isole le pôle R du baladeur au câble, MÊME entrée carte son
       python3 dac_correction_toolkit.py measure-mono sweep.wav rec_R_mono.wav -o mesure_R.csv
       python3 dac_correction_toolkit.py mono-diff mesure_L.csv mesure_R.csv -o defaut_reel.csv
   -> `defaut_reel.csv` obtenu par swap-diff OU mono-diff est déjà net de
   toute coloration de la carte son, utilisable directement dans build-eq.

6) Générer le profil de correction EQ (lissé, prêt pour ffmpeg) :
       python3 dac_correction_toolkit.py build-eq defaut_reel.csv -o profil_correction.json

7) Appliquer la correction à un fichier ou un dossier de musique :
       python3 dac_correction_toolkit.py apply profil_correction.json morceau.flac -o morceau_corrige.flac
       python3 dac_correction_toolkit.py batch-apply profil_correction.json ./musique/ ./musique_corrigee/
"""

import argparse
import concurrent.futures
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction

import numpy as np
from scipy.signal import chirp, correlate, csd, welch, resample_poly, butter, sosfilt


# ----------------------------------------------------------------------------
# Utilitaires audio
# ----------------------------------------------------------------------------

def load_audio(path):
    """Charge un fichier audio en float64, retourne (sr, data[n, ch]).

    WAV est géré nativement par scipy (aucune dépendance externe). Les autres
    formats (flac, mp3, m4a...) sont d'abord convertis en WAV temporaire via
    un sous-processus ffmpeg, puis le WAV temporaire est lu et supprimé.
    C'est volontairement simple: on ne veut pas dépendre de libsndfile/
    soundfile, qui n'est pas toujours installable (pas d'accès réseau, etc.),
    alors que ffmpeg est déjà une dépendance obligatoire du reste du pipeline.

    Les entiers PCM (16/24/32 bits) sont normalisés en float64 dans [-1, 1]
    en divisant par la valeur max du type entier d'origine ; les fichiers déjà
    en float sont simplement recastés. Un fichier mono est reshape en (n, 1)
    pour uniformiser l'interface avec le reste du code, qui suppose toujours
    un tableau 2D (échantillons x canaux).
    """
    from scipy.io import wavfile
    if not path.lower().endswith(".wav"):
        tmp_path = path + ".__tmp_convert__.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ac", "2", tmp_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        sr, data = wavfile.read(tmp_path)
        os.remove(tmp_path)
    else:
        sr, data = wavfile.read(path)
    if data.ndim == 1:
        data = data[:, None]
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / np.iinfo(data.dtype).max
    else:
        data = data.astype(np.float64)
    return sr, data


def save_audio(path, sr, data):
    """Sauvegarde en WAV 32-bit float (scipy uniquement). Pour un autre
    format de sortie (ex: .flac demandé explicitement), on écrit d'abord un
    WAV temporaire puis on convertit avec ffmpeg, comme pour load_audio."""
    from scipy.io import wavfile
    if not path.lower().endswith(".wav"):
        wav_path = os.path.splitext(path)[0] + ".wav"
        wavfile.write(wav_path, sr, data.astype(np.float32))
        subprocess.run(["ffmpeg", "-y", "-i", wav_path, path], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(wav_path)
    else:
        wavfile.write(path, sr, data.astype(np.float32))


def align(source, recording):
    """Aligne temporellement l'enregistrement sur la source par corrélation
    croisée (nécessaire car il y a toujours une latence variable entre le
    moment où le sweep est joué et le moment où l'enregistrement commence
    réellement à capter le signal — buffers audio, latence USB, etc.).

    On calcule la corrélation croisée entre la moyenne des canaux de la
    source et celle de l'enregistrement (la moyenne suffit pour trouver le
    délai global, pas besoin de le faire par canal). Le pic de corrélation
    indique le décalage (lag) en échantillons à appliquer. Si lag >= 0,
    l'enregistrement a commencé après la source (cas normal, latence de
    capture) donc on tronque le début de l'enregistrement ; sinon (rare,
    pourrait arriver avec un enregistrement démarré en avance) on tronque
    le début de la source à la place.

    Les deux signaux sont ensuite tronqués à la même longueur pour permettre
    une comparaison échantillon par échantillon dans les étapes suivantes.

    NOTE : cet alignement est un simple décalage GLOBAL (constant). Il ne
    corrige PAS une dérive d'horloge progressive entre les deux appareils
    (voir `estimate_drift_ratio` / `correct_drift` juste après, qui
    s'occupent spécifiquement de ça).
    """
    s = source.mean(axis=1)
    r = recording.mean(axis=1)
    # limite la taille pour la corrélation si les fichiers sont longs (perf) :
    # une corrélation croisée complète est en O(n log n) via FFT mais reste
    # coûteuse en mémoire pour des enregistrements de plusieurs minutes.
    max_len = min(len(s), len(r), sr_limit_samples)
    corr = correlate(r[:max_len], s[:max_len], mode="full")
    lag = corr.argmax() - (max_len - 1)
    if lag >= 0:
        recording = recording[lag:]
    else:
        source = source[-lag:]
    n = min(len(source), len(recording))
    return source[:n], recording[:n]


sr_limit_samples = 5_000_000  # cap pour la corrélation croisée (perf), ~100s à 48kHz


# ----------------------------------------------------------------------------
# Correction de dérive d'horloge entre lecture et enregistrement
# ----------------------------------------------------------------------------
#
# PROBLÈME : le baladeur (horloge de lecture) et la carte son (horloge
# d'enregistrement) sont deux appareils totalement indépendants, sans horloge
# partagée ("word clock"). Même des horloges de bonne qualité ont une
# précision typique de quelques dizaines à quelques centaines de ppm
# (parties par million). Sur un sweep de 30 secondes, une dérive de 500ppm
# représente déjà ~15ms de décalage accumulé en fin de signal — largement
# assez pour dégrader significativement une mesure de gain par fréquence,
# SURTOUT dans l'aigu (une même dérive temporelle correspond à une erreur de
# fréquence plus grande là où le sweep balaie vite, c'est-à-dire en haut du
# spectre pour un sweep logarithmique).
#
# Ce n'est PAS le même problème qu'un simple délai constant : un délai
# constant n'affecte que la phase (donc n'a aucun impact sur notre mesure de
# gain, qui ne regarde que le module |H|). La DÉRIVE, elle, s'accumule
# progressivement dans le temps et désynchronise de plus en plus le contenu
# réel du sweep par rapport à ce que l'estimateur H1 croit être en train de
# comparer — ça, ça affecte bien le gain mesuré, pas seulement la phase.
#
# MÉTHODE : on mesure le décalage local (par corrélation croisée sur une
# petite fenêtre) en début et en fin d'enregistrement. La différence entre
# ces deux décalages, rapportée à la distance qui les sépare, donne le taux
# de dérive (en échantillons par échantillon, converti en ppm pour lecture).
# On corrige ensuite en RÉÉCHANTILLONNANT l'enregistrement pour compenser
# cette dérive.
#
# IMPORTANT sur la méthode de rééchantillonnage : une simple interpolation
# linéaire (np.interp) NE CONVIENT PAS ICI. Le sweep contient du contenu
# proche de la fréquence de Nyquist (à 18-20kHz avec un échantillonnage à
# 48kHz, il n'y a que 2 à 3 échantillons par cycle) ; une interpolation
# linéaire déforme et atténue fortement ce contenu haute fréquence. Il faut
# un rééchantillonnage à bande limitée (`scipy.signal.resample_poly`, qui
# applique un filtre anti-repliement correctement dimensionné), comme celui
# utilisé par n'importe quel bon convertisseur de fréquence d'échantillonnage.

def estimate_drift_ratio(source, recording, sr, seg_frac=0.15, search_sec=0.05):
    """Estime le taux de dérive d'horloge entre source et recording (déjà
    grossièrement alignés par `align()`), en comparant le décalage local en
    début et en fin de signal.

    On prend un segment proche du début (après avoir sauté les tout premiers
    5% pour éviter les artefacts de fondu/bord) et un segment proche de la
    fin, chacun de taille `seg_frac` de la longueur totale. Pour chacun, une
    corrélation croisée LOCALE (recherche limitée à `search_sec` secondes
    autour de la position attendue, donc rapide) donne le décalage résiduel
    à cet endroit précis du signal.

    Le taux de dérive est la pente : (décalage_fin - décalage_début) /
    (distance entre les deux centres), une grandeur sans unité (échantillons
    par échantillon), directement interprétable en ppm en multipliant par 1e6.

    Retourne ce taux (float, proche de 0 si pas de dérive significative).
    """
    n = min(len(source), len(recording))
    source, recording = source[:n], recording[:n]
    seg_len = int(n * seg_frac)
    search_radius = int(search_sec * sr)

    def local_lag(pos):
        s_seg = source[pos:pos + seg_len]
        r_start = max(0, pos - search_radius)
        r_end = min(n, pos + seg_len + search_radius)
        r_seg = recording[r_start:r_end]
        corr = correlate(r_seg, s_seg, mode="valid")
        best = corr.argmax()
        return (r_start + best) - pos

    pos_early = int(n * 0.05)
    pos_late = n - seg_len - int(n * 0.05)
    lag_early = local_lag(pos_early)
    lag_late = local_lag(pos_late)
    center_early = pos_early + seg_len / 2
    center_late = pos_late + seg_len / 2
    return (lag_late - lag_early) / (center_late - center_early)


def correct_drift(recording, ratio, max_denom=200_000):
    """Corrige la dérive estimée par `estimate_drift_ratio` en rééchantillonnant
    `recording` via `scipy.signal.resample_poly` (rééchantillonnage à bande
    limitée, avec filtre anti-repliement — voir note plus haut sur pourquoi
    une interpolation linéaire naïve serait incorrecte ici).

    Le ratio de rééchantillonnage exact (1/(1+ratio)) est approché par une
    fraction rationnelle (numérateur/dénominateur entiers, dénominateur
    limité à `max_denom` pour garder un temps de calcul raisonnable — une
    précision de l'ordre de 1/max_denom, largement suffisante face à
    l'incertitude déjà présente dans l'estimation elle-même).

    Si la dérive estimée est négligeable (<0.1ppm), retourne le signal
    inchangé (évite un rééchantillonnage inutile).
    """
    if abs(ratio) < 1e-7:
        return recording
    frac = Fraction(1 / (1 + ratio)).limit_denominator(max_denom)
    return resample_poly(recording, frac.numerator, frac.denominator)


def align_with_drift_correction(source, recording, sr, verbose=True):
    """Pipeline complet: alignement grossier (délai constant) puis correction
    de dérive d'horloge. C'est cette fonction, plutôt que `align()` seule,
    qui doit être utilisée par toutes les commandes de mesure."""
    source, recording = align(source, recording)
    # la dérive est estimée sur la moyenne des canaux (suffisant, la dérive
    # d'horloge affecte les deux canaux de la même façon puisqu'ils partagent
    # le même convertisseur/horloge de chaque côté)
    ratio = estimate_drift_ratio(source.mean(axis=1), recording.mean(axis=1), sr)
    if verbose:
        print(f"  Dérive d'horloge estimée: {ratio*1e6:+.1f} ppm")
    if abs(ratio) < 1e-7:
        return source, recording
    corrected_channels = [
        correct_drift(recording[:, ch], ratio) for ch in range(recording.shape[1])
    ]
    n = min(len(source), min(len(c) for c in corrected_channels))
    recording_corrected = np.column_stack([c[:n] for c in corrected_channels])
    return source[:n], recording_corrected


# ----------------------------------------------------------------------------
# generate-sweep
# ----------------------------------------------------------------------------

def cmd_generate_sweep(args):
    """Génère un sweep (balayage) sinusoïdal logarithmique, identique sur les
    deux canaux, utilisé comme signal de test pour toutes les mesures.

    Pourquoi un sweep plutôt que de la musique ? Un morceau de musique normal
    n'a pas une énergie uniforme sur tout le spectre (peu de contenu à 15kHz
    par exemple), donc l'estimation du gain serait très bruitée aux
    fréquences peu présentes. Un sweep balaie toutes les fréquences avec une
    énergie contrôlée, ce qui donne une mesure fiable partout.

    Pourquoi logarithmique plutôt que linéaire ? Un balayage logarithmique
    passe un temps égal par octave plutôt que par Hz, ce qui correspond à la
    perception humaine des fréquences (et donne un rapport signal/bruit plus
    homogène sur l'ensemble du spectre audible, notamment dans le grave où un
    balayage linéaire passerait très peu de temps).

    Un fondu (fade in/out) de 20ms est appliqué au début et à la fin pour
    éviter les clics/craquements liés à une transition brutale de silence à
    signal (discontinuité qui génère elle-même du contenu haute fréquence
    parasite si elle n'est pas adoucie).

    L'amplitude par défaut (0.5, soit -6dBFS) laisse de la marge (headroom)
    pour absorber d'éventuelles surtensions du DAC en sortie sans écrêter.

    MARGE DE SÉCURITÉ AUX BORDS (`--margin-ratio`, 15% par défaut) : le
    sweep est en réalité généré un peu plus large que [f_min, f_max] (par
    défaut, environ 15% d'octave de marge de chaque côté en échelle log).
    Ceci est nécessaire car l'estimation de gain devient franchement peu
    fiable tout au bord des limites d'un sweep (dans le dernier ~1-2% de sa
    bande) : le signal manque de cycles complets pile à la fréquence limite,
    ce qui peut produire des valeurs aberrantes (parfois plusieurs dizaines
    de dB d'erreur ponctuelle). Mets `--margin-ratio 0` pour désactiver ce
    comportement (déconseillé).

    FICHIER DE MÉTADONNÉES (`<output>.meta.json`) : pour éviter tout risque
    d'oubli/désynchronisation entre la bande générée ici et celle utilisée
    par `measure`/`measure-mono` (qui ont LEURS PROPRES arguments
    `--f-min`/`--f-max`, séparés de ceux-ci), cette commande écrit
    automatiquement un petit fichier JSON à côté du sweep, contenant la
    bande utile [f_min, f_max] réellement fiable. `measure`/`measure-mono`
    lisent ce fichier automatiquement quand il existe et l'utilisent comme
    valeur par défaut de LEURS `--f-min`/`--f-max` — tu n'as donc normalement
    RIEN à spécifier de ce côté-là, la bonne bande est propagée toute seule.
    Un `--f-min`/`--f-max` explicite passé à `measure`/`measure-mono`
    reste prioritaire sur les métadonnées si tu veux forcer une valeur.

    --mono-channel {L,R} : place le sweep uniquement sur le canal demandé et
    du silence sur l'autre, pour la méthode `mono-diff` (voir GUIDE.md) —
    dans ce cas, seul le canal baladeur actif traversera la carte son, ce qui
    permet de router systématiquement CE canal vers la même entrée physique
    de la carte son entre deux sessions, éliminant ainsi toute différence
    entre les deux entrées de la carte son (gain, phase, bruit, dérive
    d'horloge propre à chaque voie ADC...), sans avoir besoin d'aucune
    hypothèse ni d'aucun calcul de séparation.
    """
    sr = args.samplerate
    margin = args.margin_ratio
    f_min_gen = args.f_min / (1 + margin)
    f_max_gen = args.f_max * (1 + margin)
    t = np.linspace(0, args.duration, int(sr * args.duration), endpoint=False)
    sig = chirp(t, f0=f_min_gen, f1=f_max_gen, t1=args.duration, method="logarithmic")
    # fade in/out pour éviter les clics
    fade_n = int(0.02 * sr)
    fade = np.linspace(0, 1, fade_n)
    sig[:fade_n] *= fade
    sig[-fade_n:] *= fade[::-1]
    sig *= args.amplitude  # headroom pour éviter l'écrêtage
    zeros = np.zeros_like(sig)
    if args.mono_channel == "L":
        stereo = np.column_stack([sig, zeros])
    elif args.mono_channel == "R":
        stereo = np.column_stack([zeros, sig])
    else:
        stereo = np.column_stack([sig, sig])
    save_audio(args.output, sr, stereo)
    meta = {
        "f_min": args.f_min, "f_max": args.f_max,          # bande utile/fiable
        "f_min_generated": f_min_gen, "f_max_generated": f_max_gen,  # bande réellement générée (avec marge)
        "margin_ratio": margin, "duration": args.duration, "samplerate": sr,
        "mono_channel": args.mono_channel,
    }
    meta_path = args.output + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    mono_info = f", mono sur {args.mono_channel} (silence sur l'autre)" if args.mono_channel else ""
    print(f"Sweep généré: {args.output} (bande utile {args.f_min}-{args.f_max} Hz, "
          f"généré en réalité {f_min_gen:.1f}-{f_max_gen:.1f} Hz avec marge {margin*100:.0f}%, "
          f"{args.duration}s, {sr} Hz, amplitude {args.amplitude}{mono_info})")
    print(f"  Métadonnées écrites dans: {meta_path} "
          f"(measure/measure-mono liront automatiquement --f-min {args.f_min} --f-max {args.f_max})")


# ----------------------------------------------------------------------------
# generate-noise
# ----------------------------------------------------------------------------

def cmd_generate_noise(args):
    """Génère du bruit blanc filtré (bande limitée à [f_min, f_max] avec la
    même logique de marge que `generate-sweep`), identique sur les deux
    canaux, utilisable comme signal de test ALTERNATIF au sweep.

    QUAND PRÉFÉRER LE BRUIT AU SWEEP (et quand ça ne change rien)
    -------------------------------------------------------------------
    Pour un système linéaire (l'hypothèse de travail habituelle pour un DAC/
    ampli casque, tant qu'on reste loin de la saturation), la réponse en
    fréquence mesurée par ce toolkit (l'estimateur H1) est THÉORIQUEMENT
    IDENTIQUE qu'on utilise un sweep ou du bruit blanc — principe de
    superposition, chaque fréquence est traitée indépendamment des autres,
    qu'elles soient présentes séquentiellement (sweep) ou simultanément
    (bruit). Le bruit N'APPORTE PAS d'information supplémentaire sur un
    défaut linéaire de gain L/R comme celui que ce toolkit mesure.

    Ce que le bruit apporte réellement :
    - Pas d'artefact de bord de bande (pas de "fin de sweep" séquentielle),
      donc pas besoin de la marge de sécurité de `generate-sweep`... même si
      elle est quand même appliquée ici par cohérence/simplicité, avec un
      filtre de bande qui a lui-même une pente de transition.
    - Un indicateur de qualité par fréquence bien plus informatif: la
      COHÉRENCE (voir `transfer_function_h1`), calculable de façon
      pertinente parce que le bruit sollicite chaque fréquence en
      permanence sur toute la durée de l'enregistrement.
    - En clair: un bruit blanc anormalement long PEUT détecter des choses
      qu'un sweep ne détecterait pas, mais UNIQUEMENT si le comportement du
      baladeur est NON-LINÉAIRE (distorsion, intermodulation) — et même
      dans ce cas, ce toolkit (mesure de gain linéaire uniquement) ne
      caractérise pas cette non-linéarité, il te dirait juste "cohérence
      basse ici" sans en dire la cause.

    Ce que le bruit coûte : pour une précision comparable à un sweep, il
    faut un enregistrement NETTEMENT plus long (l'énergie du bruit est
    étalée en permanence sur tout le spectre, alors que le sweep concentre
    toute son énergie sur chaque fréquence l'une après l'autre) — d'où la
    durée par défaut plus longue ici (60s) que pour `generate-sweep` (30s).
    """
    sr = args.samplerate
    margin = args.margin_ratio
    f_min_gen = args.f_min / (1 + margin)
    f_max_gen = min(args.f_max * (1 + margin), sr / 2 * 0.98)  # reste sous Nyquist
    n = int(sr * args.duration)
    rng = np.random.default_rng(args.seed)
    sig = rng.normal(0, 1, n)
    sos = butter(4, [f_min_gen, f_max_gen], btype="bandpass", fs=sr, output="sos")
    sig = sosfilt(sos, sig)
    sig /= np.abs(sig).max() + 1e-12
    # fade in/out pour éviter les clics
    fade_n = int(0.02 * sr)
    fade = np.linspace(0, 1, fade_n)
    sig[:fade_n] *= fade
    sig[-fade_n:] *= fade[::-1]
    sig *= args.amplitude  # headroom pour éviter l'écrêtage
    zeros = np.zeros_like(sig)
    if args.mono_channel == "L":
        stereo = np.column_stack([sig, zeros])
    elif args.mono_channel == "R":
        stereo = np.column_stack([zeros, sig])
    else:
        stereo = np.column_stack([sig, sig])
    save_audio(args.output, sr, stereo)
    meta = {
        "f_min": args.f_min, "f_max": args.f_max,
        "f_min_generated": f_min_gen, "f_max_generated": f_max_gen,
        "margin_ratio": margin, "duration": args.duration, "samplerate": sr,
        "mono_channel": args.mono_channel, "signal_type": "white_noise", "seed": args.seed,
    }
    meta_path = args.output + ".meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    mono_info = f", mono sur {args.mono_channel} (silence sur l'autre)" if args.mono_channel else ""
    print(f"Bruit blanc généré: {args.output} (bande utile {args.f_min}-{args.f_max} Hz, "
          f"filtré {f_min_gen:.1f}-{f_max_gen:.1f} Hz avec marge {margin*100:.0f}%, "
          f"{args.duration}s, {sr} Hz, amplitude {args.amplitude}, seed={args.seed}{mono_info})")
    print(f"  Métadonnées écrites dans: {meta_path} "
          f"(measure/measure-mono liront automatiquement --f-min {args.f_min} --f-max {args.f_max})")
    print(f"  Rappel: {args.duration}s de bruit ≈ précision d'un sweep BEAUCOUP plus court "
          f"(voir docstring de cette commande) — regarde la colonne `coherence` du CSV de mesure "
          f"pour juger si c'est suffisant.")


# ----------------------------------------------------------------------------
# Résolution automatique de f-min/f-max via le fichier de métadonnées
# écrit par `generate-sweep` (voir sa docstring) — évite tout risque de
# désynchronisation entre la bande générée et la bande analysée par
# `measure`/`measure-mono`.
# ----------------------------------------------------------------------------

def resolve_f_range(source_path, f_min_arg, f_max_arg):
    """Détermine les bornes f_min/f_max à utiliser pour l'analyse :
    1. Si `f_min_arg`/`f_max_arg` sont explicitement fournis (non None, donc
       l'utilisateur a passé --f-min/--f-max sur la ligne de commande), ils
       sont prioritaires et utilisés tels quels.
    2. Sinon, cherche un fichier `<source_path>.meta.json` (écrit
       automatiquement par `generate-sweep`) et utilise la bande utile qu'il
       contient — c'est le cas normal si le sweep a été généré par ce
       toolkit et n'a pas été renommé/déplacé séparément de son .meta.json.
    3. Sinon (pas de métadonnées trouvées, ex: fichier source externe ou
       renommé), retombe sur 20-20000 Hz avec un avertissement, puisqu'on
       ne peut alors pas savoir quelle marge de sécurité a été utilisée à
       la génération (le cas échéant).

    Retourne (f_min, f_max, source_description) où source_description est
    une courte chaîne indiquant d'où viennent ces valeurs (pour affichage).
    """
    if f_min_arg is not None and f_max_arg is not None:
        return f_min_arg, f_max_arg, "valeurs explicites (--f-min/--f-max)"

    meta_path = source_path + ".meta.json"
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        f_min = f_min_arg if f_min_arg is not None else meta["f_min"]
        f_max = f_max_arg if f_max_arg is not None else meta["f_max"]
        return f_min, f_max, f"métadonnées de {os.path.basename(meta_path)}"

    f_min = f_min_arg if f_min_arg is not None else 20.0
    f_max = f_max_arg if f_max_arg is not None else 20000.0
    return f_min, f_max, "défaut 20-20000 Hz (aucune métadonnée trouvée pour cette source — " \
                          "si ce sweep a été généré avec des bornes différentes ou une marge " \
                          "personnalisée, précise --f-min/--f-max explicitement)"


# ----------------------------------------------------------------------------
# measure : fonction de transfert par canal (estimateur H1, méthode de Welch)
# ----------------------------------------------------------------------------

def transfer_function_h1(src_ch, rec_ch, sr, nperseg=32768):
    """Estime la fonction de transfert H(f) entre le signal source (entrée
    théorique x) et le signal enregistré (sortie mesurée y), avec
    l'estimateur H1 = Pxy(f) / Pxx(f), ET la cohérence Cxy(f) associée.

    Pxy est le cross-spectre (densité spectrale croisée) entre y et x, Pxx
    est l'auto-spectre (densité spectrale de puissance) de x seul, tous deux
    calculés par la méthode de Welch (moyenne de plusieurs segments
    recouvrants, fenêtrés). C'est l'estimateur standard en analyse de
    systèmes LTI (utilisé par exemple par toute analyse de fonction de
    transfert en électronique/mécanique) : il est nettement plus robuste au
    bruit qu'une simple division de FFT sur un seul bloc, car le moyennage
    sur plusieurs segments réduit la variance de l'estimation sans biaiser
    le résultat sur un signal source parfaitement connu comme un sweep.

    nperseg contrôle le compromis résolution fréquentielle / robustesse au
    bruit : une valeur plus grande donne plus de résolution en fréquence
    (plus de points distincts dans le grave) mais moyenne sur moins de
    segments (donc plus sensible au bruit). 32768 échantillons à 48kHz
    donne une résolution d'environ 1.5 Hz, largement suffisante ici.

    COHÉRENCE (Cxy = |Pxy|² / (Pxx·Pyy), entre 0 et 1) : indicateur de
    qualité de la mesure à chaque fréquence, qui répond à la question "à
    quel point la sortie enregistrée est-elle expliquée LINÉAIREMENT par la
    source à cette fréquence ?". Une cohérence proche de 1 signifie une
    mesure fiable. Une cohérence basse à une fréquence donnée peut venir de:
    bruit de fond trop élevé par rapport au signal à cette fréquence,
    non-linéarité (distorsion) du système mesuré, ou fuite spectrale
    (signal mal aligné/désynchronisé). Avec un sweep, la cohérence est
    généralement proche de 1 partout où le sweep passe suffisamment de
    temps (l'énergie du signal de test y est concentrée), MAIS ce n'est pas
    un indicateur très informatif dans ce cas puisque le sweep ne passe
    qu'une fois, brièvement, à chaque fréquence. Avec un bruit blanc
    (`generate-noise`), la cohérence devient un indicateur BEAUCOUP plus
    utile : le signal de test est présent à toutes les fréquences en
    permanence pendant toute la durée de l'enregistrement, donc une
    cohérence basse à une fréquence donnée signale un vrai problème de
    mesure à cette fréquence précise (et pas juste "on n'a pas eu le temps
    de bien mesurer cette fréquence-là").
    """
    n = min(len(src_ch), len(rec_ch))
    src_ch, rec_ch = src_ch[:n], rec_ch[:n]
    f, Pxy = csd(rec_ch, src_ch, fs=sr, nperseg=nperseg)
    _, Pxx = welch(src_ch, fs=sr, nperseg=nperseg)
    _, Pyy = welch(rec_ch, fs=sr, nperseg=nperseg)
    Pxx[Pxx == 0] = 1e-20
    H = Pxy / Pxx
    denom = Pxx * Pyy
    denom[denom == 0] = 1e-30
    coherence = np.clip(np.abs(Pxy) ** 2 / denom, 0, 1)
    return f, H, coherence



def cmd_measure(args):
    """Calcule et exporte en CSV le gain (en dB) de chaque canal (L et R)
    entre le fichier source et l'enregistrement correspondant, ainsi que
    leur différence (diff_R_moins_L_dB = gain_R - gain_L).

    Cette différence est la grandeur centrale de tout le pipeline : c'est
    elle qui représente le déséquilibre L/R par fréquence, avant toute
    séparation entre défaut du baladeur et coloration de la carte son
    (cette séparation se fait ensuite avec `diff`, `swap-diff` ou
    `mono-diff`).

    NOTE IMPORTANTE: cette commande sert aussi bien à mesurer le baladeur
    qu'à mesurer une boucle de calibration (sortie->entrée du même appareil),
    une session normale/inversée pour `swap-diff`, ou une session mono pour
    `mono-diff` — c'est le même calcul dans tous les cas, seule
    l'interprétation de son résultat change selon l'étape du pipeline.

    CORRECTION DE DÉRIVE D'HORLOGE (activée par défaut, voir
    `estimate_drift_ratio`/`correct_drift`/`align_with_drift_correction`) :
    le baladeur et la carte son ont des horloges indépendantes, donc la
    fréquence réellement enregistrée peut très légèrement différer de la
    fréquence nominale émise (dérive typique: quelques dizaines à quelques
    centaines de ppm). Non corrigée, cette dérive dégrade la précision de la
    mesure — particulièrement dans l'aigu, et particulièrement pour les
    sweeps longs (voir docstring de `estimate_drift_ratio` pour le détail).
    Désactivable avec --no-drift-correction si besoin (ex: debug).
    Cette dérive étant commune aux deux canaux d'un même enregistrement
    stéréo (même horloge ADC pour L et R), elle n'introduit PAS de biais
    dans le déséquilibre L/R mesuré même sans cette correction — elle
    améliore la précision générale de la mesure de gain absolu, elle n'est
    pas strictement requise pour la validité du calcul de différence L/R
    en tant que tel.
    RÉSOLUTION AUTOMATIQUE DE --f-min/--f-max (voir `resolve_f_range`) : si
    tu ne précises pas ces deux options, elles sont lues automatiquement
    depuis le fichier `<source>.meta.json` écrit par `generate-sweep` (la
    bande utile qui exclut la marge de sécurité aux bords, voir sa
    docstring). Tu n'as donc normalement RIEN à spécifier ici tant que tu
    utilises un sweep généré par ce toolkit sans le renommer/déplacer
    séparément de son .meta.json. Un --f-min/--f-max explicite reste
    toujours prioritaire si tu veux forcer une autre valeur.
    """
    sr_s, source = load_audio(args.source)
    sr_r, recording = load_audio(args.recording)
    if sr_s != sr_r:
        raise SystemExit(f"Sample rates différents ({sr_s} vs {sr_r}), rééchantillonne d'abord.")
    if source.shape[1] < 2 or recording.shape[1] < 2:
        raise SystemExit("Source et enregistrement doivent être stéréo.")

    f_min, f_max, f_range_origin = resolve_f_range(args.source, args.f_min, args.f_max)
    print(f"  Bande analysée: {f_min}-{f_max} Hz ({f_range_origin})")

    if args.drift_correct:
        source, recording = align_with_drift_correction(source, recording, sr_s)
    else:
        source, recording = align(source, recording)

    freqs_L, H_L, coh_L = transfer_function_h1(source[:, 0], recording[:, 0], sr_s, args.nperseg)
    freqs_R, H_R, coh_R = transfer_function_h1(source[:, 1], recording[:, 1], sr_s, args.nperseg)

    gain_L_dB = 20 * np.log10(np.abs(H_L) + 1e-20)
    gain_R_dB = 20 * np.log10(np.abs(H_R) + 1e-20)
    diff_dB = gain_R_dB - gain_L_dB

    mask = (freqs_L >= f_min) & (freqs_L <= f_max)

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "gain_L_dB", "gain_R_dB", "diff_R_moins_L_dB", "coherence_L", "coherence_R"])
        for i in np.where(mask)[0]:
            w.writerow([f"{freqs_L[i]:.3f}", f"{gain_L_dB[i]:.4f}",
                        f"{gain_R_dB[i]:.4f}", f"{diff_dB[i]:.4f}",
                        f"{coh_L[i]:.4f}", f"{coh_R[i]:.4f}"])

    print(f"Mesure exportée: {args.output}")
    print(f"  Diff moyenne R-L (20Hz-20kHz): {diff_dB[mask].mean():.3f} dB")
    print(f"  Diff max (abs): {np.abs(diff_dB[mask]).max():.3f} dB "
          f"à {freqs_L[mask][np.argmax(np.abs(diff_dB[mask]))]:.0f} Hz")
    coh_min = min(coh_L[mask].min(), coh_R[mask].min())
    coh_mean = (coh_L[mask].mean() + coh_R[mask].mean()) / 2
    print(f"  Cohérence: moyenne {coh_mean:.4f}, min {coh_min:.4f}"
          + (" — attention, mesure peu fiable par endroits (cohérence < 0.9)" if coh_min < 0.9 else ""))


# ----------------------------------------------------------------------------
# measure-mono : équivalent de `measure` mais pour des enregistrements
# VRAIMENT mono (1 seul canal), pensé spécifiquement pour la méthode
# mono-diff avec isolation au niveau du câble (voir GUIDE.md).
# ----------------------------------------------------------------------------

def cmd_measure_mono(args):
    """Calcule le gain (en dB) par fréquence pour un enregistrement à UN
    SEUL canal, comparé à un fichier source (dont on utilise le premier
    canal comme référence — peu importe lequel, puisqu'un sweep de test
    généré normalement est identique sur ses deux canaux).

    POURQUOI CETTE COMMANDE PLUTÔT QUE `measure` POUR LA MÉTHODE MONO-DIFF
    -------------------------------------------------------------------------
    `measure` compare le canal 0 du fichier source au canal 0 de
    l'enregistrement, et le canal 1 au canal 1 — une correspondance
    index-à-index. Si tu utilises un sweep dont le contenu réel n'est que
    sur un canal (ex: `generate-sweep --mono-channel R`), il faut alors que
    l'enregistrement place SON contenu réel exactement sur le même index de
    canal (1, donc), quel que soit le canal physique de la carte son
    réellement utilisé pour la capture — un détail de bookkeeping facile à
    rater en pratique (et qui produit un résultat n'importe quoi si raté,
    sans erreur explicite).

    `measure-mono` élimine ce piège : elle prend simplement LE PREMIER canal
    de l'enregistrement (qu'il soit fourni comme fichier mono ou comme
    fichier stéréo dont un seul canal est pertinent), peu importe quel
    canal physique de la carte son a réellement capté le signal, et le
    compare au premier canal de la source (qui, pour un sweep normal
    identique sur L et R, est équivalent au second). Aucune correspondance
    d'index à respecter entre les deux sessions du mono-diff.

    WORKFLOW RECOMMANDÉ (isolation au niveau du câble, pas du fichier) :
    utilise le sweep NORMAL (généré sans --mono-channel, donc identique sur
    L et R). Isole le canal du baladeur à tester au niveau du câble (un
    adaptateur/câble breakout qui sépare les pôles L et R de la sortie
    casque), branche UNIQUEMENT le pôle à tester vers la carte son, capture
    en mono (ou en stéréo si ta carte l'exige, peu importe alors quel canal
    du fichier résultant contient le vrai signal — passe-le simplement en
    argument, `measure-mono` prendra son premier canal). Répète pour l'autre
    canal du baladeur, EN GARDANT LE MÊME CÂBLAGE CÔTÉ CARTE SON (c'est la
    condition qui élimine sa coloration propre, voir `cmd_mono_diff`).

    RÉSOLUTION AUTOMATIQUE DE --f-min/--f-max : comme pour `measure`, lue
    automatiquement depuis `<source>.meta.json` si tu ne la précises pas
    (voir `resolve_f_range`).
    """
    sr_s, source = load_audio(args.source)
    sr_r, recording = load_audio(args.recording)
    if sr_s != sr_r:
        raise SystemExit(f"Sample rates différents ({sr_s} vs {sr_r}), rééchantillonne d'abord.")

    f_min, f_max, f_range_origin = resolve_f_range(args.source, args.f_min, args.f_max)
    print(f"  Bande analysée: {f_min}-{f_max} Hz ({f_range_origin})")

    source_mono = source[:, :1]      # garde juste le 1er canal, en 2D (n,1)
    recording_mono = recording[:, :1]

    if args.drift_correct:
        source_mono, recording_mono = align_with_drift_correction(source_mono, recording_mono, sr_s)
    else:
        source_mono, recording_mono = align(source_mono, recording_mono)

    freqs, H, coh = transfer_function_h1(source_mono[:, 0], recording_mono[:, 0], sr_s, args.nperseg)
    gain_dB = 20 * np.log10(np.abs(H) + 1e-20)
    mask = (freqs >= f_min) & (freqs <= f_max)

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "gain_dB", "coherence"])
        for i in np.where(mask)[0]:
            w.writerow([f"{freqs[i]:.3f}", f"{gain_dB[i]:.4f}", f"{coh[i]:.4f}"])

    print(f"Mesure mono exportée: {args.output}")
    print(f"  Gain moyen (20Hz-20kHz): {gain_dB[mask].mean():.3f} dB")
    coh_min = coh[mask].min()
    print(f"  Cohérence: moyenne {coh[mask].mean():.4f}, min {coh_min:.4f}"
          + (" — attention, mesure peu fiable par endroits (cohérence < 0.9)" if coh_min < 0.9 else ""))


# ----------------------------------------------------------------------------
# diff : soustrait le biais de calibration de la mesure
# ----------------------------------------------------------------------------

def read_csv_curve(path):
    """Lit un CSV produit par `measure` (colonnes freq_hz, gain_L_dB,
    gain_R_dB, diff_R_moins_L_dB) et retourne 4 tableaux numpy alignés."""
    freqs, gL, gR, diff = [], [], [], []
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            freqs.append(float(row["freq_hz"]))
            gL.append(float(row["gain_L_dB"]))
            gR.append(float(row["gain_R_dB"]))
            diff.append(float(row["diff_R_moins_L_dB"]))
    return np.array(freqs), np.array(gL), np.array(gR), np.array(diff)


def cmd_diff(args):
    """Isole le défaut réel du baladeur en soustrayant la courbe de biais
    mesurée en boucle (calibration) de la mesure faite à travers le baladeur.

    Justification : comme le baladeur et la carte son sont deux systèmes en
    cascade, leurs déséquilibres L/R (exprimés en dB) s'ADDITIONNENT :
        diff_mesurée(f) = ΔD_baladeur(f) + ΔM_carte_son(f)
    Si on a mesuré ΔM_carte_son séparément (en bouclant la sortie casque du
    PC sur son entrée ligne, sans passer par le baladeur), on peut la
    soustraire pour isoler ΔD_baladeur.

    LIMITE DE CETTE MÉTHODE (voir GUIDE.md pour plus de détails) : elle
    suppose que ΔM_carte_son est identique entre la boucle de calibration et
    la vraie mesure à travers le baladeur. Ce n'est pas garanti si la sortie
    casque du PC et celle du baladeur ont des impédances de sortie très
    différentes, ce qui peut légèrement changer le comportement de l'entrée
    ligne selon la source branchée. La méthode `swap-diff` n'a pas cette
    limite et est recommandée en priorité quand elle est réalisable.
    """
    f_meas, gL_meas, gR_meas, diff_meas = read_csv_curve(args.mesure)
    f_calib, gL_c, gR_c, diff_calib = read_csv_curve(args.calibration)

    # interpole la calibration sur la grille de fréquences de la mesure
    diff_calib_interp = np.interp(f_meas, f_calib, diff_calib)
    net_diff = diff_meas - diff_calib_interp

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "diff_brute_dB", "diff_calibration_dB", "diff_nette_dB"])
        for i in range(len(f_meas)):
            w.writerow([f"{f_meas[i]:.3f}", f"{diff_meas[i]:.4f}",
                        f"{diff_calib_interp[i]:.4f}", f"{net_diff[i]:.4f}"])

    print(f"Défaut net exporté: {args.output}")
    print(f"  Diff nette moyenne: {net_diff.mean():.3f} dB, max abs: {np.abs(net_diff).max():.3f} dB")


# ----------------------------------------------------------------------------
# swap-diff : sépare le défaut réel du baladeur de la coloration de la carte
# son par la méthode d'inversion des canaux (channel swap), sans avoir besoin
# d'une calibration en boucle séparée.
# ----------------------------------------------------------------------------

def cmd_swap_diff(args):
    """Sépare le défaut réel du baladeur (ΔD) de la coloration propre à la
    carte son (ΔM) par la méthode d'inversion des canaux (channel swap),
    SANS avoir besoin de connaître ΔM à l'avance ni de faire d'hypothèse sur
    la façon dont elle a été mesurée.

    DÉRIVATION MATHÉMATIQUE COMPLÈTE
    ----------------------------------
    Notations : D_L, D_R = gain du baladeur par canal (en dB, à une fréquence
    donnée) ; M_L, M_R = gain de la carte son par canal (physiquement fixe,
    indépendant de ce qui y est branché). On note ΔD = D_R - D_L (le
    déséquilibre du baladeur qu'on cherche) et ΔM = M_R - M_L (la coloration
    de la carte son qu'on veut ignorer).

    Comme le baladeur et la carte son sont deux systèmes LTI en cascade, les
    gains en dB s'additionnent le long de la chaîne de chaque canal physique.

    Session 1 (câblage normal: baladeur-L -> carte-L, baladeur-R -> carte-R):
        gain(carte-L) = D_L + M_L
        gain(carte-R) = D_R + M_R
        diff1 = gain(carte-R) - gain(carte-L) = (D_R - D_L) + (M_R - M_L)
              = ΔD + ΔM

    Session 2 (câblage inversé: baladeur-L -> carte-R, baladeur-R -> carte-L):
        gain(carte-L) = D_R + M_L   (c'est maintenant le canal R du baladeur
                                      qui traverse le canal L de la carte)
        gain(carte-R) = D_L + M_R
        diff2 = gain(carte-R) - gain(carte-L) = (D_L - D_R) + (M_R - M_L)
              = -ΔD + ΔM

    En résolvant ce système de deux équations à deux inconnues (ΔD, ΔM) :
        ΔD = (diff1 - diff2) / 2   <- défaut RÉEL du baladeur, ΔM s'annule
        ΔM = (diff1 + diff2) / 2   <- coloration de la carte son (info bonus)

    ΔM s'annule EXACTEMENT dans le calcul de ΔD, quelle que soit sa forme ou
    son amplitude — c'est ce qui rend cette méthode plus robuste qu'une
    simple soustraction de calibration : elle ne suppose jamais que la
    coloration de la carte son est identique entre deux mesures différentes,
    elle l'élimine algébriquement à l'intérieur d'UNE SEULE paire de mesures
    cohérente entre elles.

    CONDITION DE VALIDITÉ : M_L et M_R (donc ΔM) doivent rester constants
    entre les deux sessions. Concrètement : ne touche à aucun réglage de
    gain/volume de la carte son entre les deux enregistrements, et fais les
    deux sessions à la suite sans redémarrer le pilote audio. Seul le
    câblage physique (quel canal du baladeur va où) doit changer.
    """
    with open(args.mesure_normale) as f:
        header = f.readline()
    col = "diff_R_moins_L_dB"  # `measure` produit toujours cette colonne

    def read_diff(path):
        with open(path) as f:
            r = csv.DictReader(f)
            freqs, diffs = [], []
            for row in r:
                freqs.append(float(row["freq_hz"]))
                diffs.append(float(row[col]))
        return np.array(freqs), np.array(diffs)

    f1, diff1 = read_diff(args.mesure_normale)
    f2, diff2 = read_diff(args.mesure_inversee)

    # interpole diff2 sur la grille de fréquences de diff1
    diff2_interp = np.interp(f1, f2, diff2)

    delta_D = (diff1 - diff2_interp) / 2   # vrai défaut du baladeur
    delta_M = (diff1 + diff2_interp) / 2   # coloration propre à la carte son

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "diff_nette_dB", "coloration_carte_son_dB"])
        for i in range(len(f1)):
            w.writerow([f"{f1[i]:.3f}", f"{delta_D[i]:.4f}", f"{delta_M[i]:.4f}"])

    print(f"Défaut réel (indépendant de la carte son) exporté: {args.output}")
    print(f"  Défaut baladeur - moyenne: {delta_D.mean():.3f} dB, max abs: {np.abs(delta_D).max():.3f} dB")
    print(f"  Coloration carte son (info) - moyenne: {delta_M.mean():.3f} dB, "
          f"max abs: {np.abs(delta_M).max():.3f} dB")
    if np.abs(delta_M).max() > 0.5:
        print("  Note: la coloration détectée sur la carte son est significative (>0.5dB) — "
              "bon réflexe d'avoir utilisé cette méthode plutôt qu'une simple calibration en boucle.")


# ----------------------------------------------------------------------------
# mono-diff : méthode la plus rigoureuse. Deux enregistrements MONO
# (baladeur-L seul, puis baladeur-R seul), tous deux routés vers LA MÊME
# entrée physique de la carte son. Élimine la coloration de la carte son par
# construction, sans avoir besoin d'algèbre de séparation (contrairement à
# `swap-diff`) puisqu'une seule et unique chaîne d'enregistrement est
# utilisée du début à la fin.
# ----------------------------------------------------------------------------

def cmd_mono_diff(args):
    """Calcule le défaut réel du baladeur à partir de deux mesures MONO
    (baladeur-L seul, puis baladeur-R seul), toutes deux capturées via LA
    MÊME entrée physique de la carte son. Accepte deux formats de CSV en
    entrée (auto-détectés) :
      - sortie de `measure-mono` (colonne `gain_dB`, RECOMMANDÉ — voir sa
        docstring pour pourquoi c'est la méthode la plus simple et la plus
        sûre, sans risque de confusion d'index de canal) ;
      - sortie de `measure` classique (colonnes `gain_L_dB`/`gain_R_dB`),
        pour qui préfère l'approche `generate-sweep --mono-channel` — dans
        ce cas `--channel` est OBLIGATOIRE pour préciser laquelle des deux
        colonnes est informative (voir piège décrit dans la docstring de
        `cmd_measure_mono` : il faut que le canal indiqué corresponde
        EXACTEMENT à l'index de canal où le vrai signal atterrit dans les
        DEUX fichiers `measure`, ce qui n'est pas garanti automatiquement).

    PRINCIPE (plus simple que `swap-diff`, et arguably plus rigoureux)
    ---------------------------------------------------------------------
    Deux sessions, chacune capturant UN SEUL canal du baladeur à la fois, via
    LA MÊME entrée physique de la carte son (ça demande de déplacer le câble
    entre les deux sessions, comme pour `swap-diff`, mais on n'utilise jamais
    qu'une seule des deux entrées de la carte son, jamais les deux en même
    temps).

    Notations : D_L, D_R = gain du baladeur par canal ; M_X = gain de
    l'unique entrée de carte son utilisée (peu importe sa valeur, elle
    n'apparaît jamais différemment entre les deux sessions puisque c'est
    littéralement la même entrée physique à chaque fois).

        Session A (baladeur-L actif -> carte-X) : gain_A = D_L + M_X
        Session B (baladeur-R actif -> carte-X) : gain_B = D_R + M_X
        ΔD = gain_B - gain_A = D_R - D_L   (M_X s'annule EXACTEMENT, par
                                             construction, sans avoir besoin
                                             d'aucune moyenne/algèbre)

    Cette méthode élimine par construction non seulement un déséquilibre de
    GAIN entre les deux entrées de la carte son, mais aussi TOUTE autre
    différence possible entre elles (phase, bruit propre, diaphonie/
    crosstalk, dérive d'horloge propre à chaque voie ADC si elle existait) —
    puisqu'une seule des deux entrées est utilisée du début à la fin, ses
    éventuels défauts propres n'ont tout simplement pas l'occasion de
    contaminer la comparaison L/R.

    CONDITION DE VALIDITÉ : l'entrée X de la carte son doit se comporter de
    façon stable entre les deux sessions (mêmes réglages de gain, pas de
    redémarrage du pilote audio) — exactement la même exigence que pour
    `swap-diff`, mais portant sur une seule entrée au lieu de deux.
    """
    def detect_and_read(path, channel_arg):
        with open(path) as f:
            header = f.readline()
        if "gain_dB" in header:
            col = "gain_dB"
        elif channel_arg:
            col = f"gain_{channel_arg}_dB"
        else:
            raise SystemExit(
                f"{path}: format `measure` classique détecté (gain_L_dB/gain_R_dB) "
                f"mais --channel n'a pas été fourni. Précise --channel L ou --channel R."
            )
        with open(path) as f:
            r = csv.DictReader(f)
            freqs, gains = [], []
            for row in r:
                freqs.append(float(row["freq_hz"]))
                gains.append(float(row[col]))
        return np.array(freqs), np.array(gains)

    f_L, gain_A = detect_and_read(args.mesure_baladeur_L, args.channel)
    f_R, gain_B = detect_and_read(args.mesure_baladeur_R, args.channel)

    gain_B_interp = np.interp(f_L, f_R, gain_B)
    delta_D = gain_B_interp - gain_A

    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "diff_nette_dB"])
        for i in range(len(f_L)):
            w.writerow([f"{f_L[i]:.3f}", f"{delta_D[i]:.4f}"])

    print(f"Défaut réel (méthode mono-diff, sans aucune hypothèse sur la carte son) exporté: {args.output}")
    print(f"  Moyenne: {delta_D.mean():.3f} dB, max abs: {np.abs(delta_D).max():.3f} dB")


# ----------------------------------------------------------------------------
# multivolume : compare plusieurs courbes de défaut net mesurées à différents
# volumes, pour déterminer si le défaut est un gain fixe ou dépend du volume.
# ----------------------------------------------------------------------------

def cmd_multivolume(args):
    """Compare plusieurs courbes de défaut (issues de `diff` ou `swap-diff`,
    une par palier de volume testé) pour déterminer si le déséquilibre L/R
    est un simple GAIN FIXE (constant en dB quel que soit le volume) ou s'il
    DÉPEND DU VOLUME (mécanisme non-linéaire : tracking imparfait du
    potentiomètre, non-linéarité du DAC près du zéro, distorsion de
    croisement en classe AB, calibration indépendante de chaque palier de
    volume numérique...).

    Cette distinction change directement la stratégie de correction :
      - Gain fixe -> un seul profil de correction (moyenné sur les volumes
        testés) reste valable à n'importe quel volume d'écoute.
      - Dépendant du volume -> une correction unique ne sera exacte qu'au
        volume où elle a été mesurée ; il faut soit se limiter à corriger le
        volume réellement utilisé en pratique, soit construire une
        correction par palier de volume (non géré par ce script, qui vise la
        solution pragmatique : mesurer/corriger au volume d'écoute habituel).

    Toutes les courbes sont interpolées sur une grille de fréquences commune
    (log-espacée, 300 points de 20Hz à 20kHz) avant comparaison, car les
    CSV en entrée n'ont pas forcément exactement les mêmes fréquences
    d'échantillonnage (dépend de la durée d'enregistrement de chaque
    session). L'écart max entre les courbes, par fréquence, est comparé à un
    seuil (`--threshold`, 0.3dB par défaut — au-delà, l'oreille humaine peut
    commencer à percevoir un déséquilibre stéréo dans des conditions
    d'écoute favorables).
    """
    curves = []
    common_grid = np.geomspace(20, 20000, 300)
    for path in args.diff_csvs:
        # lit la colonne pertinente selon le format du CSV (sortie de `diff`,
        # `swap-diff`, `mono-diff` -> diff_nette_dB ; sortie brute de
        # `measure` -> diff_R_moins_L_dB, utile si on n'a pas encore isolé
        # le défaut)
        with open(path) as f:
            header = f.readline()
        col = "diff_nette_dB" if "diff_nette_dB" in header else "diff_R_moins_L_dB"
        with open(path) as f:
            r = csv.DictReader(f)
            freqs_l, vals = [], []
            for row in r:
                freqs_l.append(float(row["freq_hz"]))
                vals.append(float(row[col]))
        interp = np.interp(common_grid, freqs_l, vals)
        curves.append(interp)
        print(f"  {os.path.basename(path)}: moyenne {np.mean(interp):.3f} dB")

    curves = np.array(curves)
    spread = curves.max(axis=0) - curves.min(axis=0)  # écart entre volumes, par fréquence

    print(f"\nÉcart max entre les {len(args.diff_csvs)} courbes, par fréquence:")
    print(f"  Moyen: {spread.mean():.3f} dB")
    print(f"  Max:   {spread.max():.3f} dB à {common_grid[spread.argmax()]:.0f} Hz")

    threshold = args.threshold
    if spread.max() < threshold:
        print(f"\n=> Écart sous le seuil ({threshold} dB): le défaut semble être un GAIN FIXE.")
        print("   Un seul profil de correction (moyenné sur les volumes) devrait suffire.")
    else:
        print(f"\n=> Écart au-dessus du seuil ({threshold} dB): le défaut DÉPEND DU VOLUME.")
        print("   Une correction unique ne sera juste qu'au volume où elle a été mesurée.")
        print("   Recommandé: corriger spécifiquement au volume que tu utilises le plus souvent.")

    if args.output:
        avg_curve = curves.mean(axis=0)
        with open(args.output, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["freq_hz"] + [os.path.basename(p) for p in args.diff_csvs] + ["moyenne"])
            for i, fr in enumerate(common_grid):
                w.writerow([f"{fr:.2f}"] + [f"{curves[j,i]:.4f}" for j in range(len(curves))]
                           + [f"{avg_curve[i]:.4f}"])
        print(f"\nCourbes comparées exportées: {args.output}")


# ----------------------------------------------------------------------------
# build-eq : génère un profil de correction lissé (fractional-octave smoothing)
# prêt à être appliqué via ffmpeg firequalizer
# ----------------------------------------------------------------------------

def smooth_fractional_octave(freqs, values, fraction=6):
    """Lissage en 1/fraction d'octave (ex: fraction=6 -> lissage 1/6 octave).

    Pour chaque point de fréquence, on fait la moyenne de toutes les valeurs
    dont la fréquence est à moins d'une demi-largeur de bande (en octaves) du
    point courant. C'est la technique standard de lissage des courbes de
    réponse en fréquence en acoustique/électronique (par exemple utilisée par
    tout analyseur RTA ou logiciel de mesure de réponse en fréquence).

    Essentiel pour ne pas figer le bruit de mesure dans le filtre de
    correction final : la courbe brute de défaut contient des variations
    point-à-point qui viennent en grande partie du bruit de mesure (bruit de
    fond de la pièce, imperfections de l'estimateur H1, résidus
    d'alignement temporel...) plutôt que du comportement réel du matériel.
    Construire un filtre EQ qui suit fidèlement chaque micro-variation de la
    courbe brute reviendrait à sur-ajuster (overfitting) sur ce bruit, ce qui
    donnerait un filtre inutilement complexe et potentiellement contre-
    productif. Le lissage fractionnaire d'octave élimine les variations trop
    étroites en fréquence (probablement du bruit) tout en préservant les
    tendances plus larges (probablement le vrai comportement du matériel).

    Un lissage plus fin (fraction plus grande, ex: 12 pour 1/12 octave)
    préserve plus de détail mais laisse passer plus de bruit ; un lissage
    plus large (fraction plus petite, ex: 3 pour 1/3 octave) est plus robuste
    au bruit mais peut masquer un vrai défaut étroit en fréquence.
    """
    log_f = np.log2(freqs)
    smoothed = np.zeros_like(values)
    half_width = 0.5 / fraction  # en octaves
    for i, lf in enumerate(log_f):
        mask = np.abs(log_f - lf) <= half_width
        smoothed[i] = values[mask].mean()
    return smoothed


def cmd_build_eq(args):
    """Construit un profil de correction EQ (fichier JSON) à partir d'une
    courbe de défaut net (sortie de `diff` ou `swap-diff`), prêt à être
    utilisé par `apply`/`batch-apply` pour générer un filtre ffmpeg
    `firequalizer`.

    Étapes :
    1. Ré-échantillonnage sur une grille log-régulière (500 points, 20Hz-
       20kHz) pour uniformiser la densité de points avant lissage.
    2. Lissage fractionnaire d'octave (voir `smooth_fractional_octave`).
    3. Répartition SYMÉTRIQUE de la correction sur les deux canaux : plutôt
       que de tout corriger sur un seul canal (ex: uniquement atténuer R),
       on répartit moitié-moitié (+diff/2 sur L, -diff/2 sur R). Ça corrige
       le déséquilibre relatif tout en gardant le volume global moyen
       inchangé (ce que ferait une correction asymétrique, qui changerait le
       niveau sonore perçu du morceau en plus de corriger le déséquilibre).
    4. Protection anti-écrêtage : si la répartition symétrique impliquait
       malgré tout un boost net sur un canal (gain positif, donc
       amplification), on décale les DEUX courbes vers le bas de la valeur
       nécessaire pour que le gain maximal appliqué soit exactement 0dB.
       Le profil final n'amplifie donc jamais rien, il atténue seulement
       (parfois plus sur un canal que l'autre) — cette approche élimine tout
       risque de clipping numérique après correction, au prix d'une légère
       perte de niveau global (généralement inaudible, quelques dixièmes de
       dB tout au plus pour un défaut typique).
    5. Sous-échantillonnage à `--points` points (40 par défaut) pour garder
       une commande ffmpeg de taille raisonnable : le filtre firequalizer
       interpole lui-même entre les points fournis, donc il n'est pas
       nécessaire de lui donner les 500 points de la grille de travail.
    """
    with open(args.diff_csv) as f:
        header = f.readline()
    col = "diff_nette_dB" if "diff_nette_dB" in header else "diff_R_moins_L_dB"
    with open(args.diff_csv) as f:
        r = csv.DictReader(f)
        freqs_l, vals = [], []
        for row in r:
            freqs_l.append(float(row["freq_hz"]))
            vals.append(float(row[col]))
    freqs_l, vals = np.array(freqs_l), np.array(vals)

    # rééchantillonne sur une grille log régulière avant lissage
    grid = np.geomspace(max(20, freqs_l.min()), min(20000, freqs_l.max()), 500)
    diff_grid = np.interp(grid, freqs_l, vals)
    diff_smooth = smooth_fractional_octave(grid, diff_grid, fraction=args.smoothing)

    # Correction symétrique : on répartit la correction sur les deux canaux
    # pour ne pas changer le volume global. diff = gain_R - gain_L.
    # correction_L = +diff/2 (booste L), correction_R = -diff/2 (atténue R)
    corr_L = diff_smooth / 2
    corr_R = -diff_smooth / 2

    # Sécurité anti-écrêtage : on ne fait jamais de boost net, seulement de
    # l'atténuation, en décalant les deux courbes vers le bas si besoin.
    max_boost = max(corr_L.max(), corr_R.max())
    if max_boost > 0:
        corr_L -= max_boost
        corr_R -= max_boost
        print(f"Note: décalage de {max_boost:.3f} dB appliqué pour éviter tout boost "
              f"(protection anti-écrêtage). Le profil n'atténue jamais que.")

    # sous-échantillonne à un nombre raisonnable de points pour la commande ffmpeg
    n_points = args.points
    idx = np.linspace(0, len(grid) - 1, n_points).astype(int)
    profile = {
        "source_csv": os.path.abspath(args.diff_csv),
        "smoothing_fraction_octave": args.smoothing,
        "points": [
            {"freq": float(grid[i]), "gain_L_dB": float(corr_L[i]), "gain_R_dB": float(corr_R[i])}
            for i in idx
        ],
    }
    with open(args.output, "w") as f:
        json.dump(profile, f, indent=2)

    print(f"Profil de correction exporté: {args.output} ({n_points} points, "
          f"lissage 1/{args.smoothing} octave)")


# ----------------------------------------------------------------------------
# apply / batch-apply : applique le profil via ffmpeg (firequalizer, par canal)
# ----------------------------------------------------------------------------

def build_gain_entry(points, key):
    """Construit la chaîne `gain_entry` attendue par le filtre `firequalizer`
    de ffmpeg : une liste de paires (fréquence, gain en dB) séparées par des
    points-virgules. ffmpeg interpole automatiquement entre les points
    fournis (spline), donc il n'est pas nécessaire de fournir un point pour
    chaque fréquence de la mesure d'origine."""
    entries = ";".join(f"entry({p['freq']:.1f},{p[key]:.3f})" for p in points)
    return entries


def build_filter_complex(profile):
    """Construit la chaîne `filter_complex` ffmpeg complète qui applique une
    correction EQ INDÉPENDANTE sur chaque canal :
    1. `channelsplit` sépare le flux stéréo en deux flux mono (L et R).
    2. Un `firequalizer` distinct est appliqué à chaque flux mono, avec sa
       propre courbe de gain (gain_L_dB pour L, gain_R_dB pour R) — c'est ce
       qui permet de corriger les deux canaux différemment, contrairement à
       un EQ stéréo classique qui applique la même courbe aux deux.
    3. `join` recombine les deux flux mono corrigés en un flux stéréo final.
    """
    gain_L = build_gain_entry(profile["points"], "gain_L_dB")
    gain_R = build_gain_entry(profile["points"], "gain_R_dB")
    filt = (
        "[0:a]channelsplit=channel_layout=stereo[L][R];"
        f"[L]firequalizer=gain_entry='{gain_L}'[Lc];"
        f"[R]firequalizer=gain_entry='{gain_R}'[Rc];"
        "[Lc][Rc]join=inputs=2:channel_layout=stereo[out]"
    )
    return filt


def cmd_apply(args):
    """Applique le profil de correction (JSON de `build-eq`) à un seul
    fichier audio via ffmpeg, en préservant le format flac si demandé."""
    with open(args.profile) as f:
        profile = json.load(f)
    filt = build_filter_complex(profile)
    cmd = [
        "ffmpeg", "-y", "-i", args.input,
        "-filter_complex", filt,
        "-map", "[out]",
    ]
    if args.input.lower().endswith(".flac") or args.output.lower().endswith(".flac"):
        cmd += ["-c:a", "flac"]
    cmd.append(args.output)
    print("Commande ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Fichier corrigé: {args.output}")


def cmd_batch_apply(args):
    """Applique le profil de correction à tous les fichiers audio d'une
    ARBORESCENCE complète (parcours récursif), en préservant intégralement
    la structure de dossiers d'origine dans la sortie.

    Pensé pour une vraie bibliothèque musicale : artistes/albums en
    sous-dossiers imbriqués, noms de fichiers en n'importe quel alphabet
    (japonais, etc. — Python/os.walk gèrent nativement l'UTF-8, aucun
    traitement spécial n'est nécessaire), et fichiers annexes mélangés aux
    pistes audio (pochettes .jpg/.png, paroles .lrc...).

    CE QUE CETTE VERSION GÈRE, EN PLUS D'UN SIMPLE `glob` À PLAT
    -------------------------------------------------------------------
    - Parcours RÉCURSIF (`os.walk`) de toute l'arborescence, pas seulement
      le dossier racine — indispensable pour une bibliothèque organisée en
      artiste/album/pistes.
    - La structure de dossiers est REPRODUITE À L'IDENTIQUE côté sortie
      (chemin relatif préservé), donc pas de collision entre deux pistes
      qui porteraient le même nom dans deux albums différents (très
      fréquent: "01 xxx.flac" existe dans presque chaque album).
    - Les fichiers NON-AUDIO (pochettes, .lrc, tout ce qui n'est pas dans
      `--extensions`) sont copiés tels quels dans la sortie, pour obtenir
      une bibliothèque de sortie complète et directement utilisable, pas
      seulement les pistes corrigées éparpillées sans leurs pochettes.
    - REPRISE: si un fichier de sortie existe déjà, il est ignoré par
      défaut (utile pour reprendre un traitement interrompu sur une grosse
      bibliothèque sans tout refaire depuis le début). `--overwrite` pour
      forcer le retraitement de tout.
    - Un ÉCHEC SUR UN FICHIER N'ARRÊTE PAS LE reste DU LOT : chaque appel
      ffmpeg est isolé, les échecs sont collectés et rapportés à la fin
      (avec un log détaillé), plutôt que de stopper toute la bibliothèque
      à la première piste corrompue/problématique.
    - `--jobs N` : traite N fichiers en parallèle (utile sur une grosse
      bibliothèque ; chaque appel ffmpeg tourne dans un sous-processus,
      donc le parallélisme est sûr même en Python malgré le GIL — les
      threads Python passent le plus clair de leur temps à attendre que le
      sous-processus ffmpeg termine). Défaut: 1 (séquentiel, logs plus
      lisibles) ; monte à 4-8 sur une machine multi-cœurs pour accélérer
      nettement un traitement de plusieurs milliers de pistes.
    - `--dry-run` : affiche ce qui serait fait (compte de fichiers audio à
      corriger, fichiers annexes à copier, fichiers ignorés car déjà
      présents) sans rien exécuter — utile pour vérifier avant de lancer un
      traitement long sur une bibliothèque volumineuse.
    """
    with open(args.profile) as f:
        profile = json.load(f)
    filt = build_filter_complex(profile)

    audio_exts = tuple(
        e if e.startswith(".") else "." + e
        for e in (x.strip().lower() for x in args.extensions.split(","))
        if e
    )

    input_root = os.path.abspath(args.input_dir)
    output_root = os.path.abspath(args.output_dir)

    all_files = []
    for dirpath, _dirnames, filenames in os.walk(input_root):
        for fname in filenames:
            all_files.append(os.path.join(dirpath, fname))

    audio_files = [p for p in all_files if p.lower().endswith(audio_exts)]
    other_files = [p for p in all_files if p not in audio_files]

    print(f"Arborescence: {len(audio_files)} fichier(s) audio à corriger, "
          f"{len(other_files)} fichier(s) annexe(s) à copier tel quel "
          f"(pochettes, paroles...), sous {input_root}")
    if args.dry_run:
        print("(dry-run: aucune action ne sera réellement exécutée)")

    # --- Fichiers annexes: copie brute, structure préservée ---
    copied, copy_skipped = 0, 0
    if not args.skip_others:
        for path in other_files:
            rel = os.path.relpath(path, input_root)
            out_path = os.path.join(output_root, rel)
            if os.path.exists(out_path) and not args.overwrite:
                copy_skipped += 1
                continue
            if not args.dry_run:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                shutil.copy2(path, out_path)
            copied += 1

    # --- Fichiers audio: liste des tâches restantes (après filtre reprise) ---
    tasks = []  # (path, rel, out_path)
    audio_skipped = 0
    for path in audio_files:
        rel = os.path.relpath(path, input_root)
        out_path = os.path.join(output_root, rel)
        if os.path.exists(out_path) and not args.overwrite:
            audio_skipped += 1
            continue
        tasks.append((path, rel, out_path))

    if args.dry_run:
        print(f"  -> {len(tasks)} piste(s) seraient corrigées, {audio_skipped} déjà présente(s) ignorée(s), "
              f"{copied} fichier(s) annexe(s) seraient copiés, {copy_skipped} déjà présents ignorés.")
        return

    def process_one(item):
        path, rel, out_path = item
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        cmd = ["ffmpeg", "-y", "-i", path, "-filter_complex", filt, "-map", "[out]"]
        if path.lower().endswith(".flac"):
            cmd += ["-c:a", "flac"]
        cmd.append(out_path)
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            return (rel, False, result.stderr.decode(errors="replace")[-800:])
        return (rel, True, None)

    print(f"Traitement de {len(tasks)} piste(s) audio ({audio_skipped} déjà présente(s) ignorée(s)), "
          f"parallélisme: {args.jobs}...")

    processed, failures = 0, []
    if args.jobs <= 1:
        for i, item in enumerate(tasks, start=1):
            rel = item[1]
            print(f"  [{i}/{len(tasks)}] {rel}")
            _, ok, err = process_one(item)
            if ok:
                processed += 1
            else:
                failures.append((rel, err))
                print(f"      ECHEC: {rel}")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(process_one, item): item[1] for item in tasks}
            done = 0
            for future in concurrent.futures.as_completed(futures):
                done += 1
                rel, ok, err = future.result()
                status = "OK" if ok else "ECHEC"
                print(f"  [{done}/{len(tasks)}] {status}: {rel}")
                if ok:
                    processed += 1
                else:
                    failures.append((rel, err))

    print()
    print(f"Terminé. {processed} piste(s) corrigée(s), {audio_skipped} déjà présente(s) ignorée(s), "
          f"{copied} fichier(s) annexe(s) copié(s) ({copy_skipped} déjà présents ignorés), "
          f"{len(failures)} échec(s).")

    if failures:
        log_path = os.path.join(output_root, "_erreurs_batch.log")
        with open(log_path, "w", encoding="utf-8") as f:
            for rel, err in failures:
                f.write(f"=== {rel} ===\n{err}\n\n")
        print(f"Fichiers en échec ({len(failures)}):")
        for rel, _err in failures[:20]:
            print(f"  - {rel}")
        if len(failures) > 20:
            print(f"  ... et {len(failures)-20} autre(s), voir le log complet.")
        print(f"Détails complets des erreurs: {log_path}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("generate-sweep", help="Génère un sweep logarithmique stéréo (signal de test)")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--f-min", type=float, default=20,
                     help="Bande BASSE utile/fiable souhaitée en Hz (défaut: 20). Le sweep est généré un "
                          "peu plus large (voir --margin-ratio) pour que cette valeur reste fiable à "
                          "l'analyse ; c'est CETTE valeur (pas celle réellement générée) qui sera reprise "
                          "automatiquement par measure/measure-mono via le fichier .meta.json")
    sp.add_argument("--f-max", type=float, default=20000,
                     help="Bande HAUTE utile/fiable souhaitée en Hz (défaut: 20000). Même principe que "
                          "--f-min ci-dessus.")
    sp.add_argument("--duration", type=float, default=30)
    sp.add_argument("--samplerate", type=int, default=48000)
    sp.add_argument("--amplitude", type=float, default=0.5, help="0-1, headroom anti-écrêtage")
    sp.add_argument("--mono-channel", choices=["L", "R"], default=None,
                     help="Place le sweep uniquement sur ce canal (silence sur l'autre), pour mono-diff")
    sp.add_argument("--margin-ratio", type=float, default=0.15,
                     help="Marge générée au-delà de f-min/f-max pour éviter les artefacts de bord "
                          "d'estimation (défaut: 0.15, soit 15%%). Mets 0 pour désactiver — déconseillé, "
                          "voir GUIDE.md section 'Artefacts de bord de sweep'.")
    sp.set_defaults(func=cmd_generate_sweep)

    sp = sub.add_parser("generate-noise", help="Génère du bruit blanc filtré (bande limitée), signal de "
                                                "test ALTERNATIF au sweep — voir sa docstring pour quand "
                                                "l'un ou l'autre a un intérêt réel")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--f-min", type=float, default=20, help="Bande BASSE utile/fiable en Hz (défaut: 20)")
    sp.add_argument("--f-max", type=float, default=20000, help="Bande HAUTE utile/fiable en Hz (défaut: 20000)")
    sp.add_argument("--duration", type=float, default=60,
                     help="Durée en secondes (défaut: 60 — plus long que generate-sweep par défaut, "
                          "voir docstring pour pourquoi c'est nécessaire avec du bruit)")
    sp.add_argument("--samplerate", type=int, default=48000)
    sp.add_argument("--amplitude", type=float, default=0.5, help="0-1, headroom anti-écrêtage")
    sp.add_argument("--mono-channel", choices=["L", "R"], default=None,
                     help="Place le bruit uniquement sur ce canal (silence sur l'autre), pour mono-diff")
    sp.add_argument("--margin-ratio", type=float, default=0.15,
                     help="Marge de filtrage au-delà de f-min/f-max (défaut: 0.15, soit 15%%)")
    sp.add_argument("--seed", type=int, default=None, help="Graine aléatoire (pour reproductibilité)")
    sp.set_defaults(func=cmd_generate_noise)

    sp = sub.add_parser("measure", help="Calcule le gain par fréquence et par canal (source vs enregistrement)")
    sp.add_argument("source")
    sp.add_argument("recording")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--f-min", type=float, default=None,
                     help="Bande basse à analyser. Par défaut: lue depuis <source>.meta.json "
                          "(écrit par generate-sweep), sinon 20 Hz")
    sp.add_argument("--f-max", type=float, default=None,
                     help="Bande haute à analyser. Par défaut: lue depuis <source>.meta.json "
                          "(écrit par generate-sweep), sinon 20000 Hz")
    sp.add_argument("--nperseg", type=int, default=32768)
    sp.add_argument("--no-drift-correction", dest="drift_correct", action="store_false",
                     help="Désactive la détection/correction de dérive d'horloge (activée par défaut)")
    sp.set_defaults(func=cmd_measure, drift_correct=True)

    sp = sub.add_parser("measure-mono", help="Comme `measure` mais pour un enregistrement à 1 seul canal "
                                              "(RECOMMANDÉ pour la méthode mono-diff, évite le piège de "
                                              "correspondance d'index de canal — voir sa docstring)")
    sp.add_argument("source")
    sp.add_argument("recording")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--f-min", type=float, default=None,
                     help="Bande basse à analyser. Par défaut: lue depuis <source>.meta.json "
                          "(écrit par generate-sweep), sinon 20 Hz")
    sp.add_argument("--f-max", type=float, default=None,
                     help="Bande haute à analyser. Par défaut: lue depuis <source>.meta.json "
                          "(écrit par generate-sweep), sinon 20000 Hz")
    sp.add_argument("--nperseg", type=int, default=32768)
    sp.add_argument("--no-drift-correction", dest="drift_correct", action="store_false",
                     help="Désactive la détection/correction de dérive d'horloge (activée par défaut)")
    sp.set_defaults(func=cmd_measure_mono, drift_correct=True)

    sp = sub.add_parser("diff", help="Soustrait le biais de calibration d'une mesure")
    sp.add_argument("mesure", help="CSV issu de `measure` sur le baladeur")
    sp.add_argument("calibration", help="CSV issu de `measure` en boucle (calibration carte son)")
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("swap-diff", help="Sépare le défaut du baladeur de la coloration carte son via "
                                           "inversion des canaux (aucune calibration séparée requise)")
    sp.add_argument("mesure_normale", help="CSV `measure` avec câblage normal (baladeur-L->carte-L, baladeur-R->carte-R)")
    sp.add_argument("mesure_inversee", help="CSV `measure` avec câblage inversé (baladeur-L->carte-R, baladeur-R->carte-L)")
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=cmd_swap_diff)

    sp = sub.add_parser("mono-diff", help="MÉTHODE LA PLUS RIGOUREUSE: sépare le défaut du baladeur en "
                                           "utilisant une seule et unique entrée de la carte son du début à la fin")
    sp.add_argument("mesure_baladeur_L", help="CSV de la session où seul le canal L du baladeur est actif "
                                               "(sortie de `measure-mono`, ou de `measure` + --channel)")
    sp.add_argument("mesure_baladeur_R", help="CSV de la session où seul le canal R du baladeur est actif")
    sp.add_argument("--channel", choices=["L", "R"], default=None,
                     help="Requis UNIQUEMENT si les CSV viennent de `measure` classique: entrée physique de "
                          "la carte son utilisée pour les DEUX sessions. Pas nécessaire avec `measure-mono`.")
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=cmd_mono_diff)

    sp = sub.add_parser("multivolume", help="Compare des courbes de défaut mesurées à différents volumes")
    sp.add_argument("diff_csvs", nargs="+", help="CSVs de sortie de `diff`/`swap-diff`/`mono-diff` (ou `measure`), un par volume")
    sp.add_argument("--threshold", type=float, default=0.3, help="Seuil en dB pour juger 'fixe' vs 'dépendant'")
    sp.add_argument("-o", "--output", help="CSV optionnel avec toutes les courbes superposées")
    sp.set_defaults(func=cmd_multivolume)

    sp = sub.add_parser("build-eq", help="Génère un profil de correction EQ lissé (JSON)")
    sp.add_argument("diff_csv", help="CSV du défaut net (sortie de `diff`) ou brut (sortie de `measure`)")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--smoothing", type=int, default=6, help="Lissage 1/N octave (défaut: 1/6)")
    sp.add_argument("--points", type=int, default=40, help="Nombre de points dans le filtre ffmpeg")
    sp.set_defaults(func=cmd_build_eq)

    sp = sub.add_parser("apply", help="Applique le profil de correction à un fichier")
    sp.add_argument("profile", help="JSON issu de build-eq")
    sp.add_argument("input")
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("batch-apply", help="Applique le profil de correction à toute une arborescence "
                                             "(récursif, sous-dossiers imbriqués, fichiers annexes copiés)")
    sp.add_argument("profile")
    sp.add_argument("input_dir")
    sp.add_argument("output_dir")
    sp.add_argument("--extensions", default="flac,wav,mp3,m4a,ogg,opus,wma,aac",
                     help="Extensions traitées comme fichiers audio, séparées par des virgules "
                          "(défaut: flac,wav,mp3,m4a,ogg,opus,wma,aac)")
    sp.add_argument("--overwrite", action="store_true",
                     help="Retraite/recopie même les fichiers déjà présents dans la sortie "
                          "(par défaut: ignorés, pratique pour reprendre un traitement interrompu)")
    sp.add_argument("--skip-others", action="store_true",
                     help="Ne copie pas les fichiers non-audio (pochettes, .lrc...) dans la sortie")
    sp.add_argument("--jobs", type=int, default=1,
                     help="Nombre de fichiers traités en parallèle (défaut: 1 séquentiel ; "
                          "monte à 4-8 pour accélérer sur une grosse bibliothèque)")
    sp.add_argument("--dry-run", action="store_true",
                     help="Affiche ce qui serait fait sans rien exécuter réellement")
    sp.set_defaults(func=cmd_batch_apply)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
