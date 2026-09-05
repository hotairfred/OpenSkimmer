# CW frequency integrity and evidence contract

## Problem

The ITILA path can decode one valid callsign on several widely separated
frequency bins.  Post-hoc selection of the strongest occurrence hides the
symptom but does not establish whether the same keyed waveform reached more
than one channel or whether independent signals were misclassified.

Two concrete defects make that failure dangerous:

1. `SpotTracker.process_intent()` historically synthesized `CQ <call>`.
   The synthetic CQ context let an exact SCP match emit after one decoder
   window, bypassing independent per-frequency evidence.
2. The 192 kHz to 12 kHz decimator used a 32-tap first-stage FIR with only
   about 13.4 dB rejection at 12 kHz.  A strong signal separated by one
   output sample-rate interval could alias into a channel before the narrow
   second-stage filter.

## Required invariants

- An ITILA callsign cannot become a spot from one decode window, including an
  exact SCP match or a peer-supported call.
- Evidence advances consensus only once for each native
  `(receiver bin, window sequence, filter path)` identity.
- Agreement is local to a 500 Hz RF bucket.  Evidence for the same callsign in
  another bucket never advances this bucket.
- The raw decoder text is diagnostic evidence, not reconstructed text.  MQTT
  publishes it with the native window identity before the final spot decision.
- Envelope correlation is telemetry.  It may identify likely channel leakage,
  but it does not silently reject or relocate a spot.
- The first decimator suppresses a tone at the 12 kHz alias frequency by at
  least 80 dB while preserving the CW baseband.

## Native decode-result ABI

The common scanner result record retains the original 288-byte prefix and
appends:

- `window_id`: monotonically increasing sequence for a native scanner session
  (it does not reset when a frequency bin is evicted and recreated);
- `path_hz`: decoder filter path (`100` or `200`);
- `signature_n`: number of valid envelope signature samples;
- `envelope_signature[256]`: mean-pooled and normalized representation of the
  exact envelope passed to the decoder.

Both filter paths for the same drained IQ window share a `window_id`.  They are
different observations of the same RF samples and therefore do not satisfy the
two-window consensus by themselves.

## MQTT contracts

Accepted spots remain on `mqtt.cw_topic` using `sparkgap.spot.v1`, extended
with the evidence identity and correlation summary that justified acceptance.

Every ITILA callsign candidate is also published on
`mqtt.cw_evidence_topic` (default `skimmer/cw/evidence`) as
`sparkgap.cw_evidence.v1`.  It includes raw text, bin/window/path identity,
process-session identity, the current same-bin independent-window count, the
required count, decision, and the strongest recent same-callsign correlation
on another frequency.

The evidence stream is best-effort like spot MQTT.  Broker failure cannot stop
receiver processing or telnet output.

## Correlation interpretation

Pearson correlation is evaluated over the normalized 256-sample envelope
signature with a small lag search.  High correlation on separated RF bins is
evidence that the same keying waveform reached both channels.  Low correlation
means the callsign decoder independently assigned the same text to different
signals/noise.  A value is omitted when no comparable recent signature exists.

## Verification

- Unit tests cover duplicate-window rejection, same-bin repeated-window
  acceptance, cross-frequency isolation, and MQTT evidence fields.
- DSP tests measure the generated FIR response and replay a deterministic
  recorded-IQ fixture through explicit channel probes.  A keyed signal is
  observable at its carrier and suppressed in a channel exactly 12 kHz away.
- Native libraries are rebuilt before restarting a live receiver because the
  result-record ABI changes from 288 to 1328 bytes.
