#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Cli pipeline LocalAI - encapsule entraînement, fusion GGUF, conversion f16->q8_0
# et déploiement Ollama en sous-commandes réutilisables.
#
# Usage:
#   python pipeline.py train --epochs 3 --rank 16 --ckpt 100
#   python pipeline.py merge-gguf --ckpt 300 --out localai-v7-q8_0.gguf
#   python pipeline.py convert --hf localai_finetuned_v7 --tag v7
#   python pipeline.py deploy --tag v7 --modelfile Modelfile_v7
#   python pipeline.py (affichage de l'aide)
#
# Tous les chemins et outils sont configurables via options ou variables d'env
# dont les valeurs par défaut correspondent à la pipeline v6 existante.

import argparse
import json
import os
import shutil
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Chemins par défaut (identiques à la pipeline v6 en production)
# ---------------------------------------------------------------------------
ROOT = Path(os.environ.get("LOCALAI_ROOT", "E:/ia"))
ATELIER = ROOT / "data" / "lora_atelier"
VENV = ROOT / ".venv" / "Scripts" / "python.exe"
LOGS = ROOT / "logs"
HF_HOME = os.environ.get("HF_HOME", "E:/huggingface_cache")
OLLAMA_URL = "http://127.0.0.1:11434"

# Outils conversion / quantification
CONVERT_HF = os.environ.get(
    "CONVERT_HF", "E:/ia/llama-cpp-src/convert_hf_to_gguf.py"
)
QUANTIZE_EXE = os.environ.get(
    "QUANTIZE_EXE", "E:/ia/llamacpp/llama-quantize.exe"
)

# Base modèle
BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen3-8B")
BASE_4BIT = os.environ.get("BASE_4BIT", "unsloth/qwen3-8b-unsloth-bnb-4bit")

# Binaire ollama (peut être hors PATH pour une tâche SYSTEM -> cherche
# dans les emplacements usuels du profil utilisateur).
def _find_ollama():
    if os.environ.get("OLLAMA_BIN"):
        return os.environ["OLLAMA_BIN"]
    import glob

    lecteur = os.environ.get("SystemDrive", "C:")
    # Ollama s'installe dans le profil de celui qui l'a installe : on regarde
    # tous les profils (une tache SYSTEM n'a pas le sien).
    for _cand in sorted(glob.glob(lecteur + r"\Users\*\AppData\Local\Programs\Ollama\ollama.exe")) + [
        lecteur + r"\Program Files\Ollama\ollama.exe",
    ]:
        if os.path.exists(_cand):
            return _cand
    return "ollama"

# cibles LoRA (identiques à train_lora_v6.py / merge_ckpt300.py)
TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]
SYSTEM_PROMPT = (
    "Tu es un assistant expert en auto-hebergement d'IA et en LocalAI. "
    "Tu reponds en francais, de maniere technique, complete et structuree. "
    "Quand c'est utile, donne des etapes, commandes ou exemples de code."
)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def log(msg, logfile=None):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def unload_ollama(url=OLLAMA_URL):
    """Décharge les modèles Ollama chargés (libère la VRAM/RAM avant entrainement)."""
    try:
        import httpx
        with httpx.Client(timeout=15) as client:
            ps = client.get(f"{url}/api/ps").json()
            for m in ps.get("models", []):
                tag = m.get("name") or m.get("model")
                if tag:
                    client.post(f"{url}/api/generate",
                                json={"model": tag, "keep_alive": 0}, timeout=15)
        log("Ollama models unloaded")
        return True
    except Exception as exc:
        log(f"unload_ollama warn: {exc!r}")
        return False


# Dossiers git/outils éventuellement hors du PATH (git installé par 'hermes'
# dans le profil utilisateur -> invisible pour les tâches SYSTEM). On les
# préfixe au PATH pour que unsloth trouve git lors de l'auto-install llama.cpp.
GIT_BIN = os.environ.get("GIT_BIN")
if not GIT_BIN:
    import glob as _glob

    for _cand in sorted(_glob.glob(os.environ.get("SystemDrive", "C:") + r"\Users\*\AppData\Local\hermes\git\bin")) + [
        r"C:\Program Files\Git\bin",
        r"C:\Program Files\Git\cmd",
    ]:
        if os.path.exists(os.path.join(_cand, "git.exe")):
            GIT_BIN = _cand
            break


def build_env(env_extra=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HOME"] = HF_HOME
    if GIT_BIN:
        env["PATH"] = GIT_BIN + os.pathsep + env.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    return env


def _dump(tail, logfile, maxchars=300000):
    if not logfile or not tail or not tail.strip():
        return
    try:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open("a", encoding="utf-8", errors="replace") as f:
            f.write("\n---- sortie/erreur du sous-processus (fin, dernieres %d car.) ----\n" % maxchars)
            f.write(tail[-maxchars:])
            f.write("\n---- fin ----\n")
    except Exception:
        pass


def pyscript(script_path, cwd, env_extra=None, timeout=57600, logfile=None):
    """Lance un script python via le venv.

    - flue la sortie en DIRECT dans <logfile>.stdout (stream temps reel) au
      lieu de la garder captive (observabilite pendant l'execution).
    - garde les dernieres ~300 ko en memoire pour dump en cas d'echec.
    - renvoie le returncode.
    """
    env = build_env(env_extra)
    cmd = [str(VENV), "-u", str(script_path)]
    stream = None
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        stream = logfile.with_name(logfile.name + ".stdout")

    def _stream(chunk):
        if stream:
            try:
                with stream.open("a", encoding="utf-8", errors="replace") as f:
                    f.write(chunk)
            except Exception:
                pass

    tail = ""
    rc = 1
    try:
        log(f"RUN: {' '.join(cmd)} (cwd={cwd})", logfile)
        p = subprocess.Popen(cmd, cwd=str(cwd), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        q = queue.Queue()

        def _reader():
            try:
                for chunk in iter(p.stdout.readline, ""):
                    q.put(chunk)
            finally:
                q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        deadline = time.time() + timeout
        timed_out = False
        while True:
            try:
                remain = deadline - time.time()
                if remain <= 0:
                    timed_out = True
                    break
                chunk = q.get(timeout=min(remain, 2.0))
                if chunk is None:
                    break
                tail += chunk
                tail = tail[-300000:]
                _stream(chunk)
            except queue.Empty:
                continue

        if timed_out:
            try:
                p.kill()
            except Exception:
                pass
            log(f"TIMEOUT apres {timeout}s - checkpoints/artefacts dispo", logfile)
            _dump(tail, logfile)
            return 1

        rc = p.wait()
        log(f"EXIT={rc}", logfile)
        if rc != 0:
            _dump(tail, logfile)
        return rc
    except subprocess.TimeoutExpired as exc:
        tail = (exc.stdout or "") + "\n" + (exc.stderr or "")
        log(f"TIMEOUT apres {timeout}s - checkpoints/artefacts dispo", logfile)
        _dump(tail, logfile)
        return 1
    except Exception as exc:
        log(f"ERROR: {exc!r}", logfile)
        return 1


# ---------------------------------------------------------------------------
# Sous-commande: train
# ---------------------------------------------------------------------------
def cmd_train(args):
    logfile = LOGS / f"train_{args.tag}.log"

    # Gate chantier 5 (2026-10-17) : un corpus v93 ne part en train QUE si
    # corpus_stats_v93.json est present (statut OK) et sans fuite holdout.
    if "v93" in Path(args.dataset).stem:
        stats = ATELIER / "corpus_stats_v93.json"
        if not stats.exists():
            log("GATE v93: corpus_stats_v93.json absent -> train refuse", logfile)
            sys.exit(4)
        try:
            st93 = json.loads(stats.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"GATE v93: stats illisibles ({exc}) -> train refuse", logfile)
            sys.exit(4)
        if st93.get("statut") != "OK":
            log("GATE v93: statut != OK -> train refuse", logfile)
            sys.exit(4)
        leak = ROOT / "eval" / "leak_report.json"
        if leak.exists():
            try:
                lr = json.loads(leak.read_text(encoding="utf-8"))
            except Exception:
                lr = {}
            if len(lr.get("leaks") or []) > 0:
                log("GATE v93: fuite holdout dans leak_report.json -> train refuse", logfile)
                sys.exit(4)
        log("GATE v93: OK (stats + leak_report)", logfile)

    # purge sortie pour départ propre (sauf si --resume)
    sortie = ATELIER / f"sortie_{args.tag}"
    if sortie.exists() and not args.resume:
        shutil.rmtree(sortie, ignore_errors=True)
        log(f"purge {sortie}", logfile)
    sortie.mkdir(parents=True, exist_ok=True)

    if not args.no_unload:
        unload_ollama(OLLAMA_URL)

    fusion = args.fusion or f"localai_finetuned_{args.tag}"

    # Génère un script d'entraînement paramétré dérivé de train_lora_v6.py
    # (qui a des chemins en dur -> on injecte les valeurs de la sous-commande).
    script = LOGS / f"_train_{args.tag}.py"
    metrics_path = str(LOGS / f"train_{args.tag}_metrics.jsonl")
    modules_repr = repr(TARGET_MODULES)
    body = (
        "# -*- coding: utf-8 -*-\n"
        "import json, os, sys, time\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        f"BASE_MODEL = {args.base!r}\n"
        f"MAX_SEQ = {int(args.max_seq)}\n"
        f"EPOCHS = {int(args.epochs)}\n"
        f"BATCH = {int(args.batch)}\n"
        f"ACCUM = {int(args.accum)}\n"
        f"R = {int(args.rank)}\n"
        f"SAVE_STEPS = {int(args.ckpt)}\n"
        f"ATELIER = Path({str(ATELIER)!r})\n"
        f"DATASET = ATELIER / {args.dataset!r}\n"
        f"SORTIE = Path({str(sortie)!r})\n"
        f"FUSION = {fusion!r}\n"
        f"SYSTEM_PROMPT = {SYSTEM_PROMPT!r}\n"
        f"RESUME = {1 if args.resume else 0}\n"
        f"TRAIN_LOCK = {str(LOGS / f'training_{args.tag}.lock')!r}\n"
        f"METRICS_PATH = {metrics_path!r}\n"
        "try:\n"
        "    _fd=os.open(str(TRAIN_LOCK), os.O_CREAT|os.O_EXCL|os.O_WRONLY)\n"
        "    os.write(_fd,str(os.getpid()).encode()); os.close(_fd)\n"
        "except FileExistsError:\n"
        "    _o=None\n"
        "    try: _o=int(open(TRAIN_LOCK).read().strip())\n"
        "    except Exception: _o=None\n"
        "    _alive=False\n"
        "    if _o:\n"
        "        try:\n"
        "            os.kill(_o,0); _alive=True\n"
        "        except ProcessLookupError: _alive=False\n"
        "        except OSError: _alive=False\n"
        "        except Exception: _alive=True\n"
        "    if _alive:\n"
        "        print(f'Training deja en cours PID {_o} - skip',flush=True); sys.exit(0)\n"
        "    try: os.remove(str(TRAIN_LOCK))\n"
        "    except: pass\n"
        "    _fd=os.open(str(TRAIN_LOCK), os.O_CREAT|os.O_EXCL|os.O_WRONLY)\n"
        "    os.write(_fd,str(os.getpid()).encode()); os.close(_fd)\n"
        "print('Lock training acquis',flush=True)\n"
        "import atexit\n"
        "def _rmlock():\n"
        "    try:\n"
        "        os.remove(str(TRAIN_LOCK))\n"
        "    except Exception:\n"
        "        pass\n"
        "atexit.register(_rmlock)\n"
        "try:\n"
        "    from triton.compiler.compiler import AttrsDescriptor  # noqa\n"
        "except ImportError:\n"
        "    import triton.compiler.compiler as _tc\n"
        "    class _AttrsDescriptorShim: pass\n"
        "    _tc.AttrsDescriptor = _AttrsDescriptorShim\n"
        "from unsloth import FastLanguageModel\n"
        "def charger(f):\n"
        "    ex=[]; p=ATELIER/f\n"
        "    try:\n"
        "        with open(p, encoding='utf-8') as fh:\n"
        "            for l in fh:\n"
        "                if l.strip(): ex.append(json.loads(l))\n"
        "    except FileNotFoundError: pass\n"
        "    return ex\n"
        "all_data=charger(DATASET.name)\n"
        "if not all_data: print('Dataset vide - rejet', flush=True); sys.exit(2)\n"
        "print(f'Dataset: {len(all_data)} exemples', flush=True)\n"
        "if os.environ.get('TRAIN_SMOKE')=='1':\n"
        "    print(f'SMOKE OK: dataset={len(all_data)} base={BASE_MODEL} r={R} epochs={EPOCHS} accum={ACCUM} max_seq={MAX_SEQ} save_steps={SAVE_STEPS} resume={RESUME} sortie={SORTIE} fusion={FUSION}', flush=True)\n"
        "    sys.exit(0)\n"
        "model, tokenizer = FastLanguageModel.from_pretrained(BASE_MODEL, max_seq_length=MAX_SEQ, load_in_4bit=True)\n"
        "model = FastLanguageModel.get_peft_model(\n"
        "    model, r=R,\n"
        f"    target_modules={modules_repr},\n"
        "    lora_alpha=R*2, lora_dropout=0.05, bias='none', use_gradient_checkpointing='unsloth')\n"
        "import torch\n"
        "from torch.utils.data import Dataset\n"
        "def formater(ex):\n"
        "    msgs=ex.get('messages') or [{'role':'user','content':ex.get('instruction','')},{'role':'assistant','content':ex.get('output','')}]\n"
        "    if not any(m.get('role')=='system' for m in msgs):\n"
        "        msgs=[{'role':'system','content':SYSTEM_PROMPT}]+msgs\n"
        "    return tokenizer.apply_chat_template(msgs, tokenize=False)\n"
        "class TextDataset(Dataset):\n"
        "    def __init__(self, texts, tokenizer, max_len):\n"
        "        self.enc=[]\n"
        "        for t in texts:\n"
        "            e=tokenizer(t, truncation=True, max_length=max_len, padding='max_length', return_tensors='pt')\n"
        "            ids=e['input_ids'].squeeze(0); mask=e['attention_mask'].squeeze(0)\n"
        "            lab=ids.clone(); lab[mask==0]=-100\n"
        "            self.enc.append({'input_ids':ids,'attention_mask':mask,'labels':lab})\n"
        "    def __len__(self): return len(self.enc)\n"
        "    def __getitem__(self,i): return self.enc[i]\n"
        "dataset=TextDataset([formater(e) for e in all_data], tokenizer, MAX_SEQ)\n"
        "METRICS_T0=time.time()\n"
        "from transformers import TrainingArguments, TrainerCallback\n"
        "class _MetricsCallback(TrainerCallback):\n"
        "    def on_log(self, args, state, control, logs=None, **kwargs):\n"
        "        try:\n"
        "            _row={'step': state.global_step, 'epoch': round(state.epoch,4), 'elapsed_s': round(time.time()-METRICS_T0,1)}\n"
        "            for _k in ('loss','learning_rate'):\n"
        "                if (logs or {}).get(_k) is not None: _row[_k]=(logs or {}).get(_k)\n"
        "            with open(METRICS_PATH,'a',encoding='utf-8') as _fh:\n"
        "                _fh.write(json.dumps(_row, ensure_ascii=False)+'\\n')\n"
        "        except Exception:\n"
        "            pass\n"
        "        return control\n"
        "from trl import SFTTrainer\n"
        "trainer=SFTTrainer(\n"
        "    model=model, train_dataset=dataset,\n"
        "    args=TrainingArguments(\n"
        "        per_device_train_batch_size=BATCH, gradient_accumulation_steps=ACCUM,\n"
        "        num_train_epochs=EPOCHS, learning_rate=2e-4, fp16=False, bf16=True,\n"
        "        logging_steps=1, save_strategy='steps', save_steps=SAVE_STEPS,\n"
        "        load_best_model_at_end=False, output_dir=str(SORTIE), report_to='none'), callbacks=[_MetricsCallback()])\n"
        "import glob\n"
        "ckpts=[p for p in glob.glob(str(SORTIE/'checkpoint-*')) if os.path.exists(os.path.join(p,'trainer_state.json'))]\n"
        "ckpts=sorted(ckpts, key=lambda p:int(p.split('checkpoint-')[-1]))\n"
        "if RESUME and ckpts:\n"
        "    last=ckpts[-1]\n"
        "    print('REPRISE au checkpoint:', last, flush=True)\n"
        "    trainer.train(resume_from_checkpoint=last)\n"
        "else:\n"
        "    if RESUME: print('Pas de checkpoint existant - depart a zero', flush=True)\n"
        "    trainer.train()\n"
        "model.save_pretrained_merged(FUSION, tokenizer, save_method='merged_16bit')\n"
        "print('TERMINE train, fusion:', FUSION, flush=True)\n"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")

    env = {"UNSLOTH_CE_LOSS_TARGET_GB": "1"}
    rc = pyscript(script, ATELIER, env_extra=env,
                  timeout=args.timeout, logfile=logfile)
    log(f"train {args.tag} -> exit {rc}", logfile)
    return 0 if rc == 0 else 1


# ---------------------------------------------------------------------------
# Sous-commande: merge-gguf  (fuse un checkpoint LoRA -> GGUF q8_0)
# ---------------------------------------------------------------------------
def cmd_merge_gguf(args):
    logfile = LOGS / f"merge_{args.tag}.log"

    adapter = args.adapter or ATELIER / f"sortie_{args.tag}" / f"checkpoint-{args.ckpt}"
    out = Path(args.out) if args.out else ROOT / f"localai-gguf-{args.tag}"

    # Script temporaire paramétré (chemins en dur dans merge_ckpt300.py ->
    # on génère une version avec les bons paramètres).
    script = LOGS / f"_merge_{args.tag}.py"
    modules_repr = repr(TARGET_MODULES)
    body = (
        "# -*- coding: utf-8 -*-\n"
        "import os\n"
        f'os.environ["HF_HOME"] = {HF_HOME!r}\n'
        "try:\n"
        "    from triton.compiler.compiler import AttrsDescriptor  # noqa\n"
        "except ImportError:\n"
        "    import triton.compiler.compiler as _tc\n"
        "    class _A: pass\n"
        "    _tc.AttrsDescriptor = _A\n"
        "from unsloth import FastLanguageModel\n"
        f"BASE = {str(args.base4bit)!r}\n"
        f"ADAPTER = {str(adapter)!r}\n"
        f"GGUF_OUT = {str(out)!r}\n"
        f"R = {int(args.rank)}\n"
        f"LORA_ALPHA = {int(args.alpha)}\n"
        "print('chargement base 4bit...', flush=True)\n"
        "model, tokenizer = FastLanguageModel.from_pretrained(\n"
        "    BASE, max_seq_length=512, load_in_4bit=True)\n"
        "print('construction peft...', flush=True)\n"
        "model = FastLanguageModel.get_peft_model(\n"
        "    model, r=R, lora_alpha=LORA_ALPHA,\n"
        f"    target_modules={modules_repr},\n"
        "    lora_dropout=0.05, bias='none', use_gradient_checkpointing='unsloth')\n"
        "print('chargement adaptateur checkpoint...', flush=True)\n"
        "model.load_adapter(ADAPTER, adapter_name='ckpt')\n"
        "model.set_adapter('ckpt')\n"
        f"print('conversion GGUF {args.quant} ...', flush=True)\n"
        f"model.save_pretrained_gguf(GGUF_OUT, tokenizer, quantization_method={args.quant!r})\n"
        "print('DONE_GGUF', flush=True)\n"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")

    log(f"merge-gguf tag={args.tag} ckpt={args.ckpt} quant={args.quant}", logfile)
    log(f"adapter: {adapter}", logfile)
    log(f"out: {out}", logfile)
    rc = pyscript(script, ATELIER, env_extra={"UNSLOTH_CE_LOSS_TARGET_GB": "1"},
                  timeout=args.timeout, logfile=logfile)
    log(f"merge-gguf -> exit {rc}", logfile)
    return 0 if rc == 0 else 1


# ---------------------------------------------------------------------------
# Sous-commande: convert  (16bit HF -> f16 GGUF puis q8_0)
# ---------------------------------------------------------------------------
def cmd_convert(args):
    logfile = LOGS / f"convert_{args.tag}.log"
    hf = args.hf if args.hf else ATELIER / (args.fusion or f"localai_finetuned_{args.tag}")
    f16 = ROOT / f"localai-{args.tag}-f16.gguf"
    q8 = Path(args.out) if args.out else ROOT / f"localai-{args.tag}-q8_0.gguf"
    LOGS.mkdir(parents=True, exist_ok=True)

    steps = []
    conlogo = LOGS / f"convert_{args.tag}_step1.log"
    qpslogo = LOGS / f"quantize_{args.tag}.log"

    # source f16 pour la quantification : soit fraîchement créée (--f16),
    # soit fournie (--skip-from), soit le chemin f16 par défaut.
    src_f16 = Path(args.skip_from) if args.skip_from else f16

    if args.f16:
        steps.append([
            str(VENV), "-u", CONVERT_HF, str(hf),
            "--outfile", str(f16), "--outtype", "f16",
        ])
        src_f16 = f16
    elif not src_f16.exists():
        log(f"Source f16 introuvable pour la quantification: {src_f16}", logfile)
        log("Indice: utiliser --f16 pour convertir depuis le HF, ou --skip-from <f16 existant>.", logfile)
        return 1

    steps.append([
        QUANTIZE_EXE, str(src_f16), str(q8), args.quant,
    ])

    env = build_env()

    for i, cmd in enumerate(steps, 1):
        log(f"STEP {i}: {' '.join(cmd)}", logfile)
        with (conlogo if i == 1 else qpslogo).open("wb") as f:
            r = subprocess.run(cmd, cwd=str(ROOT), env=env,
                               stdout=f, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            log(f"STEP {i} ECHEC exit={r.returncode}", logfile)
            return 1
        log(f"STEP {i} OK", logfile)

    log(f"convert -> {q8}", logfile)
    return 0


# ---------------------------------------------------------------------------
# Sous-commande: deploy  (Modelfile -> ollama create)
# ---------------------------------------------------------------------------
def cmd_deploy(args):
    logfile = LOGS / f"deploy_{args.tag}.log"
    model_name = f"localai-prog-{args.tag}"
    modelfile = Path(args.modelfile) if args.modelfile else ATELIER / f"Modelfile_{args.tag}"
    new_mp = ATELIER / f"Modelfile_{args.tag}.new"

    # réécriture du FROM vers le GGUF q8_0 cible si {{GGUF}} présent ou si --gguf
    if not modelfile.exists():
        log(f"Modelfile absent: {modelfile}", logfile)
        return 1
    content = modelfile.read_text(encoding="utf-8")
    target_gguf = None
    if args.gguf:
        target_gguf = str(args.gguf)
    elif "{{GGUF}}" in content:
        target_gguf = str(ROOT / f"localai-{args.tag}-q8_0.gguf")
    if target_gguf:
        content = content.replace("{{GGUF}}", target_gguf)
    new_mp.write_text(content, encoding="utf-8")

    log(f"deploy model={model_name} tag={args.tag} modelfile={new_mp}", logfile)
    cmd = [_find_ollama(), "create", model_name, "-f", str(new_mp)]
    env = os.environ.copy()
    try:
        log(f"RUN: {' '.join(cmd)}", logfile)
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=args.timeout,
                           capture_output=True, encoding="utf-8", errors="replace")
        log(f"EXIT={r.returncode}", logfile)
        if r.stdout:
            log(f"STDOUT: {r.stdout[-2000:]}", logfile)
        if r.stderr:
            log(f"STDERR: {r.stderr[-2000:]}", logfile)
        return 0 if r.returncode == 0 else 1
    except Exception as exc:
        log(f"ERROR: {exc!r}", logfile)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline",
        description="Pipeline LocalAI : entraînement, fusion GGUF, conversion et déploiement.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # train
    t = sub.add_parser("train", help="Entraîne un modèle QLoRA (via train_lora_v6.py ou équivalent).")
    t.add_argument("--tag", default="v7", help="Suffixe des sorties (sortie_<tag>, fusion localai_finetuned_<tag>).")
    t.add_argument("--dataset", default="sft_localai.jsonl", help="Corpus (dans ATELIER).")
    t.add_argument("--base", default=BASE_MODEL, help="Modèle de base.")
    t.add_argument("--rank", type=int, default=16, help="Rang LoRA (R).")
    t.add_argument("--epochs", type=int, default=3, help="Nombre d'époques.")
    t.add_argument("--batch", type=int, default=1, help="Batch par device.")
    t.add_argument("--accum", type=int, default=8, help="Gradient accumulation.")
    t.add_argument("--max-seq", type=int, default=1024, help="Longueur max de séquence.")
    t.add_argument("--ckpt", type=int, default=50, help="Save_steps (fréquence des checkpoints).")
    t.add_argument("--fusion", default=None, help="Nom du modèle fusionné 16bit (défaut localai_finetuned_<tag>).")
    t.add_argument("--timeout", type=int, default=57600, help="Timeout en secondes.")
    t.add_argument("--resume", action="store_true", help="Ne purge pas la sortie existante.")
    t.add_argument("--no-unload", action="store_true", help="Ne décharge pas Ollama avant.")
    t.set_defaults(func=cmd_train)

    # merge-gguf
    m = sub.add_parser("merge-gguf", help="Fusionne un checkpoint LoRA -> GGUF (q8_0 par défaut).")
    m.add_argument("--tag", default="v7", help="Niveau pour localiser sortie_<tag>.")
    m.add_argument("--ckpt", type=int, default=300, help="Numéro du checkpoint (ex 300).")
    m.add_argument("--adapter", default=None, help="Chemin adaptateur (sinon sortie_<tag>/checkpoint-<ckpt>).")
    m.add_argument("--base4bit", default=BASE_4BIT, help="Base 4-bit pour l'adaptateur.")
    m.add_argument("--rank", type=int, default=32, help="R LoRA (doit matcher l'entraînement).")
    m.add_argument("--alpha", type=int, default=64, help="lora_alpha.")
    m.add_argument("--quant", default="q8_0", help="Méthode de quantification GGUF.")
    m.add_argument("--out", default=None, help="Dossier de sortie GGUF.")
    m.add_argument("--timeout", type=int, default=14400, help="Timeout en secondes.")
    m.set_defaults(func=cmd_merge_gguf)

    # convert
    c = sub.add_parser("convert", help="Convertit un modèle HF 16bit -> f16 GGUF -> q8_0.")
    c.add_argument("--tag", default="v7", help="Suffixe des sorties (localai-<tag>-*.gguf).")
    c.add_argument("--hf", default=None, help="Dossier modèle HF (sinon ATELIER/<fusion>).")
    c.add_argument("--fusion", default="", help="Nom fusion par défaut si --hf absent (sinon localai_finetuned_<tag>).")
    c.add_argument("--f16", action="store_true", help="Produit aussi l'étape f16 (sinon on part d'un f16 existant).")
    c.add_argument("--skip-from", default=None, help="Chemin d'un f16 existant si pas de conversion. Semblable à --f16.")
    c.add_argument("--quant", default="q8_0", help="Quantification finale.")
    c.add_argument("--out", default=None, help="Fichier GGUF final (sinon localai-<tag>-q8_0.gguf).")
    c.add_argument("--timeout", type=int, default=14400, help="Timeout en secondes.")
    c.set_defaults(func=cmd_convert)

    # deploy
    d = sub.add_parser("deploy", help="Crée le modèle Ollama depuis un Modelfile.")
    d.add_argument("--tag", default="v7", help="Nom du modèle Ollama (localai-prog-<tag>).")
    d.add_argument("--modelfile", default=None, help="Chemin Modelfile (sinon ATELIER/Modelfile_<tag>).")
    d.add_argument("--gguf", default=None, help="Chemin GGUF à insérer dans FROM (sinon {{GGUF}}/q8_0).")
    d.add_argument("--timeout", type=int, default=3600, help="Timeout en secondes.")
    d.set_defaults(func=cmd_deploy)

    # infos
    sub.add_parser("info", help="Affiche les chemins/config utilisés.").set_defaults(
        func=lambda a: _print_info())

    return p


def _print_info():
    info = {
        "ROOT": str(ROOT),
        "ATELIER": str(ATELIER),
        "VENV": str(VENV),
        "LOGS": str(LOGS),
        "HF_HOME": HF_HOME,
        "CONVERT_HF": CONVERT_HF,
        "QUANTIZE_EXE": QUANTIZE_EXE,
        "BASE_MODEL": BASE_MODEL,
        "BASE_4BIT": BASE_4BIT,
    }
    for k, v in info.items():
        print(f"{k}: {v}")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except Exception as exc:
        print(f"ERREUR: {exc!r}", flush=True)
        rc = 1
    sys.exit(rc if rc is not None else 0)


if __name__ == "__main__":
    main()
