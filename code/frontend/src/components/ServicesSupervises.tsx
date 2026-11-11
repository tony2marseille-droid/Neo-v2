import { useCallback, useEffect, useState } from "react";
import { getJSON } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import { usePolling } from "../lib/usePolling";

interface ServiceEtat {
  nom: string;
  libelle: string;
  port: number;
  etat: "ok" | "demarrage" | "degrade" | "arrete" | "en_echec" | "desactive" | "inconnu";
  depuis: string;
  relances: number;
  message: string;
}

interface EtatSuperviseur {
  superviseur_actif: boolean;
  services: ServiceEtat[];
}

const CLASSE: Record<ServiceEtat["etat"], string> = {
  ok: "status-ok",
  demarrage: "status-warn",
  degrade: "status-warn",
  arrete: "status-bad",
  en_echec: "status-bad",
  desactive: "",
  inconnu: "",
};

/** Services lances et surveilles par le superviseur (localai-watch). */
export default function ServicesSupervises({ active, lang }: { active: boolean; lang: Lang }) {
  const [etat, setEtat] = useState<EtatSuperviseur | null>(null);
  const charger = useCallback(() => {
    getJSON<EtatSuperviseur>("/api/system/services")
      .then(setEtat)
      .catch(() => setEtat(null));
  }, []);

  useEffect(() => {
    if (active) charger();
  }, [active, charger]);
  usePolling(charger, 10000, active);

  if (!etat) return null;
  if (!etat.superviseur_actif) {
    return (
      <div className="sp-row">
        <span>{t(lang, "svc.supervisor")}</span>
        <span className="status-bad">{t(lang, "svc.supervisorDown")}</span>
      </div>
    );
  }
  return (
    <>
      {etat.services
        // Ollama et le serveur ont deja leur ligne (version) dans le panneau.
        .filter((s) => s.etat !== "desactive" && s.nom !== "ollama" && s.nom !== "serveur")
        .map((s) => (
          <div
            className="sp-row"
            key={s.nom}
            title={[`:${s.port}`, s.message, s.relances ? `${t(lang, "svc.restarts")} : ${s.relances}` : ""]
              .filter(Boolean)
              .join(" · ")}
          >
            <span>{s.libelle}</span>
            <span className={CLASSE[s.etat]}>{t(lang, `svc.state.${s.etat}`)}</span>
          </div>
        ))}
    </>
  );
}
