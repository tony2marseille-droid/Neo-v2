import os
import pathlib
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Neo"
APP_VERSION = "0.4.1"


def _default_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _bundled() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return _default_root()


def _dossier_documents_defaut() -> Path:
    """Dossier des fichiers produits (images, videos, voix).

    Sous le compte systeme (service Windows), « Documents » est
    C:/Windows/System32/config/systemprofile/Documents : invisible et
    inaccessible pour l'utilisateur (images perdues, erreur WinError 2 le
    10/11/2026). On prend alors <racine>/workspace/images, le dossier que
    l'interface affiche. LOCALAI_NEO_DOCUMENTS_DIR reste prioritaire.
    """
    maison = Path.home()
    if "systemprofile" in str(maison).lower():
        return _default_root() / "workspace" / "images"
    return maison / "Documents" / "Neo" / "Images_generees"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOCALAI_",
        env_file=str(_default_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8320
    ollama_host: str = "http://127.0.0.1:11434"
    inference_backend: str = "ollama"
    llamacpp_bin_dir: Path = _default_root() / "llamacpp"
    llamacpp_port: int = 8399
    gguf_dir: Path = _default_root() / "gguf"
    root_dir: Path = _default_root()
    data_dir: Path = _default_root() / "data"
    models_catalog_file: Path = _bundled() / "config" / "models.json"
    remote_catalog_url: str = ""
    # Dossier de sortie pour les images générées (configurable)
    neo_documents_dir: pathlib.Path = _dossier_documents_defaut()
    update_manifest_url: str = ""
    default_model: str = ""
    tts_voice: str = ""
    tts_rate: int = 185

    @property
    def llamacpp_server_bin(self) -> Path:
        return self.llamacpp_bin_dir / "llama-server.exe"

    @property
    def db_path(self) -> Path:
        # Base historique de Neo, conservee apres le renommage du produit.
        # Le renommage avait cree une base oracle.db VIDE : le RAG, les fiches,
        # les memoires et les conversations etaient invisibles cote Python.
        force = os.environ.get("NEO_DB_PATH")
        if force:
            return Path(force).expanduser()
        return self.data_dir / "localai.db"

    @property
    def tts_cache_dir(self) -> Path:
        return self.data_dir / "tts"

    @property
    def frontend_dist(self) -> Path:
        bundled = _bundled() / "webui"
        if bundled.is_dir():
            return bundled
        dev = self.root_dir / "frontend" / "dist"
        if dev.is_dir():
            return dev
        return bundled


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.tts_cache_dir.mkdir(parents=True, exist_ok=True)
settings.gguf_dir.mkdir(parents=True, exist_ok=True)


def ollama_url() -> str:
    """URL de base d'Ollama, source unique pour tout le backend Python.

    Priorite : LOCALAI_OLLAMA_URL (variable historique des outils), sinon le
    reglage `ollama_host` (LOCALAI_OLLAMA_HOST ou .env), sans « / » final.
    """
    return (os.environ.get("LOCALAI_OLLAMA_URL") or settings.ollama_host).rstrip("/")


# Ollama RECHARGE un modele (5,3 s pour gemma4:12b) des que num_ctx change d'une
# requete a l'autre. Gemma 4 sert a tout (conversation, vision, reformulation) et
# ne coute que 200 Mo de 8 192 a 16 384 jetons : une seule taille partout. Meme
# regle que `localai_core::ollama::contexte_pour` cote Rust.
CTX_GEMMA4 = 16384


def contexte_pour(modele: str, souhaite: int) -> int:
    return CTX_GEMMA4 if (modele or "").lower().startswith("gemma4") else souhaite
