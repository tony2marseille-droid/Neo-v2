"""Point d'entrée : python -m toolshim [--config config.json] [--port 8330]."""
import argparse
import logging

import uvicorn

from .config import load_config
from .server import create_app


def main() -> None:
    """Lit les arguments, charge la configuration et démarre le serveur uvicorn."""
    ap = argparse.ArgumentParser(prog="toolshim")
    ap.add_argument("--config", default=None, help="chemin de config.json")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
