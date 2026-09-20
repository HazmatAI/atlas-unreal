import json,struct,pathlib,collections,argparse
parser=argparse.ArgumentParser(description='Audit Atlas roots and complete parent groups without creating Unreal assets.')
parser.add_argument('--pack',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
args=parser.parse_args();p=args.pack;args.output.mkdir(parents=True,exist_ok=True)
m=json.loads((p/'manifest.json').read_text()); mats=json.loads((p/'materials.json').read_text()); data=(p/'instances.bin').read_bytes()
fmt={'f32x12':'12f','u32':'I','i32':'i'}
assert len(data)==m['instanceCount']*m['instance']['stride'], 'Instance count/stride mismatch'
rows=[]
for n in range(m['instanceCount']):
 r={f['name']:struct.unpack_from('<'+fmt[f['fmt']],data,n*m['instance']['stride']+f['offset']) for f in m['instance']['fields']}
 r={k:(v[0] if len(v)==1 else v) for k,v in r.items()};r['index']=n;assert 0<=r['meshId']<len(m['meshes']) and 0<=r['rootId']<len(m['roots']);rows.append(r)
def supported(t):return t['role']=='opaque' and t['alphaMode']=='OPAQUE' and t.get('albedo') and t.get('normal') and t.get('doubleSided') and not any(t.get(k) for k in ['vp','detail','parallax','emissive','glassTRS'])
binmesh=(p/'meshes.bin').read_bytes();bounds={}
for mesh in m['meshes']:
 a=[struct.unpack_from('<3f',binmesh,mesh['vtxOffset']+i*m['vertex']['stride']) for i in range(mesh['vtxCount'])];bounds[mesh['id']]=[[min(v[j] for v in a) for j in range(3)],[max(v[j] for v in a) for j in range(3)]]
def stats(rs):
 ids=sorted({r['meshId'] for r in rs});mi=sorted({s['materialId'] for i in ids for s in m['meshes'][i]['submeshes']});lo=[float('inf')]*3;hi=[-float('inf')]*3
 for r in rs:
  b=bounds[r['meshId']];corners=[[b[x][0],b[y][1],b[z][2],1] for x in range(2) for y in range(2) for z in range(2)];v=[[sum(c[k]*r['affine'][j*4+k] for k in range(4)) for j in range(3)] for c in corners];lo=[min(lo[j],min(c[j] for c in v)) for j in range(3)];hi=[max(hi[j],max(c[j] for c in v)) for j in range(3)]
 slots=[s['materialId'] for r in rs for s in m['meshes'][r['meshId']]['submeshes']]
 return dict(count=len(rs),meshes=len(ids),levels=sorted({r['lv'] for r in rs}),bounds=[lo,hi] if rs else None,materials=mi,supported=sum(bool(supported(mats[i])) for i in mi),slotCoverage=round(sum(bool(supported(mats[i])) for i in slots)/len(slots),3) if slots else 0,names=[m['meshes'][i]['name'] for i in ids][:12])
roots=[dict(root=i,name=name,**stats([r for r in rows if r['rootId']==i])) for i,name in enumerate(m['roots'])]
active=[r for r in rows if r['lodIndex']<=0 and not r['flags']&8]
groups=collections.defaultdict(list)
for r in active:
 for key in ['par','par2']:
  if r[key]:groups[(r['rootId'],r['lv'],key,r[key])].append(r)
candidates=[dict(key=k,**stats(rs)) for k,rs in groups.items() if 50<=len(rs)<=200]
selected=[r for r in active if (r['rootId'],r['lv'],r['par2'])==(3,547,9787)]
result=dict(sourceFingerprint=m['sourceFingerprint'],roots=roots,candidates=candidates,selected=dict(stats(selected),instanceIds=[r['index'] for r in selected],meshIds=sorted({r['meshId'] for r in selected})))
(args.output/'labyrinth-root-audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
lines=['# Labyrinthe : audit des racines et secteur pilote','', 'Audit des records complets ; bounds des huit coins des AABB locales, transformes en metres Atlas (Y-up). Les ids des materiaux utilises figurent dans le JSON adjacent. Couverture = slots instancies compatibles avec le master opaque courant, pas qualite visuelle.','', '| Root | Nom | Instances | Meshes | Niveaux | Materiaux supportes/total | Slots supportes | Bounds min / max (m) |','|---|---|---:|---:|---|---:|---:|---|']
for r in roots:
 b=' / '.join(', '.join(f'{v:.2f}' for v in xyz) for xyz in r['bounds']) if r['bounds'] else '-'
 lines.append(f"| {r['root']} | {r['name'] or '(sans racine)'} | {r['count']} | {r['meshes']} | {r['levels']} | {r['supported']}/{len(r['materials'])} | {r['slotCoverage']:.1%} | {b} |")
lines+=['','## Choix','', 'RootId=3, Level=547, GrandparentId=9787 : groupe complet actif hors LOD ou LOD0. 53 instances, 4 meshes, 9 materiaux opaques supportes. Plateformes, escaliers et garde-corps metalliques : ensemble architectural vertical de 3.42 x 22.58 x 3.78 m (axes Atlas). Aucun voisinage spatial ni troncature du groupe.','', 'Les autres petites racines sont des interactifs ou des objets disperses. Les groupes de tuyaux/planches a 100 % de couverture sont souvent disperses ou moins architecturaux. Le groupe retenu couvre plusieurs slots par mesh sans lancer un import massif.','', 'Les ids parent/grandparent sont repl ies et locaux au niveau : les dossiers Unreal encodent racine/niveau/ancetre/parent ; ils ne pretendent pas restaurer la hierarchie GameObject complete.','', 'Materiaux admissibles : role opaque, alpha OPAQUE, double face, albedo et normal presentes ; aucun bloc vp/detail/parallax/emissive ni glassTRS. Les signatures restantes recoivent un fallback magenta dans le commandlet.','', 'LOD superieurs exclus pour eviter la superposition ; associations LOD, collisions et lumieres source differees.','']
(args.output/'labyrinth-root-audit.md').write_text('\n'.join(lines).replace('repl ies','replies'),encoding='utf-8')
print(json.dumps(result['selected'],indent=2))
