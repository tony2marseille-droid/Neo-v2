import { useEffect, useRef } from "react";

/**
 * Appelle `fn` toutes les `ms` millisecondes tant que `enabled` est vrai.
 * L'intervalle est coupe des que `enabled` repasse a faux ou au demontage ;
 * la derniere version de `fn` est toujours utilisee (pas de fermeture perimee,
 * pas d'intervalle recree a chaque rendu). Aucun appel immediat.
 */
export function usePolling(fn: () => void, ms: number, enabled = true): void {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => fnRef.current(), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
}
