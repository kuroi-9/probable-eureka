#!/usr/bin/env python3
"""
Compare le gain (en dB) par fréquence entre le canal gauche et le canal droit
d'un enregistrement de la sortie casque d'un baladeur, par rapport au fichier
source original (le sweep envoyé).

Nécessite : numpy, scipy
    pip install numpy scipy --break-system-packages

Usage :
    python3 analyse_gain_frequence.py source.flac recording.wav

Le fichier source doit être STÉRÉO avec le MÊME signal sur les deux canaux
(généré par exemple avec le sweep ffmpeg fourni). L'enregistrement doit être
la capture de la sortie casque du baladeur (stéréo, même durée ou plus longue).
"""

import sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample

def load_audio(path):
    """Charge un wav ou flac en float32, retourne (samplerate, data[n_samples, n_channels])."""
    if path.lower().endswith(".flac"):
        import soundfile as sf
        data, sr = sf.read(path, always_2d=True, dtype="float32")
    else:
        sr, data = wavfile.read(path)
        if data.dtype != np.float32:
            data = data.astype(np.float32) / np.iinfo(data.dtype).max
        if data.ndim == 1:
            data = data[:, None]
    return sr, data

def align(source, recording):
    """Aligne temporellement l'enregistrement sur la source via corrélation croisée
    (utilise la moyenne des canaux pour l'estimation du délai)."""
    s = source.mean(axis=1)
    r = recording.mean(axis=1)
    corr = correlate(r, s, mode="full")
    lag = corr.argmax() - (len(s) - 1)
    if lag >= 0:
        recording = recording[lag:]
    else:
        source = source[-lag:]
    n = min(len(source), len(recording))
    return source[:n], recording[:n]

def transfer_function(src_ch, rec_ch, sr, n_fft=65536):
    """Fonction de transfert H(f) = FFT(rec)/FFT(src) moyennée par blocs (Welch-like)."""
    hop = n_fft // 2
    window = np.hanning(n_fft)
    n = min(len(src_ch), len(rec_ch))
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    num = np.zeros(len(freqs), dtype=np.complex128)
    den = np.zeros(len(freqs), dtype=np.complex128)
    count = 0
    for start in range(0, n - n_fft, hop):
        s = src_ch[start:start + n_fft] * window
        r = rec_ch[start:start + n_fft] * window
        S = np.fft.rfft(s)
        R = np.fft.rfft(r)
        num += R * np.conj(S)   # cross-spectrum
        den += S * np.conj(S)   # auto-spectrum source (H1 estimator)
        count += 1
    den[den == 0] = 1e-20
    H = num / den
    return freqs, H

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 analyse_gain_frequence.py source.flac recording.wav")
        sys.exit(1)

    src_path, rec_path = sys.argv[1], sys.argv[2]
    sr_src, source = load_audio(src_path)
    sr_rec, recording = load_audio(rec_path)

    if sr_src != sr_rec:
        n_target = int(len(recording) * sr_src / sr_rec)
        recording = resample(recording, n_target)
        sr_rec = sr_src

    source, recording = align(source, recording)

    if source.shape[1] < 2 or recording.shape[1] < 2:
        print("Erreur: source et enregistrement doivent être stéréo (2 canaux).")
        sys.exit(1)

    freqs_L, H_L = transfer_function(source[:, 0], recording[:, 0], sr_src)
    freqs_R, H_R = transfer_function(source[:, 1], recording[:, 1], sr_src)

    gain_L_dB = 20 * np.log10(np.abs(H_L) + 1e-20)
    gain_R_dB = 20 * np.log10(np.abs(H_R) + 1e-20)
    diff_dB = gain_R_dB - gain_L_dB

    # Ne garde que la bande utile (20 Hz - 20 kHz) pour l'affichage
    mask = (freqs_L >= 20) & (freqs_L <= 20000)

    print(f"{'Fréquence (Hz)':>15} | {'Gain L (dB)':>12} | {'Gain R (dB)':>12} | {'Diff R-L (dB)':>14}")
    print("-" * 62)
    # échantillonnage log pour un affichage lisible (une ligne par tiers d'octave environ)
    freqs_to_show = np.geomspace(20, 20000, 40)
    for f_target in freqs_to_show:
        idx = np.argmin(np.abs(freqs_L[mask] - f_target))
        real_idx = np.where(mask)[0][idx]
        print(f"{freqs_L[real_idx]:15.1f} | {gain_L_dB[real_idx]:12.2f} | "
              f"{gain_R_dB[real_idx]:12.2f} | {diff_dB[real_idx]:14.2f}")

    print("\nRésumé:")
    print(f"  Différence moyenne R-L sur 20Hz-20kHz : {diff_dB[mask].mean():.3f} dB")
    print(f"  Écart-type de la différence          : {diff_dB[mask].std():.3f} dB")
    print(f"  Différence max (valeur absolue)      : {np.abs(diff_dB[mask]).max():.3f} dB "
          f"à {freqs_L[mask][np.argmax(np.abs(diff_dB[mask]))]:.0f} Hz")

    # Sauvegarde CSV pour tracer un graphe si besoin
    out_csv = "gain_par_frequence.csv"
    with open(out_csv, "w") as f:
        f.write("freq_hz,gain_L_dB,gain_R_dB,diff_R_moins_L_dB\n")
        for i in np.where(mask)[0]:
            f.write(f"{freqs_L[i]:.2f},{gain_L_dB[i]:.4f},{gain_R_dB[i]:.4f},{diff_dB[i]:.4f}\n")
    print(f"\nDonnées complètes exportées dans: {out_csv}")

if __name__ == "__main__":
    main()
