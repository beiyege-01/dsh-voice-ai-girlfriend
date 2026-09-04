/**
 * Embedded copy of hf-realtime-voice/worklets/mic-capture.js (verbatim from
 * the user's project) so the plugin can register it via a Blob URL — plugin
 * bundles are built by tsdown, not Vite, so ?raw imports are unavailable.
 *
 * Processor: resamples the AudioContext rate (typically 48 kHz) to 16 kHz,
 * packs little-endian Int16 PCM, posts ~40 ms chunks; also posts {kind:'level'}
 * RMS per chunk for the main-thread endpointing.
 */
export const MIC_CAPTURE_WORKLET_SOURCE = `
const TARGET_RATE = 16000;
const DEFAULT_CHUNK_MS = 40;
const GATE_ATTACK_MS = 5;
const GATE_HOLD_MS = 250;
const GATE_RELEASE_MS = 80;

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const chunkMs = options?.processorOptions?.chunkMs ?? DEFAULT_CHUNK_MS;
    this._inputRate = sampleRate;
    this._ratio = this._inputRate / TARGET_RATE;
    this._chunkSamples16k = Math.round((TARGET_RATE * chunkMs) / 1000);
    this._scratch = new Float32Array(0);
    this._decimated = new Float32Array(this._chunkSamples16k);
    this._enabled = true;

    this._gateEnabled = false;
    this._thresholdLin = 0;
    this._gateGain = 1;
    this._holdRemaining = 0;
    this._attackCoef = Math.exp(-1 / ((GATE_ATTACK_MS / 1000) * TARGET_RATE));
    this._releaseCoef = Math.exp(-1 / ((GATE_RELEASE_MS / 1000) * TARGET_RATE));
    this._holdSamples = Math.round((GATE_HOLD_MS / 1000) * TARGET_RATE);

    this.port.onmessage = (e) => {
      const data = e.data;
      if (data?.kind === "enable") this._enabled = !!data.value;
      else if (data?.kind === "gate") {
        this._gateEnabled = !!data.enabled;
        this._thresholdLin = data.enabled ? Math.pow(10, data.thresholdDb / 20) : 0;
      }
    };
  }

  _ingest(incoming) {
    if (incoming.length === 0) return;
    const next = new Float32Array(this._scratch.length + incoming.length);
    next.set(this._scratch, 0);
    next.set(incoming, this._scratch.length);
    this._scratch = next;
    this._maybeEmit();
  }

  _maybeEmit() {
    const r = this._ratio;
    const n = this._chunkSamples16k;
    const needIn = Math.ceil(n * r);
    const dec = this._decimated;
    while (this._scratch.length >= needIn) {
      let sumSq = 0;
      if (Math.abs(r - 3) < 1e-6) {
        for (let i = 0; i < n; i++) {
          const idx = i * 3;
          const s = (this._scratch[idx] + this._scratch[idx + 1] + this._scratch[idx + 2]) / 3;
          dec[i] = s;
          sumSq += s * s;
        }
      } else {
        for (let i = 0; i < n; i++) {
          const srcPos = i * r;
          const idx = Math.floor(srcPos);
          const frac = srcPos - idx;
          const a = this._scratch[idx];
          const b = this._scratch[idx + 1] ?? a;
          const s = a + (b - a) * frac;
          dec[i] = s;
          sumSq += s * s;
        }
      }
      const rms = Math.sqrt(sumSq / n);

      let target = 1;
      if (this._gateEnabled) {
        if (rms >= this._thresholdLin) {
          this._holdRemaining = this._holdSamples;
        } else if (this._holdRemaining > 0) {
          this._holdRemaining -= n;
        } else {
          target = 0;
        }
      }

      const out = new Int16Array(n);
      let gain = this._gateGain;
      for (let i = 0; i < n; i++) {
        const coef = target > gain ? this._attackCoef : this._releaseCoef;
        gain = target + (gain - target) * coef;
        const s = dec[i] * gain;
        const clamped = s < -1 ? -1 : s > 1 ? 1 : s;
        out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }
      this._gateGain = gain;

      const consumed = Math.floor(n * r);
      this._scratch = this._scratch.slice(consumed);

      this.port.postMessage({ kind: "level", rms });

      if (this._enabled) {
        this.port.postMessage(out.buffer, [out.buffer]);
      }
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const mono = input[0];
    if (mono.length > 0) this._ingest(mono);
    return true;
  }
}

registerProcessor("mic-capture", MicCaptureProcessor);
`
