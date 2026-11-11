/** Microphone recording (toggle, with silence stop) and spoken answers (interruptible) without any audio device: the rules only.
 * Speech-to-text and text-to-speech both run in the daemon; the UI records, and plays what comes back. Pure, tested. */

export const RATE = 16000;
export const SPEECH_RMS = 600; // above this a chunk counts as speech
export const SILENCE_STOP_S = 1.5;
export const MIN_SECONDS = 0.4;

export function floatToInt16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) out[i] = Math.max(-32768, Math.min(32767, Math.round(f[i]! * 32767)));
  return out;
}

export function rms(pcm: Int16Array): number {
  if (!pcm.length) return 0;
  let sum = 0;
  for (const x of pcm) sum += x * x;
  return Math.sqrt(sum / pcm.length);
}

/** 16-bit mono PCM as a complete WAV file. */
export function pcmToWav(pcm: Int16Array, rate = RATE): Uint8Array {
  const out = new Uint8Array(44 + pcm.length * 2), v = new DataView(out.buffer);
  const text = (at: number, s: string): void => [...s].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  v.setUint32(4, 36 + pcm.length * 2, true);
  text(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, pcm.length * 2, true);
  pcm.forEach((s, i) => v.setInt16(44 + i * 2, s, true));
  return out;
}

/** Collects microphone chunks and decides when the recording is over: the time limit, or silence after something was said. */
export class Recording {
  private chunks: Int16Array[] = [];
  private samples = 0;
  private heard = false;
  private quiet = 0;

  constructor(private maxSeconds = 120, private stopOnSilence = true, private rate = RATE) {}

  /** Add a chunk; true = stop now. */
  feed(chunk: Int16Array): boolean {
    if (!chunk.length) return false;
    this.chunks.push(chunk);
    this.samples += chunk.length;
    if (rms(chunk) > SPEECH_RMS) {
      this.heard = true;
      this.quiet = 0;
    } else if (this.heard) this.quiet += chunk.length / this.rate;
    return this.samples / this.rate >= this.maxSeconds || (this.stopOnSilence && this.quiet >= SILENCE_STOP_S);
  }

  /** The finished WAV, or null when the recording is too short to be worth transcribing. */
  wav(): Uint8Array | null {
    if (this.samples / this.rate < MIN_SECONDS) return null;
    const all = new Int16Array(this.samples);
    let at = 0;
    for (const c of this.chunks) {
      all.set(c, at);
      at += c.length;
    }
    return pcmToWav(all, this.rate);
  }
}

/** Plays an answer sentence by sentence as the clips arrive, and can be interrupted at once (the user talks, sends, or presses stop).
 * `play(clip)` starts a clip and `halt()` silences the player; call `ended()` when a clip finishes or cannot be played. */
export class Speaker<Clip> {
  private id: unknown = null; // the answer being spoken; clips of any other are stale
  private queue: Clip[] = [];
  private playing = false;
  private synthDone = true;
  private was = false;

  constructor(private play: (clip: Clip) => void, private halt: () => void, private changed: (speaking: boolean) => void) {}

  get speaking(): boolean {
    return this.playing || !this.synthDone;
  }

  private report(): void {
    if (this.speaking !== this.was) this.changed((this.was = this.speaking));
  }

  /** A new answer starts: whatever was being said stops. */
  begin(id: unknown): void {
    this.stop();
    this.id = id;
    this.synthDone = false;
    this.report();
  }

  clip(id: unknown, clip: Clip): void {
    if (id !== this.id || this.synthDone) return;
    this.queue.push(clip);
    if (!this.playing) this.next();
  }

  /** The daemon has sent every sentence (or failed). */
  done(id: unknown): void {
    if (id !== this.id) return;
    this.synthDone = true;
    this.report();
  }

  ended(): void {
    if (this.playing) this.next();
  }

  private next(): void {
    const clip = this.queue.shift();
    this.playing = clip !== undefined;
    if (clip !== undefined) this.play(clip);
    this.report();
  }

  stop(): void {
    this.id = null;
    this.queue = [];
    this.playing = false;
    this.synthDone = true;
    this.halt();
    this.report();
  }
}
