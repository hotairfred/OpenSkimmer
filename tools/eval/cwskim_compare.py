#!/usr/bin/env python3
"""Compare our file-mode decode of a recording against a CW Skimmer reference.

For the classic benchmark recordings (DK3QN, n6tv, VU2PTT) we have CW Skimmer's
own spot output on the same IQ — the industry-standard reference. This scores our
file-mode run against it at real contest density:

  RECALL    = of CW Skimmer's calls, how many we also got  (skim ∩ ours / skim)
  OUR-EXTRA = calls WE got that CW Skimmer did NOT          (ours − skim)
              (real calls CWSkim missed, OR our junk — inspect; CWSkim is strong
               but not perfect, so extras aren't automatically wrong)
  CWSKIM-ONLY = CW Skimmer got, we missed                   (skim − ours)

Two our-logs → A/B delta (recall + extras), same reference.

Reference format (CW Skimmer telnet/ALL): "DX de CALL-#:  <offset>  <DXCALL>  NN dB ..."
Our format: file-mode log, the "DECODED CALLSIGNS" section (freq kHz CALL nn dB).

USAGE
  cwskim_compare.py --ref cwskimmer_2009scp_spots.txt --ours ours_dk3qn.log
  cwskim_compare.py --ref REF --ours A.log B.log --labels A,B
"""
import argparse, re, sys

REF_RE = re.compile(r'DX de \S+:\s+[-0-9.]+\s+([A-Z0-9/]+)\s+\d+\s*dB', re.I)
OURS_RE = re.compile(r'\d+\.\d\s+kHz\s+(\S+)\s+\d+\s+dB')

def variants(c):
    s = {c}
    if '/' in c:
        for p in c.split('/'):
            if p:
                s.add(p)
    return s

def cmatch(a, b):
    return a == b or bool(variants(a) & variants(b))

def load_ref(path):
    out = set()
    for ln in open(path, errors='replace'):
        m = REF_RE.search(ln)
        if m:
            out.add(m.group(1).upper())
    return out

def load_ours(path):
    out, inb = set(), False
    for ln in open(path, errors='replace'):
        if 'DECODED CALLSIGNS' in ln:
            inb = True; continue
        if inb:
            m = OURS_RE.search(ln)
            if m:
                out.add(m.group(1).upper())
            elif ln.strip() == '':
                inb = False
    return out

def confirmed(call, refset):
    return call in refset or any(cmatch(call, r) for r in refset)

def score(ours, ref):
    got = {c for c in ref if confirmed(c, ours)}
    extra = sorted(c for c in ours if not confirmed(c, ref))
    missed = sorted(ref - got)
    recall = len(got) / len(ref) if ref else float('nan')
    return dict(n_ours=len(ours), n_ref=len(ref), got=len(got),
                recall=recall, extra=extra, missed=missed)

def main():
    ap = argparse.ArgumentParser(description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ref', required=True, help='CW Skimmer reference spot file')
    ap.add_argument('--ours', nargs='+', required=True, help='our file-mode log(s)')
    ap.add_argument('--labels', default='')
    ap.add_argument('--show', type=int, default=25)
    args = ap.parse_args()

    ref = load_ref(args.ref)
    labels = args.labels.split(',') if args.labels else [f'ours{i+1}' for i in range(len(args.ours))]
    print('=' * 72)
    print(f'CW SKIMMER vs US — ref={args.ref}  ({len(ref)} CWSkim calls)')
    print('=' * 72)
    results = []
    for path, label in zip(args.ours, labels):
        ours = load_ours(path)
        r = score(ours, ref); r['label'] = label; r['ours'] = ours
        results.append(r)
        print(f'\n[{label}]  ours={r["n_ours"]} calls')
        print(f'  RECALL vs CWSkim: {100*r["recall"]:.1f}%  ({r["got"]}/{r["n_ref"]})')
        print(f'  our-EXTRA (we got, CWSkim missed: {len(r["extra"])}): '
              + ', '.join(r['extra'][:args.show]) + (' …' if len(r['extra']) > args.show else ''))
        print(f'  CWSkim-ONLY (we missed: {len(r["missed"])}): '
              + ', '.join(r['missed'][:args.show]) + (' …' if len(r['missed']) > args.show else ''))

    if len(results) == 2:
        a, b = results
        print('\n' + '-' * 72)
        print(f'A/B  ({a["label"]} → {b["label"]})')
        print(f'  recall {100*a["recall"]:.1f}% → {100*b["recall"]:.1f}% '
              f'(Δ {100*(b["recall"]-a["recall"]):+.1f} pts)')
        am, bm = set(a['missed']), set(b['missed'])
        print(f'  recall gained: ' + (', '.join(sorted(am - bm)[:args.show]) or 'none'))
        print(f'  recall lost:   ' + (', '.join(sorted(bm - am)[:args.show]) or 'none'))

if __name__ == '__main__':
    main()
