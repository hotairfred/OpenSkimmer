#!/usr/bin/env python3
"""Production-density spot scorer — recall + PRECISION against a live reference.

The B1 file eval scores recall against a static curated key and gives NO precision
number. This scores against the live 3-way tees captured at production density, so
it measures both halves of spot quality the way the cluster actually sees us.

REFERENCE MODEL (see memory feedback_three_way_validation):
  • SDC (co-located, same shack/antenna) = the RECALL reference. It hears the same
    RF we do, including local QSO partners + casual ops the worldwide RBN misses.
    Recall = of the CW calls SDC spotted in-window on our bands, how many did WE get.
  • SDC ∪ RBN = the PRECISION / validation reference. A call WE spot is "confirmed"
    if SDC or RBN (anywhere) also spotted it. Our calls confirmed by neither are
    junk candidates (at contest density, overwhelmingly mis-decodes — though a
    genuinely-local-only QSO can land here, hence "candidate").

TWO MODES, same scorer:
  PASSIVE  — one `--ours` (our LIVE spots). Measures the current config's recall +
             precision at real density. Needs only the 3 tees; no IQ capture.
  REPLAY A/B — two `--ours` (config A vs B, file-mode replay of one captured raw-IQ
             window) scored against the SAME SDC/RBN reference. The delta is the
             effect of the config change at production density.

Tee format (tools/capture_cluster.py): "HH:MM:SS DX de <spotter>: <freq> <call> <mode> ..."

USAGE
  # passive (current config quality at density):
  production_ab_score.py --sdc sdc.log --rbn rbn.log --ours os.log
  # replay A/B (config A vs B on the same captured window):
  production_ab_score.py --sdc sdc.log --rbn rbn.log --ours A.log B.log --labels A,B
  # restrict window / bands:
  production_ab_score.py ... --window 21:00:00-21:15:00 --bands 7,14
"""
import argparse, re, sys
from collections import defaultdict

# Same parse as mine_blacklist.py SPOT_RE — keep in sync.
SPOT_RE = re.compile(
    r'^(\d{2}:\d{2}:\d{2}) DX de (\S+?):?\s+([\d.]+)\s+(\S+)\s+(\S+)')

# Spotter callsigns to EXCLUDE from a reference feed (don't let our own spots,
# echoed back through a cluster tee, validate themselves).
def _spotter_base(sp):
    return sp.split('-')[0].upper()

def band_of(freq_khz):
    """40m/20m/etc bucket from kHz. None if outside ham HF."""
    mhz = freq_khz / 1000.0
    for lo, hi, name in [(1.8,2.0,'160'),(3.5,4.0,'80'),(5.3,5.4,'60'),
                         (7.0,7.3,'40'),(10.1,10.15,'30'),(14.0,14.35,'20'),
                         (18.06,18.17,'17'),(21.0,21.45,'15'),(24.89,24.99,'12'),
                         (28.0,29.7,'10')]:
        if lo <= mhz <= hi:
            return name
    return None

def variants(c):
    s = {c}
    if '/' in c:
        for p in c.split('/'):
            if p:
                s.add(p)
    return s

def call_match(a, b):
    """Slash-tolerant call equality."""
    return a == b or bool(variants(a) & variants(b))

def in_window(ts, w0, w1):
    if not w0:
        return True
    return w0 <= ts <= w1

def parse_tee(path, w0, w1, bands, exclude_spotters=None):
    """Return dict: call -> set(bands) for CW spots in window/bands.
    Also returns the raw count of CW spot lines (for rate context)."""
    exclude_spotters = exclude_spotters or set()
    calls = defaultdict(set)
    nlines = 0
    with open(path, errors='replace') as f:
        for ln in f:
            m = SPOT_RE.match(ln)
            if not m:
                continue
            ts, sp, freq, call, mode = m.groups()
            if mode.upper() not in ('CW', 'RTTY'):
                # tees can carry FT8/FT4 from some nodes; CW skimmer A/B is CW.
                if mode.upper() != 'CW':
                    continue
            if _spotter_base(sp) in exclude_spotters:
                continue
            if not in_window(ts, w0, w1):
                continue
            try:
                fk = float(freq)
            except ValueError:
                continue
            b = band_of(fk)
            if b is None:
                continue
            if bands and b not in bands:
                continue
            calls[call.upper()].add(b)
            nlines += 1
    return calls, nlines

def confirmed(call, ref_calls):
    """Is `call` present in a reference call-set (slash-tolerant)?"""
    if call in ref_calls:
        return True
    return any(call_match(call, r) for r in ref_calls)

def score_one(ours, sdc, rbn, label):
    our_set = set(ours)
    sdc_set = set(sdc)
    union = sdc_set | set(rbn)
    # Recall vs SDC (co-located reference)
    got = {c for c in sdc_set if confirmed(c, our_set)}
    missed = sorted(sdc_set - got)
    recall = len(got) / len(sdc_set) if sdc_set else float('nan')
    # Precision vs SDC ∪ RBN
    conf = {c for c in our_set if confirmed(c, union)}
    junk = sorted(our_set - conf)
    precision = len(conf) / len(our_set) if our_set else float('nan')
    return {
        'label': label, 'n_ours': len(our_set), 'n_sdc': len(sdc_set),
        'recall': recall, 'got': len(got), 'missed': missed,
        'precision': precision, 'conf': len(conf), 'junk': junk,
    }

def pct(x):
    return f'{100*x:.1f}%' if x == x else '  n/a'

def main():
    ap = argparse.ArgumentParser(description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ours', nargs='+', required=True,
                    help='our spot tee(s): 1=passive, 2=A/B')
    ap.add_argument('--sdc', required=True, help='SDC (co-located) tee')
    ap.add_argument('--rbn', required=True, help='RBN (worldwide) tee')
    ap.add_argument('--bands', default='', help='comma MHz filter e.g. 7,14 (default: all)')
    ap.add_argument('--window', default='', help='HH:MM:SS-HH:MM:SS')
    ap.add_argument('--labels', default='', help='comma labels for --ours')
    ap.add_argument('--our-spotter', default='WF8Z',
                    help='our spotter base call, excluded from SDC/RBN refs (default WF8Z)')
    ap.add_argument('--show', type=int, default=15, help='max calls to list per bucket')
    args = ap.parse_args()

    w0 = w1 = None
    if args.window:
        w0, w1 = args.window.split('-')
    bands = set(band_of(float(b)*1000) for b in args.bands.split(',') if b) if args.bands else None
    if bands:
        bands.discard(None)
    excl = {args.our_spotter.upper()}

    sdc, sdc_n = parse_tee(args.sdc, w0, w1, bands, exclude_spotters=excl)
    rbn, rbn_n = parse_tee(args.rbn, w0, w1, bands, exclude_spotters=excl)
    labels = args.labels.split(',') if args.labels else [f'ours{i+1}' for i in range(len(args.ours))]

    print('=' * 72)
    print('PRODUCTION-DENSITY SCORE  (recall vs SDC | precision vs SDC∪RBN)')
    print(f'  reference: SDC={len(sdc)} calls ({sdc_n} CW spots), '
          f'RBN={len(rbn)} calls ({rbn_n} CW spots)'
          + (f'  bands={",".join(sorted(bands))}' if bands else '  bands=all')
          + (f'  window={args.window}' if args.window else ''))
    print('=' * 72)

    results = []
    for path, label in zip(args.ours, labels):
        ours, our_n = parse_tee(path, w0, w1, bands)  # don't exclude self here
        r = score_one(ours, sdc, rbn, label)
        r['our_n'] = our_n
        results.append(r)
        print(f'\n[{label}]  ours={r["n_ours"]} calls ({our_n} CW spots)')
        print(f'  RECALL    {pct(r["recall"]):>7}  ({r["got"]}/{r["n_sdc"]} SDC calls)')
        print(f'  PRECISION {pct(r["precision"]):>7}  ({r["conf"]}/{r["n_ours"]} confirmed by SDC∪RBN)')
        if r['missed']:
            print(f'  SDC-but-not-us (recall gaps, {len(r["missed"])}): '
                  + ', '.join(r['missed'][:args.show])
                  + (' …' if len(r['missed']) > args.show else ''))
        if r['junk']:
            print(f'  ours-confirmed-by-nobody (junk candidates, {len(r["junk"])}): '
                  + ', '.join(r['junk'][:args.show])
                  + (' …' if len(r['junk']) > args.show else ''))

    if len(results) == 2:
        a, b = results
        print('\n' + '-' * 72)
        print(f'A/B DELTA  ({a["label"]} → {b["label"]})')
        print(f'  recall    {pct(a["recall"])} → {pct(b["recall"])}  '
              f'(Δ {100*(b["recall"]-a["recall"]):+.1f} pts)')
        print(f'  precision {pct(a["precision"])} → {pct(b["precision"])}  '
              f'(Δ {100*(b["precision"]-a["precision"]):+.1f} pts)')
        gained = sorted(set(b['missed']) ^ set(a['missed']))  # symmetric diff on misses
        a_miss, b_miss = set(a['missed']), set(b['missed'])
        print(f'  recall gained by {b["label"]}: '
              + (', '.join(sorted(a_miss - b_miss)[:args.show]) or 'none'))
        print(f'  recall lost by {b["label"]}:   '
              + (', '.join(sorted(b_miss - a_miss)[:args.show]) or 'none'))
        a_junk, b_junk = set(a['junk']), set(b['junk'])
        print(f'  new junk in {b["label"]}: '
              + (', '.join(sorted(b_junk - a_junk)[:args.show]) or 'none'))
        print(f'  junk cleared by {b["label"]}: '
              + (', '.join(sorted(a_junk - b_junk)[:args.show]) or 'none'))

if __name__ == '__main__':
    main()
