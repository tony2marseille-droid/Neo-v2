import asyncio
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import settings

router = APIRouter(prefix="/api/finetune", tags=["finetune"])

ATELIER = settings.data_dir / "lora_atelier"
VENV_PYTHON = settings.root_dir / ".venv" / "Scripts" / "python.exe"
SFT_FILE = ATELIER / "sft.jsonl"
RLVR_FILE = ATELIER / "sft_rlvr.jsonl"
MANIFEST_FILE = ATELIER / "manifest.json"
TRAIN_SCRIPT = ATELIER / "train_lora.py"
FINETUNED_DIR = ATELIER / "localai_finetuned"
GGUF_FILE = ATELIER / "localai_prog_v2.gguf"
MODELFILE = ATELIER / "Modelfile-v2"

# Famille des modeles issus du fine-tuning. Les artefacts historiques s'appellent
# "localai-prog-*" ; le renommage produit en Oracle ne doit pas faire croire que
# le modele n'est pas importe : on reconnait les deux prefixes.
PREFIXES_PROG = ("localai-prog", "oracle-prog")
FAMILLE_PROG = os.environ.get("NEO_PROG_FAMILLE", "localai-prog")


class DatasetEntry(BaseModel):
    messages: list[dict]
    category: str = ""


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _extract_categories(entries: list[dict]) -> list[str]:
    categories = Counter()
    for entry in entries:
        cat = entry.get("category", entry.get("theme", ""))
        if not cat:
            msgs = entry.get("messages", [])
            if msgs and msgs[0].get("role") == "user":
                first_words = msgs[0]["content"].split()[:5]
                cat = " ".join(first_words) if first_words else "unknown"
        categories[cat] += 1
    return [f"{c} ({n})" for c, n in categories.most_common()]


def _detect_gpu() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = out.stdout.strip().splitlines()[0]
        name, vram = [p.strip() for p in line.split(",")]
        vram_mb = int(vram)
        tier = "T3" if vram_mb >= 22000 else "T2" if vram_mb >= 10000 else "T1"
        return {"gpu_name": name, "vram_mb": vram_mb, "tier": tier}
    except Exception:
        return {"gpu_name": None, "vram_mb": 0, "tier": "T1"}


def _ollama_list() -> list[str]:
    try:
        exe = os.environ.get("OLLAMA_BIN") or ""
        if not exe:
            import glob

            lecteur = os.environ.get("SystemDrive", "C:")
            # Tous les profils : Ollama est installe dans celui de l'utilisateur,
            # le service tourne sous un autre compte.
            for cand in sorted(glob.glob(lecteur + r"\Users\*\AppData\Local\Programs\Ollama\ollama.exe")) + [
                lecteur + r"\Program Files\Ollama\ollama.exe",
            ]:
                if os.path.exists(cand):
                    exe = cand
                    break
        if not exe:
            exe = "ollama"
        out = subprocess.run(
            [exe, "list"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [line.split()[0] for line in out.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []


def _read_manifest() -> dict | None:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _find_gguf_converter() -> Path | None:
    candidates = [
        settings.root_dir / "convert_hf_to_gguf.py",
        *([Path(os.environ["HF_HOME"]) / "convert_hf_to_gguf.py"] if os.environ.get("HF_HOME") else []),
        Path(os.environ.get("TEMP", "")) / "convert_hf_to_gguf.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _dossier_finetune() -> Path | None:
    """Dernier modele fusionne (localai_finetuned[_vX]) contenant un config.json."""
    candidats = []
    if FINETUNED_DIR.exists():
        candidats.append(FINETUNED_DIR)
    try:
        candidats += [
            p for p in ATELIER.glob("localai_finetuned*")
            if p.is_dir() and p != FINETUNED_DIR and (p / "config.json").exists()
        ]
    except Exception:
        pass
    if not candidats:
        return None
    return max(candidats, key=lambda p: p.stat().st_mtime)


def _gguf_le_plus_recent() -> Path | None:
    """GGUF deja converti le plus recent, quel que soit le prefixe localai/oracle."""
    try:
        cands = [
            p for p in settings.root_dir.glob("*-v*-q8_0.gguf")
            if p.is_file() and any(p.name.startswith(pfx) for pfx in ("localai-v", "oracle-v"))
        ]
        if cands:
            return max(cands, key=lambda p: p.stat().st_mtime)
    except Exception:
        pass
    if GGUF_FILE.exists():
        return GGUF_FILE
    return None


def _est_modele_prog(nom: str) -> bool:
    return any(nom.startswith(pfx) for pfx in PREFIXES_PROG)


def _rang_prog(nom: str) -> tuple[int, int]:
    """Version la plus haute, puis variante pleine precision (q8) avant q4/q5."""
    chiffres = re.findall(r"(\d+)", nom)
    version = int(chiffres[0]) if chiffres else 0
    quantifie = 0 if re.search(r"q[45]", nom) else 1
    return (version, quantifie)


@router.get("/status")
async def get_status() -> dict:
    try:
        sft_entries = _read_jsonl(SFT_FILE)
        rlvr_entries = _read_jsonl(RLVR_FILE)
        manifest = _read_manifest()
        gguf_ref = _gguf_le_plus_recent()
        gguf_exists = gguf_ref is not None
        gguf_size_mb = round(gguf_ref.stat().st_size / (1024 * 1024)) if gguf_ref else 0
        models = _ollama_list()
        hw = _detect_gpu()
        progs = [m for m in models if _est_modele_prog(m)]
        ollama_tag = max(progs, key=_rang_prog) if progs else ""

        try:
            from backend.core.rlvr import TACHES
            templates_count = len(TACHES)
        except Exception:
            templates_count = 0

        return {
            "dataset": {
                "count": len(sft_entries),
                "sft_count": len(sft_entries),
                "rlvr_count": len(rlvr_entries),
                "total": len(sft_entries) + len(rlvr_entries),
                "topics": _extract_categories(sft_entries),
            },
            "rlvr_dataset": {
                "count": len(rlvr_entries),
            },
            "training": {
                "available": TRAIN_SCRIPT.exists(),
                "last_run": manifest.get("genere_le") if manifest else None,
                "base_model": manifest.get("config", {}).get("base", "Qwen/Qwen3-4B") if manifest else "Qwen/Qwen3-4B",
                "epochs": manifest.get("config", {}).get("epochs", 2) if manifest else 2,
                "lora_rank": manifest.get("config", {}).get("lora_r", 16) if manifest else 16,
            },
            "model": {
                "gguf_exists": gguf_exists,
                "gguf_size_mb": gguf_size_mb,
                "gguf_size_gb": round(gguf_size_mb / 1024, 2),
                "gguf_path": str(gguf_ref) if gguf_ref else "",
                "ollama_imported": bool(progs),
                "ollama_tag": ollama_tag,
                "finetuned_dir_exists": _dossier_finetune() is not None,
            },
            "hardware": {**hw, "vram_gb": round(hw.get("vram_mb", 0) / 1024, 1)},
            "rlvr": {
                "templates_count": templates_count,
                "last_run": manifest.get("genere_le") if manifest else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dataset")
async def get_dataset() -> dict:
    try:
        return {"entries": _read_jsonl(SFT_FILE)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dataset")
async def add_to_dataset(entry: DatasetEntry) -> dict:
    try:
        entries = _read_jsonl(SFT_FILE)
        new_entry = {
            "messages": entry.messages,
            "category": entry.category,
        }
        entries.append(new_entry)
        _write_jsonl(SFT_FILE, entries)
        return {"count": len(entries), "added": new_entry}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/dataset/{index}")
async def delete_from_dataset(index: int) -> dict:
    try:
        entries = _read_jsonl(SFT_FILE)
        if index < 0 or index >= len(entries):
            raise HTTPException(status_code=404, detail=f"Index {index} hors limites (0-{len(entries)-1})")
        removed = entries.pop(index)
        _write_jsonl(SFT_FILE, entries)
        return {"count": len(entries), "removed": removed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RlvrRequest(BaseModel):
    n_cibles: int = 200


@router.post("/rlvr/run")
async def run_rlvr(req: RlvrRequest = RlvrRequest()) -> dict:
    async def _run():
        try:
            await asyncio.to_thread(_run_rlvr_sync)
        except Exception as e:
            print(f"[RLVR ERROR] {e}")

    def _run_rlvr_sync():
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run(
            [str(VENV_PYTHON), "-c",
             f"from backend.core.rlvr import generer; generer(n_cibles={req.n_cibles})"],
            cwd=str(settings.root_dir),
            env=env,
            timeout=1200,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        asyncio.create_task(_run())
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rlvr/status")
async def rlvr_status() -> dict:
    try:
        entries = _read_jsonl(RLVR_FILE)
        validated = [e for e in entries if e.get("verifie")]
        manifest = _read_manifest()
        return {
            "total": len(entries),
            "validated": len(validated),
            "last_run": manifest.get("genere_le") if manifest else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/train")
async def start_training() -> dict:
    async def _run():
        try:
            await asyncio.to_thread(_run_training_sync)
        except Exception as e:
            print(f"[TRAIN ERROR] {e}")

    def _run_training_sync():
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Cache Hugging Face : reglage chemins.cache_hf de neo.toml (HF_HOME).
        if os.environ.get("HF_HOME"):
            env["HF_HOME"] = os.environ["HF_HOME"]
        if VENV_PYTHON.exists():
            env["PATH"] = str(VENV_PYTHON.parent) + os.pathsep + env.get("PATH", "")
        subprocess.run(
            [str(VENV_PYTHON), str(TRAIN_SCRIPT)],
            cwd=str(ATELIER),
            env=env,
            timeout=21600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        if not TRAIN_SCRIPT.exists():
            raise HTTPException(status_code=404, detail="train_lora.py introuvable")
        task = asyncio.create_task(_run())
        return {"status": "started", "pid": id(task)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/train/status")
async def training_status() -> dict:
    try:
        manifest = _read_manifest()
        sortie_dir = ATELIER / "sortie"
        return {
            "manifest": manifest,
            "sortie_exists": sortie_dir.exists(),
            "finetuned_exists": FINETUNED_DIR.exists(),
            "last_run": manifest.get("genere_le") if manifest else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/convert")
async def convert_to_gguf() -> dict:
    async def _run():
        try:
            await asyncio.to_thread(_convert_sync)
        except Exception as e:
            print(f"[CONVERT ERROR] {e}")

    def _convert_sync():
        source = _dossier_finetune()
        if source is None:
            raise RuntimeError("aucun modele fusionne localai_finetuned*/ - lance l'entrainement d'abord")
        converter = _find_gguf_converter()
        if not converter:
            raise RuntimeError("convert_hf_to_gguf.py introuvable")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run(
            [str(VENV_PYTHON), str(converter), str(source), "--outfile", str(GGUF_FILE), "--outtype", "q8_0"],
            cwd=str(settings.root_dir),
            env=env,
            timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        source = _dossier_finetune()
        if source is None:
            raise HTTPException(status_code=404, detail="Aucun modele fusionne localai_finetuned*/ introuvable")
        converter = _find_gguf_converter()
        if not converter:
            raise HTTPException(status_code=404, detail="convert_hf_to_gguf.py introuvable")
        asyncio.create_task(_run())
        return {"status": "started", "converter": str(converter), "source": source.name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import-ollama")
async def import_to_ollama() -> dict:
    async def _run():
        try:
            await asyncio.to_thread(_import_sync)
        except Exception as e:
            print(f"[OLLAMA IMPORT ERROR] {e}")

    def _import_sync():
        if not GGUF_FILE.exists():
            raise RuntimeError("localai_prog_v2.gguf introuvable")
        if not MODELFILE.exists():
            MODELFILE.write_text(f"FROM ./{GGUF_FILE.name}\nPARAMETER temperature 0.7\n", encoding="utf-8")
        subprocess.run(
            ["ollama", "create", f"{FAMILLE_PROG}-v2", "-f", str(MODELFILE)],
            cwd=str(ATELIER),
            timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        if not GGUF_FILE.exists():
            raise HTTPException(status_code=404, detail="localai_prog_v2.gguf introuvable - convertis d'abord")
        asyncio.create_task(_run())
        return {"status": "started", "model": f"{FAMILLE_PROG}-v2"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/merge")
async def merge_datasets() -> dict:
    try:
        sft_entries = _read_jsonl(SFT_FILE)
        rlvr_entries = _read_jsonl(RLVR_FILE)
        seen: set[str] = set()
        merged: list[dict] = []
        for entry in sft_entries + rlvr_entries:
            key = json.dumps(entry.get("messages", []), ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                merged.append(entry)
        _write_jsonl(SFT_FILE, merged)
        return {
            "total_before_sft": len(sft_entries),
            "total_rlvr": len(rlvr_entries),
            "merged_count": len(merged),
            "duplicates_removed": len(sft_entries) + len(rlvr_entries) - len(merged),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
