# Production-density A/B — runbook

Replaces the 15-min B1 file eval (recall-only, against a static key, demonstrably
misleading — see memory feedback_cq_garble_eval_window_artifact) with measurement
against the **live 3-way tees at real contest density**, giving recall AND the
precision/junk number B1 can't.

Reference model (memory feedback_three_way_validation): **SDC = co-located recall
reference** (same RF, hears local QSO partners RBN misses); **SDC ∪ RBN = precision
reference** (a call we spot is confirmed if either has it; confirmed-by-nobody =
junk candidate).

## Two modes

### A. PASSIVE — current config quality at density.  ✅ TURNKEY NOW (no IQ capture)
Measures how our LIVE config scores against SDC∪RBN over a contest window. Needs only
the tees (already built) + the scorer (built). Run at the next CWT (Wed 13/19/03 Z).

1. Start the 3 tees for the window (on any box that can reach all three; skimmer1 works):
   ```
   python3 tools/capture_cluster.py 127.0.0.1            7300 WF8Z /tmp/os.log    &   # ours
   python3 tools/capture_cluster.py 192.168.1.205        7373 WF8Z /tmp/sdc.log   &   # SDC (co-located)
   python3 tools/capture_cluster.py telnet.reversebeacon.net 7000 WF8Z /tmp/rbn.log &   # RBN (worldwide)
   ```
   (Run for the full CWT hour. RBN is a firehose — /tmp/rbn.log grows fast; that's fine.)
2. Stop the tees after the session (`pkill -f capture_cluster.py` — mind the SSH
   self-match trap, use `pgrep -fx`/pidfile if killing over SSH).
3. Score:
   ```
   tools/eval/production_ab_score.py --sdc /tmp/sdc.log --rbn /tmp/rbn.log \
       --ours /tmp/os.log --bands 7,14 --window 19:00:00-20:00:00
   ```
   → current-config RECALL (vs SDC) + PRECISION (vs SDC∪RBN), with the miss list
   and junk-candidate list. **This is the first real precision number we've ever had.**

### B. REPLAY A/B — compare config A vs B on identical real IQ.  ⏳ needs raw-IQ capture
The deterministic, repeatable A/B + the tool that cracks the live-vs-recorded delivery
gap (36 spots/min recorded vs ~5/min live — feedback_decoder_vs_delivery).

1. **Capture** a real contest-density window of raw wideband IQ to disk (the part still
   to build — see below) PLUS the 3 tees for the same wall-clock window.
2. **Replay** the captured IQ through `sparkgap.py --file` under config A and config B.
3. **Score** each replay's spot log against the SAME SDC/RBN reference:
   ```
   tools/eval/production_ab_score.py --sdc sdc.log --rbn rbn.log \
       --ours runA.log runB.log --labels A,B --bands 7
   ```
   → recall/precision delta + which calls each config gained/lost/junked.

## Remaining engineering for mode B (raw-IQ capture + replay)
- **Capture:** `c_capture.c` (repo root) already grabs the HPSDR/Pitaya IQ stream off
  to the side (doesn't touch the production sparkgap process). Extend it to write a
  sustained N-minute capture in a format `run_file_mode` can read. The existing
  SIGUSR1 hook only snapshots ~60s of the FT8 buffer per band — too short.
- **Format bridge:** `run_file_mode` currently reads a WAV (fmt+data chunks, 192k
  24-bit stereo IQ, per the B1 recordings). The capture must emit a compatible
  per-band WAV (or teach file-mode to read the raw HPSDR capture). This is the main
  remaining work item for mode B.
- **Delivery-gap diagnostic (the payoff):** once mode B works, capture a window where
  LIVE only produced ~5 spots/min, replay it through file-mode, and confirm whether
  file-mode gets ~36/min on the *same bytes*. If yes → the gap is in the live IQ
  delivery/scheduling path (multi-thread territory), not the decoder. That's the
  binding pre-CQWW item, finally measurable.

## Status
- Scorer `tools/eval/production_ab_score.py` — **built + unit-validated** (synthetic
  3-source: recall, precision, slash-tolerance, band filter, A/B delta all correct).
- Passive mode — **ready for the next CWT**, zero new code.
- Replay mode — blocked on the raw-IQ capture+format bridge above.
