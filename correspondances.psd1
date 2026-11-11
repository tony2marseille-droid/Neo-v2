# correspondances.psd1 - SEULE description de "quel fichier du depot va ou en production".
#
# Le depot est la source unique : on modifie code/, on commite, puis deployer.ps1
# recopie ces entrees vers la production. verifier_ecart.ps1 compare les memes
# entrees pour signaler toute modification faite directement en production.
#
# Miroirs  : dossier aligne EXACTEMENT (robocopy /MIR) : un fichier supprime du
#            depot disparait de la production (apres sauvegarde).
# Fichiers : fichier copie un par un (dossiers de production partages avec
#            des donnees, des modeles ou des fichiers hors projet).
# Les chemins "Depot" sont relatifs a code\.
# Exclus partout : __pycache__, .venv, node_modules, *.pyc, *.log.
@{
    Miroirs = @(
        @{ Depot = 'localai-rs\localai-core\src';     Vivant = 'E:\localai-rs\localai-core\src' }
        @{ Depot = 'localai-rs\localai-server\src';   Vivant = 'E:\localai-rs\localai-server\src' }
        @{ Depot = 'localai-rs\localai-watch\src';    Vivant = 'E:\localai-rs\localai-watch\src' }
        @{ Depot = 'localai-rs\localai-pipeline\src'; Vivant = 'E:\localai-rs\localai-pipeline\src' }
        @{ Depot = 'localai-rs\neo-silencieux\src';   Vivant = 'E:\localai-rs\neo-silencieux\src' }
        @{ Depot = 'backend';                         Vivant = 'E:\ia\backend' }
        @{ Depot = 'tests_unit';                      Vivant = 'E:\ia\tests_unit' }
        @{ Depot = 'frontend\src';                    Vivant = 'E:\ia\frontend\src' }
        @{ Depot = 'tools\toolshim';                  Vivant = 'E:\ia\tools\toolshim' }
    )
    Fichiers = @(
        # Espace de travail Rust
        @{ Depot = 'localai-rs\Cargo.toml';                  Vivant = 'E:\localai-rs\Cargo.toml' }
        @{ Depot = 'localai-rs\Cargo.lock';                  Vivant = 'E:\localai-rs\Cargo.lock' }
        @{ Depot = 'localai-rs\CHANGELOG.md';                Vivant = 'E:\localai-rs\CHANGELOG.md' }
        @{ Depot = 'localai-rs\rebuild.bat';                 Vivant = 'E:\localai-rs\rebuild.bat' }
        @{ Depot = 'localai-rs\localai-core\Cargo.toml';     Vivant = 'E:\localai-rs\localai-core\Cargo.toml' }
        @{ Depot = 'localai-rs\localai-server\Cargo.toml';   Vivant = 'E:\localai-rs\localai-server\Cargo.toml' }
        @{ Depot = 'localai-rs\localai-server\build.rs';     Vivant = 'E:\localai-rs\localai-server\build.rs' }
        @{ Depot = 'localai-rs\localai-watch\Cargo.toml';    Vivant = 'E:\localai-rs\localai-watch\Cargo.toml' }
        @{ Depot = 'localai-rs\localai-pipeline\Cargo.toml'; Vivant = 'E:\localai-rs\localai-pipeline\Cargo.toml' }
        @{ Depot = 'localai-rs\neo-silencieux\Cargo.toml';   Vivant = 'E:\localai-rs\neo-silencieux\Cargo.toml' }
        # Frontend (le dossier contient aussi node_modules et dist, construits sur place)
        @{ Depot = 'frontend\index.html';         Vivant = 'E:\ia\frontend\index.html' }
        @{ Depot = 'frontend\package.json';       Vivant = 'E:\ia\frontend\package.json' }
        @{ Depot = 'frontend\package-lock.json';  Vivant = 'E:\ia\frontend\package-lock.json' }
        @{ Depot = 'frontend\tsconfig.json';      Vivant = 'E:\ia\frontend\tsconfig.json' }
        @{ Depot = 'frontend\tsconfig.app.json';  Vivant = 'E:\ia\frontend\tsconfig.app.json' }
        @{ Depot = 'frontend\tsconfig.node.json'; Vivant = 'E:\ia\frontend\tsconfig.node.json' }
        @{ Depot = 'frontend\vite.config.ts';     Vivant = 'E:\ia\frontend\vite.config.ts' }
        @{ Depot = 'frontend\vitest.config.ts';   Vivant = 'E:\ia\frontend\vitest.config.ts' }
        @{ Depot = 'frontend\.oxlintrc.json';     Vivant = 'E:\ia\frontend\.oxlintrc.json' }
        # Configuration
        @{ Depot = 'config\models.json'; Vivant = 'E:\ia\config\models.json' }
        @{ Depot = 'config\services.json'; Vivant = 'E:\ia\config\services.json' }   # services du superviseur (relu a chaud)
        @{ Depot = 'config\neo_persona.md'; Vivant = 'E:\ia\config\neo_persona.md' }  # personnage de Neo (relu a chaque message)
        @{ Depot = 'config\neo.toml'; Vivant = 'E:\ia\neo.toml' }                     # reglages de CETTE machine (non versionne)
        # Scripts a la racine de E:\ia, lances par le serveur ou par les taches planifiees
        @{ Depot = 'racine\apprentissage.py';    Vivant = 'E:\ia\apprentissage.py' }    # route /documents (apprentissage)
        @{ Depot = 'racine\conseil.py';          Vivant = 'E:\ia\conseil.py' }          # route /conseil
        @{ Depot = 'racine\impl_idee.py';        Vivant = 'E:\ia\impl_idee.py' }        # route /conseil (implementer une idee)
        @{ Depot = 'racine\veille.py';           Vivant = 'E:\ia\veille.py' }           # route /veille
        @{ Depot = 'racine\pipeline.py';         Vivant = 'E:\ia\pipeline.py' }         # route /finetune
        @{ Depot = 'racine\serve_video.py';      Vivant = 'E:\ia\serve_video.py' }      # tache NeoVideoAPI (:8321)
        @{ Depot = 'racine\rerank_server.py';    Vivant = 'E:\ia\rerank_server.py' }    # tache Neo_Rerank (:8322)
        @{ Depot = 'racine\garde_prod.ps1';      Vivant = 'E:\ia\garde_prod.ps1' }      # tache Neo_GardeProd
        @{ Depot = 'racine\run_coach.bat';       Vivant = 'E:\ia\run_coach.bat' }       # tache LocalAI_coach
        @{ Depot = 'racine\run_server_boot.ps1'; Vivant = 'E:\ia\run_server_boot.ps1' } # tache LocalAI_server_boot
        @{ Depot = 'racine\run_server_watch.ps1'; Vivant = 'E:\ia\run_server_watch.ps1' }
        @{ Depot = 'racine\run_ollama.ps1';      Vivant = 'E:\ia\run_ollama.ps1' }      # relance par garde_prod
        # Outils d'installation
        @{ Depot = 'scripts\NeoConfig.psm1';        Vivant = 'E:\ia\scripts\NeoConfig.psm1' }   # lecture de neo.toml pour les scripts
        @{ Depot = 'scripts\build_exe.ps1';         Vivant = 'E:\ia\scripts\build_exe.ps1' }
        @{ Depot = 'scripts\install_models.ps1';    Vivant = 'E:\ia\scripts\install_models.ps1' }
        @{ Depot = 'scripts\publish_update.ps1';    Vivant = 'E:\ia\scripts\publish_update.ps1' }
        @{ Depot = 'scripts\SETUP-PC-PUISSANT.md';  Vivant = 'E:\ia\scripts\SETUP-PC-PUISSANT.md' }
    )
}
