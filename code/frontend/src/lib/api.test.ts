import { describe, expect, it, vi, afterEach } from "vitest";
import { getJSON, sendJSON, streamSSE, transcribeAudio } from "./api";
import { ReadableStream } from "node:stream/web";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getJSON", () => {
  it("renvoie le JSON parse", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJSON("/api/health")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health");
  });

  it("leve une erreur sur statut non ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    );
    await expect(getJSON("/api/panne")).rejects.toThrow("HTTP 500");
  });
});

describe("sendJSON", () => {
  it("serialise le corps et la methode", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    await sendJSON("/api/settings", "PUT", { key: "theme", value: "sombre" });
    expect(fetchMock).toHaveBeenCalledWith("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "theme", value: "sombre" }),
    });
  });

  it("omet le corps quand il est absent", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    await sendJSON("/api/action", "POST");
    const [, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      { body?: unknown }
    ];
    expect(options.body).toBeUndefined();
  });
});

describe("streamSSE", () => {
  it("decode les evenements data: y compris a cheval sur deux blocs", async () => {
    const evenements: unknown[] = [];
    const bloc1 = new TextEncoder().encode(
      'data: {"type":"a"}\n\ndata: pas-du-json\n\nda'
    );
    const bloc2 = new TextEncoder().encode('ta: {"type":"b"}\n\n');
    const flux = new ReadableStream<Uint8Array>({
      start(controleur) {
        controleur.enqueue(bloc1);
        controleur.enqueue(bloc2);
        controleur.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: flux }));

    await streamSSE("/api/chat", { message: "salut" }, (e) => evenements.push(e));
    // La ligne `data: pas-du-json` est ignoree (JSON invalide).
    expect(evenements).toEqual([{ type: "a" }, { type: "b" }]);
  });

  it("propage l'exception levee par onEvent (evenement error)", async () => {
    const flux = new ReadableStream<Uint8Array>({
      start(controleur) {
        controleur.enqueue(
          new TextEncoder().encode('data: {"type":"error","error":"pull refuse"}\r\n\r\ndata:{"type":"done"}\n\n')
        );
        controleur.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: flux }));
    const vus: string[] = [];
    await expect(
      streamSSE("/api/models/install", {}, (e) => {
        vus.push(e.type);
        if (e.type === "error") throw new Error(String(e.error));
      })
    ).rejects.toThrow("pull refuse");
    expect(vus).toEqual(["error"]);
  });

  it("accepte data: sans espace et les fins de ligne CRLF", async () => {
    const flux = new ReadableStream<Uint8Array>({
      start(controleur) {
        controleur.enqueue(new TextEncoder().encode('data:{"type":"a"}\r\n\r\ndata: {"type":"b"}'));
        controleur.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: flux }));
    const evenements: unknown[] = [];
    await streamSSE("/api/chat", {}, (e) => evenements.push(e));
    expect(evenements).toEqual([{ type: "a" }, { type: "b" }]);
  });

  it("leve si la reponse n'est pas ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        body: null,
        text: async () => "jeton requis",
      })
    );
    await expect(streamSSE("/api/chat", {}, () => undefined)).rejects.toThrow(
      "HTTP 401"
    );
  });
});

describe("transcribeAudio", () => {
  it("envoie le fichier audio en FormData", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ text: "bonjour" }) });
    vi.stubGlobal("fetch", fetchMock);

    const audio = new Blob(["audio"], { type: "audio/webm" });
    await expect(transcribeAudio(audio)).resolves.toEqual({ text: "bonjour" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/voice/transcribe",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      })
    );
  });
});
