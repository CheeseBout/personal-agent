/** The hidden voice window: records the microphone when asked and plays spoken answers. It decides nothing about the agent:
 * a recording goes back to the main process, and clips come from the daemon through it. */
import { useEffect } from "react";
import type { Ev } from "../../shared/session";
import { floatToInt16, RATE, Recording, Speaker } from "../../shared/voice";

const post = (name: string, ...args: unknown[]): Promise<any> => window.agent.overlay(name, ...args);

function base64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

class Engine {
  private audio = new Audio();
  private speaker = new Speaker<string>(
    (url) => {
      this.audio.src = url;
      this.audio.play().catch(() => this.clipOver());
    },
    () => {
      this.audio.pause();
      this.audio.removeAttribute("src");
    },
    (on) => void post("speaking", on));
  private rec: { recording: Recording; stream: MediaStream; ctx: AudioContext } | null = null;
  private off: () => void;

  constructor() {
    this.audio.onended = this.audio.onerror = () => this.clipOver(); // a clip that cannot be played is skipped, never a hang
    this.off = window.agent.onEvent((ev) => void (ev.type === "_voice" && this.on(ev)));
    void post("ready");
  }

  dispose(): void {
    this.off();
    this.speaker.stop();
    this.finish(false);
  }

  private clipOver(): void {
    if (this.audio.src.startsWith("blob:")) URL.revokeObjectURL(this.audio.src);
    this.speaker.ended();
  }

  private on(ev: Ev): void {
    if (ev.cmd === "speak") this.speaker.begin(ev.id);
    else if (ev.cmd === "clip") {
      const bytes = Uint8Array.from(atob(String(ev.data)), (c) => c.charCodeAt(0));
      this.speaker.clip(ev.id, URL.createObjectURL(new Blob([bytes], { type: String(ev.mime || "audio/mpeg") })));
    } else if (ev.cmd === "done") this.speaker.done(ev.id);
    else if (ev.cmd === "stop") this.speaker.stop();
    else if (ev.cmd === "record") void this.record(Number(ev.maxSeconds) || 120);
    else if (ev.cmd === "stopRecord") this.finish(true);
  }

  private async record(maxSeconds: number): Promise<void> {
    if (this.rec) return;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    } catch {
      return void post("failed", "no microphone found");
    }
    const ctx = new AudioContext({ sampleRate: RATE }); // the browser resamples the device to 16 kHz for us
    const recording = new Recording(maxSeconds);
    // shortcut: ScriptProcessorNode is deprecated but needs no separate worklet file; move to an AudioWorklet if Chromium ever removes it.
    const tap = ctx.createScriptProcessor(4096, 1, 1);
    tap.onaudioprocess = (e) => {
      if (this.rec?.recording === recording && recording.feed(floatToInt16(e.inputBuffer.getChannelData(0)))) this.finish(true);
    };
    ctx.createMediaStreamSource(stream).connect(tap);
    tap.connect(ctx.destination); // a processor only runs while connected; it writes no output, so nothing is played back
    this.rec = { recording, stream, ctx };
    void post("recording", true);
  }

  private finish(report: boolean): void {
    const r = this.rec;
    if (!r) return;
    this.rec = null;
    r.stream.getTracks().forEach((t) => t.stop()); // the microphone light goes off at once
    void r.ctx.close();
    if (!report) return;
    const wav = r.recording.wav();
    void (wav ? post("recorded", base64(wav)) : post("failed", "recording too short"));
  }
}

export function Voice(): null {
  useEffect(() => {
    const e = new Engine();
    return () => e.dispose();
  }, []);
  return null;
}
