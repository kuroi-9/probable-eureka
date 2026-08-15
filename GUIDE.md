# Guide : mesurer et corriger un déséquilibre de gain L/R sur un baladeur

Ce guide accompagne `dac_correction_toolkit.py`. Il explique le contexte, le
matériel nécessaire, chaque étape du pipeline en détail, et comment
interpréter les résultats.

## Sommaire

1. [Contexte et objectif](#1-contexte-et-objectif)
2. [Matériel nécessaire](#2-matériel-nécessaire)
3. [Vérifications avant de commencer](#3-vérifications-avant-de-commencer)
4. [Théorie en bref](#4-théorie-en-bref)
5. [Pipeline pas à pas](#5-pipeline-pas-à-pas)
6. [Interpréter les résultats](#6-interpréter-les-résultats)
7. [Appliquer la correction](#7-appliquer-la-correction)
8. [Dépannage](#8-dépannage)
9. [Limites de la méthode](#9-limites-de-la-méthode)
10. [Référence des commandes](#10-référence-des-commandes)

---

## 1. Contexte et objectif

Point de départ : une perception d'un léger déséquilibre de volume entre les
canaux gauche et droit sur un baladeur à architecture symétrique 4 DAC (un
DAC dédié à la partie positive et un DAC dédié à la partie négative du
signal, par canal). Deux tests simples ont déjà exclu certaines causes :

- **Retourner le casque** n'a presque rien changé → le problème ne vient
  probablement pas du casque lui-même.
- **Inverser les canaux numériquement** (`pan=stereo|FL=FR|FR=FL`) n'a
  presque rien changé non plus → le problème ne vient probablement pas non
  plus du fichier source / de la source audio.

Ces deux résultats pointent vers le baladeur (DAC, ampli casque, ou le
contrôle de volume) comme origine la plus probable du déséquilibre.

**Objectif du pipeline** : mesurer objectivement (plutôt qu'à l'oreille) le
déséquilibre L/R du baladeur, **fréquence par fréquence**, en éliminant
toute confusion possible avec la coloration propre de l'appareil utilisé
pour la mesure (carte son / entrée ligne). Une fois cette courbe de défaut
obtenue, générer un filtre de correction et l'appliquer aux fichiers
musicaux.

---

## 2. Matériel nécessaire

| Élément | Rôle | Notes |
|---|---|---|
| Le baladeur à tester | Appareil sous test | - |
| Câble 4.4mm (ou jack approprié) vers l'entrée d'enregistrement | Relier la sortie casque à l'entrée d'enregistrement | Un simple câble stéréo classique suffit |
| Une entrée ligne stéréo | Capturer le signal de sortie du baladeur | Voir ci-dessous, point critique |
| `ffmpeg` installé | Conversion de formats, application du filtre final | `ffmpeg -version` pour vérifier |
| Python 3 + `numpy`/`scipy` | Faire tourner le script d'analyse | `pip install numpy scipy --break-system-packages` |

### Sur l'entrée d'enregistrement : évite l'entrée micro si possible

Une entrée micro de carte mère classique pose plusieurs problèmes pour ce
genre de mesure :

- **Filtre coupe-bas** intégré (souvent ~80-100Hz) qui fausse la mesure dans
  le grave.
- **Alimentation fantôme / mic bias** (quelques volts) qui peut interagir
  mal avec une sortie casque, voire créer un léger court-circuit.
- **AGC et suppression de bruit** activés par défaut sur beaucoup de
  pilotes — non-linéaires, ils faussent activement la mesure de gain.
  **Impératif si tu utilises quand même une entrée micro : désactive toutes
  les "améliorations audio" dans les paramètres son.**
- Souvent **mono**, ce qui rend la comparaison L/R impossible dans un seul
  enregistrement.

**Meilleures options, par ordre de préférence :**
1. Une interface audio USB avec vraie entrée ligne stéréo (ex: Behringer
   UCA202, ~25-30€) — élimine tous les problèmes ci-dessus.
2. Une entrée ligne dédiée sur la carte mère (différente de l'entrée micro),
   si disponible.
3. À défaut, une entrée micro avec toutes les améliorations désactivées,
   en gardant à l'esprit les limites ci-dessus.

---

## 3. Vérifications avant de commencer

Avant de lancer la moindre mesure sérieuse, vérifie ta chaîne
d'enregistrement :

1. **Stéréo ou mono ?** Vérifie dans les paramètres son (panneau de la carte
   son ou paramètres système) que l'entrée capture bien 2 canaux distincts.
2. **Améliorations audio désactivées** (AGC, suppression de bruit, effets) —
   à couper entièrement sur l'entrée utilisée.
3. **Bruit de fond** : enregistre quelques secondes de silence (rien
   branché) et regarde le niveau RMS avec `astats` :
   ```bash
   ffmpeg -i silence.wav -af astats -f null - 2>&1 | grep "RMS level"
   ```
   Un bruit de fond élevé dégrade la précision de toute la mesure.
4. **Pas d'écrêtage** : fais un essai avec le sweep à faible volume d'abord,
   vérifie le peak level, augmente progressivement.

---

## 4. Théorie en bref

### Fonction de transfert et gain en dB

On modélise chaque étage de la chaîne (baladeur, carte son) comme un système
linéaire invariant dans le temps (LTI), caractérisé par une fonction de
transfert complexe H(f) — en clair, un gain et un déphasage qui peuvent
varier avec la fréquence. Le point clé : **deux systèmes en cascade ont des
gains qui se multiplient**, ce qui, exprimé en décibels (dB = 20·log₁₀ du
module), **s'additionne**. C'est cette propriété qui permet, plus loin, de
séparer proprement le défaut du baladeur de celui de la carte son par simple
addition/soustraction de courbes.

### Pourquoi un sweep et pas de la musique

Un morceau de musique n'a pas une énergie uniforme sur tout le spectre (peu
de contenu à 15kHz par exemple), ce qui rend la mesure du gain peu fiable
aux fréquences peu présentes. Un **sweep logarithmique** (balayage de 20Hz à
20kHz, avec un temps égal passé par octave plutôt que par Hz) donne une
énergie de test homogène et prévisible sur tout le spectre audible.

### L'estimateur H1 (méthode de Welch)

Plutôt qu'une simple division de FFT (bruitée), le script utilise
l'estimateur **H1 = Pxy / Pxx** :
- Pxy = densité spectrale croisée entre le signal enregistré et la source
- Pxx = densité spectrale de la source seule

Les deux sont calculées en moyennant plusieurs segments recouvrants du
signal (méthode de Welch), ce qui réduit fortement le bruit de mesure par
rapport à une FFT unique sur tout le signal.

### Sweep vs bruit blanc (`generate-sweep` vs `generate-noise`)

Pour un système **linéaire** (l'hypothèse de travail habituelle pour un DAC/
ampli casque, tant qu'on reste loin de la saturation), la réponse en
fréquence mesurée par ce toolkit est **théoriquement identique** qu'on
utilise un sweep ou du bruit blanc — principe de superposition, chaque
fréquence est traitée indépendamment des autres, qu'elles soient présentes
l'une après l'autre (sweep) ou toutes en même temps (bruit). **Le bruit
n'apporte donc pas d'information supplémentaire sur le déséquilibre L/R que
ce toolkit mesure.**

Ce qu'il apporte réellement :
- Pas d'artefact de bord de bande (voir plus bas), puisqu'il n'y a pas de
  balayage séquentiel.
- Un indicateur de qualité **par fréquence** bien plus informatif : la
  **cohérence** (voir juste après), calculable de façon pertinente
  puisque le bruit sollicite chaque fréquence en permanence sur toute la
  durée de l'enregistrement — contrairement au sweep, qui ne passe qu'une
  fois, brièvement, à chaque fréquence.
- Il pourrait en théorie détecter des choses qu'un sweep ne détecte pas
  **uniquement** si le baladeur a un comportement non-linéaire (distorsion,
  intermodulation) — mais même dans ce cas, ce toolkit ne caractérise pas
  cette non-linéarité, il indiquerait juste une cohérence basse à
  l'endroit concerné, sans en dire la cause.

Ce qu'il coûte : pour une précision comparable à un sweep, il faut un
enregistrement **nettement plus long** — l'énergie du bruit est étalée en
permanence sur tout le spectre, alors que le sweep concentre toute son
énergie sur chaque fréquence l'une après l'autre. D'où la durée par défaut
plus longue de `generate-noise` (60s) par rapport à `generate-sweep` (30s).

En résumé : **pas de contre-indication à utiliser du bruit, mais pas de
gain réel non plus** pour ce que ce toolkit mesure — sauf si tu veux
surveiller la cohérence comme indicateur de qualité indépendant.

### La cohérence, indicateur de qualité de la mesure

La cohérence (Cxy = |Pxy|²/(Pxx·Pyy), entre 0 et 1) répond à la question :
"à quel point la sortie enregistrée est-elle expliquée **linéairement** par
la source, à cette fréquence précise ?". Une cohérence proche de 1 signifie
une mesure fiable à cette fréquence ; une cohérence basse peut venir d'un
bruit de fond trop élevé par rapport au signal, d'une non-linéarité
(distorsion) du système mesuré, ou d'un mauvais alignement temporel.

`measure` et `measure-mono` calculent et exportent la cohérence dans le CSV
(colonnes `coherence_L`/`coherence_R` ou `coherence`), et affichent un
avertissement si la cohérence minimale observée descend sous 0.9. Avec un
sweep, cet indicateur reste peu informatif (le sweep ne passe que
brièvement à chaque fréquence, donc une cohérence "moyenne" y est normale
même quand la mesure est bonne). Avec du bruit blanc, en revanche, une
cohérence basse à une fréquence donnée signale un vrai problème de mesure
localisé à cet endroit du spectre.

### La méthode d'inversion des canaux (swap-diff)

Une première façon d'ignorer la coloration de la carte son. En notant ΔD le
déséquilibre du baladeur et ΔM celui de la carte son (tous deux en dB) :

```
Session normale  (baladeur-L→carte-L, baladeur-R→carte-R) : diff1 = ΔD + ΔM
Session inversée (baladeur-L→carte-R, baladeur-R→carte-L) : diff2 = -ΔD + ΔM

  ΔD = (diff1 - diff2) / 2   <- défaut réel du baladeur (ΔM s'annule)
  ΔM = (diff1 + diff2) / 2   <- coloration propre à la carte son (bonus)
```

L'avantage sur une calibration en boucle classique (sortie casque PC →
entrée ligne PC, sans le baladeur) : cette dernière suppose que la carte
son se comporte pareil qu'elle soit alimentée par la sortie casque du PC ou
par celle du baladeur — pas garanti si les deux ont des impédances de
sortie différentes. La méthode par inversion élimine ΔM **algébriquement**,
sans avoir besoin de cette hypothèse.

**Condition de validité** : ΔM (donc les réglages de la carte son) doit
rester strictement identique entre les deux sessions. Ne touche à aucun
réglage de gain entre les deux, fais-les à la suite.

### La méthode mono-diff (recommandée)

Encore plus simple, et encore plus robuste. Au lieu d'utiliser les deux
entrées de la carte son simultanément (comme `swap-diff`), on capture
**un seul canal du baladeur à la fois**, en mono, toujours via **la même
entrée physique** de la carte son :

```
Session A (baladeur-L isolé au câble -> carte-X) : gain_A = D_L + M
Session B (baladeur-R isolé au câble -> carte-X) : gain_B = D_R + M

  ΔD = gain_B - gain_A   <- M s'annule EXACTEMENT, aucune algèbre à deux
                            sessions nécessaire (c'est littéralement le
                            même chemin physique à chaque fois)
```

Avantages sur `swap-diff` :
- Pas de division/moyenne de deux mesures bruitées — soustraction directe.
- Élimine aussi la **diaphonie (crosstalk)** entre les deux voies de la
  carte son captées simultanément, un défaut que `swap-diff` ne peut pas
  éliminer puisqu'il utilise justement les deux voies en même temps.
- Pas besoin d'inverser le câblage côté carte son — seul le pôle de sortie
  du baladeur connecté change entre les deux sessions.

**Isolation pratique du canal** : utilise un câble/adaptateur "breakout"
(sortie casque vers deux jacks mono séparés) pour isoler le pôle gauche ou
droit du baladeur, puis branche uniquement celui à tester sur la carte son.
Capture en mono (1 canal) — pas besoin de "coller" ou combiner quoi que ce
soit, le toolkit traite chaque session indépendamment via `measure-mono` et
les compare directement avec `mono-diff`.

**Même condition de validité que swap-diff** : ne touche à aucun réglage de
la carte son entre les deux sessions, fais-les à la suite.

### Dérive d'horloge entre lecture et enregistrement

Le baladeur (horloge de lecture) et la carte son (horloge d'enregistrement)
sont deux appareils **totalement indépendants**, sans horloge partagée
("word clock"). Même des horloges de bonne qualité dérivent l'une par
rapport à l'autre de l'ordre de quelques dizaines à quelques centaines de
ppm (parties par million). Sur un sweep de 30 secondes, une dérive de
500ppm représente déjà ~15ms de décalage **accumulé progressivement** —
contrairement à un simple délai constant (qui n'affecte que la phase, donc
sans impact sur une mesure de gain), cette dérive désynchronise de plus en
plus le contenu réel du sweep par rapport à ce que la mesure croit
comparer, ce qui dégrade bien le gain mesuré — surtout dans l'aigu.

`measure` et `measure-mono` estiment cette dérive automatiquement (par
corrélation croisée locale en début et fin de signal) et la corrigent par
**rééchantillonnage à bande limitée** avant de calculer la fonction de
transfert. Point technique important : une simple interpolation linéaire ne
suffit pas ici — le sweep contient du contenu proche de la fréquence de
Nyquist (à 18-20kHz avec un échantillonnage à 48kHz, seulement 2-3
échantillons par cycle), qu'une interpolation linéaire déformerait
fortement. Le toolkit utilise `scipy.signal.resample_poly`, qui applique un
filtre anti-repliement correctement dimensionné.

Cette correction est activée par défaut (désactivable avec
`--no-drift-correction`). Elle améliore la précision de la mesure de gain
absolu, mais n'est **pas strictement nécessaire** pour la validité du
déséquilibre L/R en tant que tel, puisque la dérive affecte les deux canaux
d'un même enregistrement de la même façon (même horloge ADC pour L et R).

### Artefacts de bord de sweep, et comment `--f-min`/`--f-max` s'articulent

**Il y a DEUX paires de `--f-min`/`--f-max` distinctes dans ce toolkit,**
qui n'ont pas le même rôle — c'est une source de confusion facile si on ne
les distingue pas clairement :

| Commande | Rôle de `--f-min`/`--f-max` |
|---|---|
| `generate-sweep` | La bande **utile/fiable que tu souhaites obtenir**. Le sweep est en réalité généré un peu plus large que ça (voir marge ci-dessous) — ces valeurs ne sont PAS les bornes réelles du fichier audio généré, ce sont les bornes que tu pourras analyser de façon fiable. |
| `measure` / `measure-mono` | La bande sur laquelle **restreindre l'analyse** de l'enregistrement capturé, pour exclure les portions du sweep peu fiables (voir ci-dessous) ou pour ne garder que la partie du spectre qui t'intéresse. |

**Pourquoi une marge est nécessaire** : tout au bord des limites d'un sweep
(dans son dernier ~1-2%), l'estimation de gain devient franchement peu
fiable — le signal manque de cycles complets pile à la fréquence limite, ce
qui peut produire des erreurs ponctuelles de plusieurs dizaines de dB.
`generate-sweep` génère donc le sweep un peu plus large que la bande utile
demandée (marge de 15% par défaut, ajustable via `--margin-ratio`), pour
que ces artefacts de bord tombent dans la zone "jetable" et non dans la
bande que tu comptes réellement analyser.

**Tu n'as normalement RIEN à faire pour que ça marche correctement.**
`generate-sweep` écrit automatiquement un petit fichier
`<fichier>.wav.meta.json` à côté du sweep généré, contenant la bande utile
demandée. Si tu ne précises PAS `--f-min`/`--f-max` à `measure` ou
`measure-mono`, ces commandes lisent ce fichier automatiquement et
utilisent exactement la bonne bande, sans risque d'oubli ou de
désynchronisation entre les deux commandes. Exemple concret :

```bash
python3 dac_correction_toolkit.py generate-sweep -o sweep.wav --f-min 50 --f-max 15000
# -> génère en réalité ~43-17250Hz, mais écrit sweep.wav.meta.json avec f_min=50, f_max=15000

python3 dac_correction_toolkit.py measure sweep.wav ma_capture.wav -o mesure.csv
# -> lit automatiquement sweep.wav.meta.json, analyse restreinte à 50-15000Hz
# affiche: "Bande analysée: 50.0-15000.0 Hz (métadonnées de sweep.wav.meta.json)"
```

Si tu passes explicitement `--f-min`/`--f-max` à `measure`/`measure-mono`,
cette valeur est **toujours prioritaire** sur le fichier de métadonnées (utile
si tu veux zoomer sur une sous-bande précise après coup, par exemple). Et si
le fichier de métadonnées est absent (sweep généré par un autre outil,
fichier renommé/déplacé séparément de son `.meta.json`...), le toolkit
retombe sur 20-20000 Hz avec un avertissement explicite plutôt que
d'échouer silencieusement.


### Gain fixe vs dépendant du volume

Si le défaut vient d'un simple gain constant (tolérance de composant fixe,
en aval du contrôle de volume), l'écart en dB reste identique quel que soit
le volume d'écoute — un seul profil de correction suffit alors partout.

Si le défaut dépend du volume, plusieurs mécanismes physiques peuvent en
être la cause :
- Erreur de tracking entre les deux voies du contrôle de volume (souvent
  pire aux volumes bas).
- Non-linéarité du DAC (DNL/INL), plus marquée près du zéro.
- Distorsion de croisement (crossover distortion) en classe AB, plus
  perceptible à faible amplitude.
- Calibration indépendante de chaque palier de volume numérique.

Dans ce cas, une correction unique ne sera exacte qu'au volume où elle a
été mesurée — d'où l'intérêt de tester `multivolume` avant de se lancer
dans une correction définitive.

### Lissage fractionnaire d'octave

La courbe de défaut brute contient du bruit de mesure point par point (bruit
de fond, imperfections de l'estimateur, résidus d'alignement temporel). Un
filtre de correction qui suit fidèlement chaque micro-variation de cette
courbe brute sur-ajusterait sur ce bruit plutôt que sur le vrai comportement
du matériel. Le lissage en 1/6 d'octave (par défaut) fait la moyenne des
valeurs proches en fréquence (technique standard en mesure acoustique) pour
ne garder que les tendances larges, probablement réelles.

---

## 5. Pipeline pas à pas

### Étape 0 — Installer les dépendances

```bash
pip install numpy scipy --break-system-packages
# ffmpeg doit déjà être installé (vérifie avec `ffmpeg -version`)
```

### Étape 1 — Générer le signal de test

```bash
python3 dac_correction_toolkit.py generate-sweep -o sweep.wav
```

Options utiles : `--duration 30` (secondes), `--amplitude 0.5` (0-1, marge
anti-écrêtage), `--f-min 20 --f-max 20000` (bande utile souhaitée — ce sont
ces valeurs, pas la bande réellement générée, qui seront reprises
automatiquement à l'étape 4 via le fichier `sweep.wav.meta.json`, voir
[section dédiée](#artefacts-de-bord-de-sweep-et-comment---f-min---f-max-sarticulent)
plus haut).

### Étape 2 — Enregistrer

**Pour mono-diff (recommandé)** : utilise un câble/adaptateur breakout pour
isoler un seul pôle (L ou R) de la sortie du baladeur, branche-le sur ton
entrée d'enregistrement, joue `sweep.wav` (le sweep normal, stéréo, généré à
l'étape 1 — pas besoin d'option spéciale), capture en mono dans
`rec_L_mono.wav`. Répète avec l'autre pôle isolé, **sans changer aucun
réglage de la carte son**, dans `rec_R_mono.wav`.

**Pour swap-diff (alternative)** : câble la sortie casque/4.4mm complète du
baladeur (stéréo) vers l'entrée ligne, joue le sweep, capture en stéréo dans
`rec_normal.wav`. Puis inverse le câblage L/R entre le baladeur et la carte
son (adaptateur inverseur, ou croise les fils), répète dans
`rec_inversee.wav`.

Dans les deux cas : joue à un volume que tu utilises habituellement en
écoute, et vérifie l'absence d'écrêtage (peak level < 0dBFS avec de la
marge).

### Étape 5 — Isoler le défaut réel du baladeur

**Méthode recommandée (mono-diff)** — isole au câble un pôle de sortie du
baladeur à la fois, capture en mono, toujours via la même entrée physique
de la carte son :

```bash
python3 dac_correction_toolkit.py measure-mono sweep.wav rec_L_mono.wav -o mesure_L.csv
python3 dac_correction_toolkit.py measure-mono sweep.wav rec_R_mono.wav -o mesure_R.csv
python3 dac_correction_toolkit.py mono-diff mesure_L.csv mesure_R.csv -o defaut_reel.csv
```

**Méthode alternative (swap-diff)** — si l'isolation au câble n'est pas
pratique, inverse plutôt le câblage L/R côté carte son entre deux sessions
stéréo classiques :

```bash
python3 dac_correction_toolkit.py measure sweep.wav rec_normal.wav -o mesure_normale.csv
python3 dac_correction_toolkit.py measure sweep.wav rec_inversee.wav -o mesure_inversee.csv
python3 dac_correction_toolkit.py swap-diff mesure_normale.csv mesure_inversee.csv -o defaut_reel.csv
```

Les deux méthodes affichent un résumé (moyenne, écart max) directement dans
le terminal. `swap-diff` te signale en plus si la coloration détectée sur
la carte son était significative.

### Étape 6 — (Recommandé) Vérifier la dépendance au volume

Répète les étapes 2 à 5 à plusieurs volumes (ex: 25%, 50%, 75%, 100% du
volume max du baladeur), puis :

```bash
python3 dac_correction_toolkit.py multivolume defaut_v25.csv defaut_v50.csv defaut_v70.csv defaut_v100.csv -o comparaison_volumes.csv
```

Le script te dit si le défaut est un gain fixe ou dépend du volume (voir
[section 4](#gain-fixe-vs-dépendant-du-volume)), avec un seuil ajustable via
`--threshold` (0.3dB par défaut).

### Étape 7 — Construire le profil de correction

```bash
python3 dac_correction_toolkit.py build-eq defaut_reel.csv -o profil_correction.json
```

Options : `--smoothing 6` (1/6 octave par défaut), `--points 40` (nombre de
points dans le filtre ffmpeg final).

### Étape 8 — Appliquer la correction

```bash
# Un seul fichier
python3 dac_correction_toolkit.py apply profil_correction.json morceau.flac -o morceau_corrige.flac

# Tout un dossier
python3 dac_correction_toolkit.py batch-apply profil_correction.json ./musique/ ./musique_corrigee/
```

---

## 6. Interpréter les résultats

Après `swap-diff`, le script affiche par exemple :

```
Défaut baladeur - moyenne: 0.35 dB, max abs: 0.62 dB
Coloration carte son (info) - moyenne: 0.05 dB, max abs: 0.18 dB
```

- **Défaut baladeur** proche de 0dB partout → pas de vrai déséquilibre
  matériel détecté ; la perception initiale pourrait venir d'une asymétrie
  auditive personnelle (l'audition humaine n'est pas parfaitement
  symétrique entre les deux oreilles) plutôt que du matériel.
- **Défaut baladeur** constant sur tout le spectre (ex: +0.4dB partout) →
  déséquilibre de gain global, cohérent avec une tolérance de composant sur
  l'un des DACs ou de l'étage de sortie.
- **Défaut baladeur** qui varie fortement avec la fréquence (ex: correct
  dans le médium mais +1dB dans l'aigu) → pointe plutôt vers un défaut
  localisé (filtre de reconstruction du DAC, réponse de l'ampli casque...)
  que vers un simple offset de gain.

Repère de perception : un écart de l'ordre de **0.3 à 0.5dB** est
généralement considéré comme le seuil approximatif où un déséquilibre
stéréo commence à devenir perceptible dans de bonnes conditions d'écoute —
donne un ordre de grandeur pour juger si ce qui est mesuré est cohérent
avec ce que tu perçois.

---

## 7. Appliquer la correction

Le filtre généré :
- Répartit la correction moitié sur L, moitié sur R (préserve le volume
  global moyen du morceau).
- N'amplifie jamais rien — seulement de l'atténuation relative — pour
  éliminer tout risque d'écrêtage après correction.
- Reste lissé (1/6 octave par défaut) pour éviter de coller au bruit de
  mesure.

Si le défaut s'est révélé **dépendant du volume** à l'étape 6, corrige
spécifiquement à partir de la mesure faite au volume que tu utilises
réellement le plus souvent — une correction "juste à 90%" au bon volume
vaut mieux qu'une correction théoriquement parfaite mesurée à un volume que
tu n'utilises jamais.

---

## 8. Dépannage

| Symptôme | Piste |
|---|---|
| `measure` donne un diff énorme (plusieurs dB) et incohérent | Vérifie l'absence d'écrêtage dans l'enregistrement ; vérifie que la source est bien stéréo (pas de somme accidentelle en mono) |
| Valeurs aberrantes (dizaines de dB) concentrées tout en haut ou en bas de la bande mesurée | Signe que la bande analysée déborde sur la marge de sécurité du sweep — vérifie que le fichier `<sweep>.wav.meta.json` existe bien à côté du sweep utilisé (sinon `measure` retombe sur 20-20000Hz par défaut, qui peut être trop large si tu avais généré une bande personnalisée) |
| Résultats très différents entre deux mesures identiques (répétabilité faible) | Bruit de fond trop élevé ; refais le test dans un environnement plus calme, ou augmente l'amplitude du sweep (avec marge anti-écrêtage) |
| `swap-diff` donne une coloration carte son (`ΔM`) énorme | Un réglage a probablement changé entre les deux sessions (gain d'entrée, AGC réactivé...) ; recommence les deux sessions à la suite sans rien toucher entre les deux |
| `mono-diff` avec `--channel` donne un résultat incohérent (valeurs énormes) | Piège classique de l'approche `--mono-channel` : le canal indiqué doit correspondre exactement à l'index où atterrit le vrai signal dans les DEUX fichiers `measure`. Utilise plutôt `measure-mono` (fichiers vraiment mono), qui élimine ce risque structurellement |
| Le filtre `firequalizer` échoue au lancement de ffmpeg | Vérifie la version de ffmpeg (`ffmpeg -version`) ; certaines compilations minimales n'incluent pas ce filtre |
| Fichier corrigé qui sonne "bizarre"/trop filtré | Augmente `--smoothing` (valeur plus petite = lissage plus large) dans `build-eq`, le profil colle peut-être trop au bruit de mesure |
| `measure` affiche une dérive d'horloge de plusieurs milliers de ppm | Anormalement élevé (au-delà de ~1000-2000ppm, plutôt le signe d'un problème d'alignement que d'une vraie dérive d'horloge) — vérifie l'absence de coupures/décrochages dans l'enregistrement |

---

## 9. Limites de la méthode

- **Mesure électrique, pas perceptive** : ce pipeline mesure un gain
  objectif, pas la perception humaine (qui dépend aussi du contenu du
  morceau, de la fatigue auditive, de l'asymétrie naturelle de l'audition
  entre les deux oreilles).
- **Défauts non-linéaires plus complexes** : la méthode mesure un gain
  linéaire par fréquence. Elle ne capture pas une éventuelle distorsion
  harmonique différente entre les deux canaux (qui demanderait une analyse
  spectrale de la distorsion, hors du périmètre de cet outil).
- **Dépendance au volume non gérée automatiquement** : si le défaut dépend
  du volume, ce toolkit ne construit pas de correction multi-volume
  automatique — il faut choisir un volume de référence à corriger.
- **Le profil corrige le signal numérique en amont** : si le vrai défaut se
  situe après le DAC (résistances de sortie, connecteur, câble interne...),
  la correction reste valide (elle corrige le résultat final mesuré, peu
  importe sa cause), mais elle ne "répare" rien physiquement — c'est un
  correctif logiciel, pas une réparation matérielle.

---

## 10. Référence des commandes

```
generate-sweep    Génère le signal de test (sweep log stéréo, avec marge de sécurité aux bords)
generate-noise    Génère du bruit blanc filtré (alternative au sweep, voir section dédiée)
measure           Calcule le gain par fréquence et par canal (source vs enregistrement stéréo)
measure-mono      Comme `measure` mais pour un enregistrement à 1 seul canal (recommandé pour mono-diff)
diff              Soustrait une calibration en boucle d'une mesure (méthode simple)
swap-diff         Sépare le défaut réel de la coloration carte son par inversion des canaux
mono-diff         Sépare le défaut réel via deux captures mono sur la même entrée carte son (recommandé)
multivolume       Compare plusieurs mesures à différents volumes (gain fixe vs dépendant du volume)
build-eq          Génère le profil de correction EQ lissé (JSON)
apply             Applique la correction à un fichier
batch-apply       Applique la correction à tout un dossier
```

Toutes les commandes de mesure (`measure`, `measure-mono`) corrigent
automatiquement la dérive d'horloge entre lecture et enregistrement
(désactivable avec `--no-drift-correction`).

Aide détaillée de chaque commande : `python3 dac_correction_toolkit.py <commande> -h`
