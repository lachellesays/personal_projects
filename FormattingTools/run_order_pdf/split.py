import pandas as pd

GAP = 10


def group(level):
    return 'Masters' if level == 'Masters' else ('Senior/Champ' if level in ('Senior', 'Champ', 'Champion') else 'Beginner/Novice')


def load(path):
    df = pd.read_excel(path, engine='calamine').fillna('')
    df.columns = [c.strip() for c in df.columns]
    return [{k: (str(v).strip() if not isinstance(v, (int, float)) else v) for k, v in r.items()} for r in df.to_dict('records')]


def split_classes(rows):
    """Split each (day, event) block into classes: a new class starts where dogs begin repeating,
    moved back to the last place the level or height order restarted."""
    blocks = []
    for r in rows:
        if not blocks or (blocks[-1]['day'], blocks[-1]['event']) != (r['Date'], r['Event']):
            blocks.append({'day': r['Date'], 'event': r['Event'], 'rows': []})
        blocks[-1]['rows'].append(r)
    classes = []
    for b in blocks:
        rs = b['rows']
        diffs = [int(rs[i]['Height']) - int(rs[i - 1]['Height']) for i in range(1, len(rs))]
        trend = 1 if sum(1 for d in diffs if d > 0) >= sum(1 for d in diffs if d < 0) else -1
        cuts, start = [], 0
        while True:
            seen, repeat = set(), None
            for i in range(start, len(rs)):
                key = (rs[i]['Handler Name'], rs[i]['Dog'])
                if key in seen:
                    repeat = i
                    break
                seen.add(key)
            if repeat is None:
                break
            cand = [i for i in range(start + 1, repeat + 1)
                    if group(rs[i]['Level']) != group(rs[i - 1]['Level'])
                    or (int(rs[i]['Height']) - int(rs[i - 1]['Height'])) * trend < 0]
            cut = max(cand) if cand else repeat
            cuts.append(cut)
            start = cut
        bounds = [0] + cuts + [len(rs)]
        parts = [rs[bounds[k]:bounds[k + 1]] for k in range(len(bounds) - 1)]
        for n, part in enumerate(parts, start=1):
            name = b['event'] + (f' {n}' if len(parts) > 1 else '')
            classes.append({'day': b['day'], 'name': name, 'rows': part})
    return classes


def add_gaps(cls):
    last, count = {}, 0
    for i, r in enumerate(cls['rows']):
        r['seq'] = i + 1
        h = r['Handler Name']
        r['gap'] = (i - last[h] - 1) if h in last else None
        r['after'] = (last[h] + 1) if h in last else None
        r['conflict'] = r['gap'] is not None and r['gap'] < GAP
        if r['conflict']:
            cls['rows'][last[h]]['conflict_partner'] = True
        last[h] = i
    cls['conflicts'] = sum(1 for r in cls['rows'] if r['conflict'])
    return cls


if __name__ == '__main__':
    import sys
    for c in split_classes(load(sys.argv[1])):
        add_gaps(c)
        print(c['day'], c['name'], len(c['rows']), 'conflicts', c['conflicts'], '| starts', c['rows'][0]['Level'], c['rows'][0]['Height'])
