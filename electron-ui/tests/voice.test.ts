// Recording and spoken answers without an audio device (ported from tests/unit/test_ui_logic.py and tests/ui/test_ui_voice.py).
import { describe, expect, it } from "vitest";
import { floatToInt16, pcmToWav, RATE, Recording, rms, Speaker } from "../src/shared/voice";

const tone = (seconds: number, level: number): Int16Array => new Int16Array(Math.round(seconds * RATE)).fill(level);

describe("recording", () => {
  it("writes a valid 16 kHz mono WAV and measures loudness", () => {
    const wav = pcmToWav(new Int16Array([0, 1000, -1000, 32767]));
    const v = new DataView(wav.buffer), tag = (at: number): string => String.fromCharCode(...wav.slice(at, at + 4));
    expect([tag(0), tag(8), tag(36)]).toEqual(["RIFF", "WAVE", "data"]);
    expect([v.getUint32(4, true), v.getUint16(22, true), v.getUint32(24, true), v.getUint16(34, true), v.getUint32(40, true)]).toEqual([44, 1, 16000, 16, 8]);
    expect([v.getInt16(46, true), v.getInt16(48, true), v.getInt16(50, true)]).toEqual([1000, -1000, 32767]);
    expect(rms(new Int16Array([3, -4, 3, -4]))).toBeCloseTo(Math.sqrt(12.5));
    expect(rms(new Int16Array(0))).toBe(0);
    expect([...floatToInt16(new Float32Array([0, 0.5, -1, 2]))]).toEqual([0, 16384, -32767, 32767]);
  });

  it("stops after 1.5 s of silence, but only once something was said", () => {
    const r = new Recording(120);
    expect(r.feed(tone(2, 0))).toBe(false); // silence before speech never stops it
    expect(r.feed(tone(0.5, 5000))).toBe(false);
    expect(r.feed(tone(1, 0))).toBe(false);
    expect(r.feed(tone(0.3, 5000))).toBe(false); // speaking again resets the pause
    expect(r.feed(tone(1, 0))).toBe(false);
    expect(r.feed(tone(0.5, 0))).toBe(true);
    expect(r.wav()!.length).toBe(44 + Math.round(5.3 * RATE) * 2);
  });

  it("stops at the time limit and refuses a recording too short to transcribe", () => {
    const r = new Recording(2, false);
    expect(r.feed(tone(1.5, 5000))).toBe(false);
    expect(r.feed(tone(0.5, 5000))).toBe(true);
    const short = new Recording();
    short.feed(tone(0.3, 5000));
    expect(short.wav()).toBeNull();
  });
});

describe("spoken answers", () => {
  const rig = (): { s: Speaker<string>; played: string[]; states: boolean[]; halts: number[] } => {
    const played: string[] = [], states: boolean[] = [], halts = [0];
    const s = new Speaker<string>((c) => played.push(c), () => (halts[0]! += 1), (on) => states.push(on));
    return { s, played, states, halts };
  };

  it("plays the clips in order as they arrive and reports speaking until the last one ended", () => {
    const { s, played, states } = rig();
    s.begin(1);
    expect(s.speaking).toBe(true); // waiting for the first sentence already counts
    s.clip(1, "a");
    s.clip(1, "b");
    expect(played).toEqual(["a"]);
    s.ended();
    expect(played).toEqual(["a", "b"]);
    s.done(1);
    expect(s.speaking).toBe(true); // "b" is still playing
    s.ended();
    expect(states).toEqual([true, false]);
  });

  it("is interrupted at once, and clips of the interrupted answer are dropped", () => {
    const { s, played, states, halts } = rig();
    s.begin(1);
    s.clip(1, "a");
    s.stop();
    expect([s.speaking, halts[0]! >= 1]).toEqual([false, true]);
    s.clip(1, "late");
    s.done(1);
    s.ended();
    expect(played).toEqual(["a"]);
    s.begin(2); // a new answer replaces the old one
    s.clip(1, "stale");
    s.clip(2, "x");
    expect(played).toEqual(["a", "x"]);
    s.done(2);
    s.ended();
    expect(states).toEqual([true, false, true, false]);
  });

  it("a failed synthesis with nothing to play ends the speaking state", () => {
    const { s, states } = rig();
    s.begin(1);
    s.done(1);
    expect(states).toEqual([true, false]);
  });
});
