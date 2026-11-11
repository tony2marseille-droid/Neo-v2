import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

// Le module "talkinghead" (et three.js) est resolu par l'importmap de
// index.html et charge a la demande, seulement quand l'avatar est affiche.

export interface Avatar3DHandle {
  speak: (blob: Blob, text?: string) => Promise<boolean>;
  ready: boolean;
}

interface Props {
  size?: number;
}

const Avatar3D = forwardRef<Avatar3DHandle, Props>(function Avatar3D({ size = 160 }, ref) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<any>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    let cancelled = false;
    let head: any = null;
    let audioCtx: AudioContext | null = null;

    // Libere le moteur 3D (contexte WebGL) et le contexte audio.
    function liberer() {
      try { head?.stop?.(); } catch { /* deja arrete */ }
      try { head?.dispose?.(); } catch { /* API absente selon la version */ }
      try { head?.renderer?.dispose?.(); head?.renderer?.forceContextLoss?.(); } catch { /* idem */ }
      void audioCtx?.close().catch(() => undefined);
      head = null;
      audioCtx = null;
      headRef.current = null;
      audioCtxRef.current = null;
    }

    async function init() {
      try {
        if (!containerRef.current) return;
        // Specifieur dans une variable : Vite le laisse intact et le navigateur
        // le resout par l'importmap de index.html (un litteral casse le serveur de dev).
        const module = "talkinghead";
        const TH = await import(/* @vite-ignore */ module);
        // Demonte pendant le chargement : on n'alloue rien.
        if (cancelled || !containerRef.current) return;
        const TalkingHead = TH.TalkingHead || TH.default?.TalkingHead || TH.default;

        audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
        audioCtxRef.current = audioCtx;

        head = new TalkingHead(containerRef.current, {
          audioCtx,
          cameraView: "head",
          modelFPS: 30,
          lipsyncLang: "en",
          ttsLang: "en-US",
        });
        headRef.current = head;

        await head.showAvatar({
          url: "/avatars/brunette.glb",
          body: "F",
          avatarMood: "neutral",
          lipsyncLang: "en",
        });

        if (cancelled) {
          liberer();
          return;
        }
        setStatus("ready");
      } catch (err: any) {
        console.error("Avatar3D init error:", err);
        if (!cancelled) {
          setStatus("error");
          setErrorMsg(String(err?.message || err));
        }
      }
    }

    init();

    return () => {
      cancelled = true;
      liberer();
    };
  }, []);

  useImperativeHandle(ref, () => ({
    ready: status === "ready",
    speak: async (blob: Blob, text?: string) => {
      const head = headRef.current;
      const audioCtx = audioCtxRef.current;
      if (!head || !audioCtx || status !== "ready") return false;
      try {
        if (audioCtx.state === "suspended") await audioCtx.resume();
        const arrayBuf = await blob.arrayBuffer();
        const audioBuffer = await audioCtx.decodeAudioData(arrayBuf.slice(0));

        if (text && text.trim()) {
          const words = text.trim().split(/\s+/);
          const durationMs = audioBuffer.duration * 1000;
          const totalChars = words.reduce((a, w) => a + w.length, 0) || 1;
          const wtimes: number[] = [];
          const wdurations: number[] = [];
          let t = 0;
          for (const w of words) {
            const dur = Math.max(40, Math.round((w.length / totalChars) * durationMs));
            wtimes.push(Math.round(t));
            wdurations.push(dur);
            t += dur;
          }
          await head.speakAudio({ audio: audioBuffer, words, wtimes, wdurations });
        } else {
          await head.speakAudio({ audio: audioBuffer });
        }
        return true;
      } catch (err) {
        console.error("Avatar3D speak error:", err);
        return false;
      }
    },
  }), [status]);

  if (status === "error") {
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          background: "var(--bg-card)",
          color: "var(--text-dim)",
          border: "1px solid var(--border)",
          fontSize: 10,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          padding: 4,
        }}
        title={errorMsg}
      >
        3D indisponible
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onClick={() => { if (audioCtxRef.current?.state === "suspended") audioCtxRef.current.resume(); }}
      style={{
        width: size,
        height: size,
        borderRadius: 8,
        overflow: "hidden",
        background: "radial-gradient(circle at 50% 35%, rgba(var(--accent-rgb), 0.22), var(--bg-card) 70%)",
        border: "1px solid var(--border)",
        position: "relative",
      }}
    >
      {status === "loading" && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-dim)",
            fontSize: 11,
          }}
        >
          Chargement 3D…
        </div>
      )}
    </div>
  );
});

export default Avatar3D;
