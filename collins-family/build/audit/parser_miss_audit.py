#!/usr/bin/env python3
"""Read-only audit of the live tree (18 Nov 2026). Input: JSON dump of /api/{people,events,places,notes,citations,media}.
Writes parser_miss_audit_2026-11-18.csv. Changes nothing."""
import json,re,csv,collections,sys
sys.path.insert(0,'/workspace/gramps-web/build')
from lib_parse import EVENT_PREFIX
d=json.load(open(sys.argv[1] if len(sys.argv)>1 else '/tmp/all.json'))
OUT='/workspace/gramps-web/build/audit/parser_miss_audit_2026-11-18.csv'
P={p['handle']:p for p in d['people']}; E={e['handle']:e for e in d['events']}
PL={p['handle']:p for p in d['places']}; N={n['handle']:n for n in d['notes']}
M={m['handle']:m for m in d['media']}; C={c['handle']:c for c in d['citations']}
YR=re.compile(r'(?<!\d)((?:1[5-9]|20)\d{2})(?!\d)')
def strip_paren(s): return re.sub(r'\([^)]*\)?','',s)
def yr(s):
    m=YR.search(strip_paren(s)); return int(m.group(1)) if m else None
def pname(p):
    n=p['primary_name']; return (n['first_name']+' '+' '.join(s['surname'] for s in n['surname_list'])).strip()
def chain(pl):
    out=[pl]; seen={pl['handle']}
    while pl['placeref_list']:
        h=pl['placeref_list'][0]['ref']
        if h in seen or h not in PL: break
        pl=PL[h]; seen.add(h); out.append(pl)
    return out
rows=[]
def add(cat,typ,gid,name,detail,evidence='',fix=''):
    rows.append(dict(category=cat,object_type=typ,gramps_id=gid,name=name,detail=detail,evidence=evidence,suggested_fix=fix))
RANGE=re.compile(r'^(?P<a>[^;]*?)\s*(?:–|—|\s-\s)\s*(?P<b>[^;]*)$')
def chart_facts(lines):
    """-> dict type -> list of (year, raw, form)"""
    f=collections.defaultdict(list)
    for l in lines:
        if l.startswith('~'): continue
        first=l.split(';')[0].strip()
        if not EVENT_PREFIX.match(first):
            m=RANGE.match(strip_paren(first))
            if m and yr(m.group('a')) and (yr(m.group('b')) or not m.group('b').strip()):
                f['Birth'].append((yr(m.group('a')),l,'range-no-prefix'))
                if yr(m.group('b')): f['Death'].append((yr(m.group('b')),l,'range-no-prefix'))
            elif m and not yr(m.group('a')) and yr(m.group('b')) and re.match(r'^\s*(\?|\.\.\.)?\s*$',m.group('a')):
                f['Death'].append((yr(m.group('b')),l,'range-no-prefix'))
        for cl in [c.strip() for c in l.split(';')]:
            m=EVENT_PREFIX.match(cl)
            if not m: continue
            k=m.group('kind').lower()
            t={'b.':'Birth','born':'Birth','d.':'Death','died':'Death','bur.':'Burial','buried':'Burial'}.get(k)
            if not t: continue
            f[t].append((yr(cl[m.end():]),cl,'prefixed'))
    return f
person_chart={}
for p in d['people']:
    lines=[]
    for nh in p['note_list']:
        t=N.get(nh,{}).get('text',{}).get('string','')
        if 'Chart lines:' in t:
            for l in t.split('Chart lines:',1)[1].splitlines():
                l=l.strip()
                if l.startswith('- ') and l[2:] not in lines: lines.append(l[2:])
    person_chart[p['handle']]=lines
def pevents(p,typ):
    return [E[r['ref']] for r in p['event_ref_list'] if r['ref'] in E and E[r['ref']]['type']==typ]
counts=collections.Counter(); ex=collections.defaultdict(list)
def note(cat,s):
    counts[cat]+=1
    if len(ex[cat])<6: ex[cat].append(s)
# (1) chart dates missing / conflicting
for p in d['people']:
    f=chart_facts(person_chart[p['handle']])
    for typ in ('Birth','Death'):
        yrs=[x for x in f.get(typ,[]) if x[0]]
        if not yrs: continue
        evs=pevents(p,typ)
        if not evs:
            cat=f'chart_{typ.lower()}_missing_event'
            add(cat,'person',p['gramps_id'],pname(p),f'chart says {typ.lower()} {yrs[0][0]}; no {typ} event',' | '.join(x[1] for x in yrs),f'add {typ} event from the record behind the chart line')
            note(cat,f"{p['gramps_id']} {pname(p)}: {yrs[0][1]}")
            if any(x[2]=='range-no-prefix' for x in yrs): counts[cat+'__from_range_line']+=1
        else:
            for e in evs:
                ey=e['date']['dateval'][2] if e['date']['dateval'] else 0
                cy={x[0] for x in yrs}
                if ey and all(abs(ey-y)>1 for y in cy):
                    cat=f'event_year_conflicts_chart'
                    add(cat,'event',e['gramps_id'],pname(p),f"{p['gramps_id']} {typ} event year {ey} vs chart {sorted(cy)}",' | '.join(x[1] for x in yrs),'check the record; re-parse ignoring parenthetical years')
                    note(cat,f"{e['gramps_id']} ({p['gramps_id']} {pname(p)}) {typ} {ey} vs chart {sorted(cy)}: {yrs[0][1]}")
                elif not ey:
                    cat='event_no_year_but_chart_has_year'
                    add(cat,'event',e['gramps_id'],pname(p),f"{p['gramps_id']} {typ} event has no year; chart {sorted(cy)}",' | '.join(x[1] for x in yrs),'set the date from the record')
                    note(cat,f"{e['gramps_id']} ({p['gramps_id']} {pname(p)}) {typ}: {yrs[0][1]}")
# (2) events with non-date text
ev_owner={}
for p in d['people']:
    for r in p['event_ref_list']: ev_owner.setdefault(r['ref'],p)
for e in d['events']:
    t=(e['date'] or {}).get('text') or ''
    if not t: continue
    dv=e['date']['dateval']; o=ev_owner.get(e['handle']); on=f"{o['gramps_id']} {pname(o)}" if o else '(family/none)'
    if dv and dv[2]: 
        counts['event_date_text_redundant_with_valid_date']+=1; continue
    if EVENT_PREFIX.match(t) or re.match(r'^(bur|b|d|m)\b',t,re.I): cat='event_date_text_is_raw_clause'
    elif not YR.search(t): cat='event_date_text_no_year'
    else: cat='event_date_text_unparsed_with_year'
    add(cat,'event',e['gramps_id'],on,f"{e['type']}: date text {t!r} dateval {dv}",e['description'],'clear date text; keep raw clause in description; set real date only from a record')
    note(cat,f"{e['gramps_id']} {e['type']} {on}: {t!r}")
# (3) junk place names + (5) hierarchy
use=collections.Counter(e['place'] for e in d['events'] if e['place'])
kids=collections.Counter(r['ref'] for p in d['places'] for r in p['placeref_list'])
JUNK=[('starts_ur',lambda n:re.match(r'^b?ur\.',n)),('paren',lambda n:'(' in n or ')' in n),('aged',lambda n:re.search(r'\baged?\b',n,re.I)),
      ('cert',lambda n:'cert' in n.lower()),('return',lambda n:'return' in n.lower()),('slash',lambda n:'/' in n),
      ('lower_or_punct_start',lambda n:bool(n) and (n[0].islower() or not n[0].isalnum())),('question',lambda n:'?' in n),('or_alternative',lambda n:re.search(r'\bor\b',n))]
for pl in d['places']:
    n=pl['name']['value']; hits=[k for k,f in JUNK if f(n)]
    ch=chain(pl); title=', '.join(x['name']['value'] for x in ch)
    if hits:
        cat='place_junk_name'
        add(cat,'place',pl['gramps_id'],n,f"flags={'+'.join(hits)}; events={use[pl['handle']]}; children={kids[pl['handle']]}; title={pl['title']!r}",'','rename to the real place (strip "ur."/commentary) or merge into the correct place; delete if unused')
        note(cat,f"{pl['gramps_id']} {n!r} ({'+'.join(hits)})")
    probs=[]
    if ch[-1]['place_type']!='Country': probs.append('top_not_country')
    if ',' in n: probs.append('flat_name_with_commas')
    if pl['place_type'] in ('Unknown',''): probs.append('type_unknown')
    if pl['place_type'] in ('City','Town','Village','Borough','Township','Cemetery','Parish') and not any(x['place_type']=='State' or x['place_type']=='Province' for x in ch[1:]) and ch[-1]['name']['value'] not in ('England','France','Germany','Scotland','Ireland','Russia','Crimea','Acadie'):
        probs.append('no_state_or_province')
    if pl['place_type'] in ('City','Town','Village','Borough','Township','Cemetery') and not any(x['place_type']=='County' for x in ch[1:]): probs.append('no_county')
    if pl['title'] and pl['title']!=title: probs.append('title_differs_from_hierarchy')
    if not use[pl['handle']] and not kids[pl['handle']]: probs.append('unused')
    major=[x for x in probs if x in('top_not_country','flat_name_with_commas','no_state_or_province','type_unknown')]
    if major:
        add('place_incomplete_hierarchy','place',pl['gramps_id'],n,f"{'+'.join(probs)}; type={pl['place_type']}; hierarchy={title!r}; events={use[pl['handle']]}",'','enclose in city > county > state/province > country; set full title')
        note('place_incomplete_hierarchy',f"{pl['gramps_id']} {title!r} ({'+'.join(major)})")
    for x in probs:
        counts['place_flag__'+x]+=1
# duplicates by leading name
def key(n): return re.sub(r'^(b?ur\.\s*)','',n.split(',')[0].strip().lower()).rstrip('.')
grp=collections.defaultdict(list)
for pl in d['places']: grp[key(pl['name']['value'])].append(pl)
for k,g in sorted(grp.items()):
    if len(g)>1:
        desc='; '.join(f"{x['gramps_id']} {', '.join(y['name']['value'] for y in chain(x))!r} [{x['place_type']}, events={use[x['handle']]}]" for x in g)
        add('place_possible_duplicate','place',' '.join(x['gramps_id'] for x in g),k,desc,'','review: merge true duplicates (same real place) into one hierarchical place; keep different places (e.g. Washington state vs Washington County, Iowa) apart')
        note('place_possible_duplicate',desc)
# (4) privacy
DEATHKW=re.compile(r'death|died|obituar|headstone|burial|grave|funeral|memorial|cemetery|probate|will\b',re.I)
for p in d['people']:
    f=chart_facts(person_chart[p['handle']])
    strong=[]
    for t in('Death','Burial'):
        for e in pevents(p,t): strong.append(f"{t} event {e['gramps_id']} {e['date']['dateval'][2] or e['date']['text'] or ''}".strip())
        for x in f.get(t,[]): strong.append(f"chart: {x[1]}")
    medium=[M[m['ref']]['desc'] for m in p['media_list'] if m['ref'] in M and DEATHKW.search(M[m['ref']]['desc'] or '')]
    by=None
    for e in pevents(p,'Birth'):
        by=e['date']['dateval'][2] or by
    if not by:
        bys=[x[0] for x in f.get('Birth',[]) if x[0]]; by=bys[0] if bys else None
    if p['private'] and (strong or medium):
        cat='private_but_death_evidence'
        add(cat,'person',p['gramps_id'],pname(p),f'private=true; born {by}',' | '.join(strong+medium)[:600],'read the record; if it proves death, add Death/Burial event and set private=false')
        note(cat,f"{p['gramps_id']} {pname(p)} b.{by}: {(strong+medium)[0][:120]}")
    if not p['private'] and by and by>1926 and not strong and not medium:
        cat='public_possibly_living_born_after_1926'
        add(cat,'person',p['gramps_id'],pname(p),f'private=false; born {by}; no death evidence','','set private=true unless a record proves death')
        note(cat,f"{p['gramps_id']} {pname(p)} b.{by}")
    if not p['private'] and not by and not strong and not medium:
        counts['public_no_birth_year_no_death_evidence']+=1
        rel=[]
        for fam in d['families']:
            members=[fam['father_handle'],fam['mother_handle']]
            kids_=[c['ref'] for c in fam['child_ref_list']]
            if p['handle'] in kids_:
                rel+= [('parent',P[h]) for h in members if h in P]
            if p['handle'] in members:
                rel+= [('spouse',P[h]) for h in members if h in P and h!=p['handle']]+[('child',P[h]) for h in kids_ if h in P]
        def by_of(q):
            for e in pevents(q,'Birth'):
                if e['date']['dateval'][2]: return e['date']['dateval'][2]
        lim={'parent':1900,'spouse':1926,'child':1946}
        hits=[f"{r} {q['gramps_id']} b.{by_of(q)}" for r,q in rel if by_of(q) and by_of(q)>=lim[r]]
        if hits:
            cat='public_possibly_living_inferred_no_birth_date'
            add(cat,'person',p['gramps_id'],pname(p),'private=false; no birth year; relatives suggest a 20th-century birth','; '.join(hits),'set private=true unless a record proves death')
            note(cat,f"{p['gramps_id']} {pname(p)}: {'; '.join(hits)}")
with open(OUT,'w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(json.dumps({'counts':counts,'examples':ex},indent=1,ensure_ascii=False))
print('rows',len(rows),OUT)
# (1b) event year taken only from a parenthetical (e.g. "(1952 cert)", "(12 in 1841)")
extra=[]
for e in d['events']:
    y=e['date']['dateval'][2] if e['date']['dateval'] else 0; desc=e['description'] or ''
    if y and '(' in desc:
        inside=re.findall(r'\(([^)]*)\)',desc); outside=re.sub(r'\([^)]*\)','',desc)
        if any(str(y) in s for s in inside) and str(y) not in outside:
            o=ev_owner.get(e['handle'])
            extra.append(dict(category='event_year_from_parenthetical',object_type='event',gramps_id=e['gramps_id'],name=f"{o['gramps_id']} {pname(o)}" if o else '',detail=f"{e['type']} year {y} appears only inside parentheses",evidence=desc,suggested_fix='use the year outside the parentheses (or no date); keep the parenthetical as a note'))
with open(OUT,'a',newline='') as fh:
    csv.DictWriter(fh,fieldnames=list(rows[0].keys())).writerows(extra)
print('event_year_from_parenthetical',len(extra))
