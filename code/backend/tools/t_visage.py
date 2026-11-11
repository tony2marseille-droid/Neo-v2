# -*- coding: utf-8 -*-
"""Detection de visages locale (OpenCV DNN YuNet) : compte, boites, image annotee.

Aucun envoi reseau : le modele ONNX est lu sur disque. Utilise par la route
Rust /api/vision/faces et par l'outil detect_faces du chat.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
from backend.core import reglages

# Emplacements connus du modele YuNet (232 Ko, OpenCV Zoo).
_MODELES = (
    reglages.chemin("NEO_YUNET", "models", "face_detection_yunet_2023mar.onnx"),
    Path(__file__).resolve().parents[2] / "models" / "face_detection_yunet_2023mar.onnx",
)


def _modele() -> Path | None:
    for p in _MODELES:
        if p.is_file():
            return p
    return None


def tool_detect_faces(image_path: str, save_path: str = "") -> str:
    """Detecte les visages d'une image et ecrit une copie annotee.

    Retourne un JSON : {ok, count, faces:[{x,y,w,h,score}], annotated_path}.
    """
    p = Path(image_path)
    if not p.is_file():
        return f"Erreur: image introuvable {image_path}"
    modele = _modele()
    if modele is None or not hasattr(cv2, "FaceDetectorYN_create"):
        return ("Erreur: detecteur de visages indisponible "
                "(modele YuNet ou OpenCV DNN manquant).")
    img = cv2.imread(str(p))
    if img is None:
        return f"Erreur: image illisible {image_path}"

    h, w = img.shape[:2]
    # YuNet est plus rapide sur une image reduite ; on remet les boites a l'echelle.
    echelle = min(1.0, 1280.0 / max(w, h))
    if echelle < 1.0:
        petit = cv2.resize(img, (max(1, int(w * echelle)), max(1, int(h * echelle))))
    else:
        petit = img
    ph, pw = petit.shape[:2]

    try:
        detecteur = cv2.FaceDetectorYN_create(str(modele), "", (pw, ph), 0.6, 0.3, 5000)
        _, visages = detecteur.detect(petit)
    except Exception as exc:  # noqa: BLE001
        return f"Erreur: detection impossible ({exc})"

    boites: list[dict[str, float]] = []
    if visages is not None:
        for v in visages:
            x, y, bw, bh = (float(t) / echelle for t in v[:4])
            score = float(v[-1])
            boites.append({
                "x": round(x, 1), "y": round(y, 1),
                "w": round(bw, 1), "h": round(bh, 1),
                "score": round(score, 3),
            })

    annote = img.copy()
    epaisseur = max(2, w // 400)
    taille = max(0.5, w / 1600.0)
    for b in boites:
        x, y, bw, bh = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
        cv2.rectangle(annote, (x, y), (x + bw, y + bh), (0, 255, 0), epaisseur)
        cv2.putText(annote, f"{b['score'] * 100:.0f}%", (x, max(18, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, taille, (0, 255, 0), epaisseur, cv2.LINE_AA)

    dst = Path(save_path) if save_path else p.with_name(f"{p.stem}_visages.jpg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst), annote, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
        return "Erreur: impossible d'ecrire l'image annotee"

    return json.dumps({
        "ok": True,
        "count": len(boites),
        "faces": boites,
        "annotated_path": str(dst),
        "model": str(modele),
    }, ensure_ascii=False)


if __name__ == "__main__":
    import sys

    print(tool_detect_faces(sys.argv[1]) if len(sys.argv) > 1 else "usage : python t_visage.py <image>")
