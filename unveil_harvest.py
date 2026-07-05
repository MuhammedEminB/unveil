#!/usr/bin/env python3
# Unveil şehir paketi hasatçısı — GitHub Actions üzerinde çalışır, Overpass'tan GERÇEK yerleri toplar.
import json, math, re, time, urllib.request, urllib.parse, os, sys

MIRRORS=["https://overpass-api.de/api/interpreter",
         "https://overpass.kumi.systems/api/interpreter",
         "https://overpass.private.coffee/api/interpreter"]
UA={"User-Agent":"UnveilCityPackBuilder/1.0 (hobby project)"}

QCAT={
 'parks_gardens': ['leisure=park','leisure=recreation_ground','leisure=garden','leisure=common',
                   'tourism=picnic_site','leisure=picnic_table','amenity=fountain','landuse=meadow'],
 'beaches_coast': ['natural=beach','natural=cape','natural=bay','natural=strait','man_made=pier',
                   'place=island','place=islet','natural=shoal','natural=reef','natural=spit','natural=isthmus'],
 'mountains_viewpoints': ['tourism=viewpoint','natural=peak','natural=ridge','natural=saddle','natural=cliff',
                          'natural=arete','tourism=alpine_hut','tourism=wilderness_hut','natural=fell',
                          'natural=scree','natural=glacier'],
 'water_wetlands': ['waterway=waterfall','natural=spring','natural=hot_spring','natural=geyser',
                    'natural=wetland','leisure=bird_hide','natural=water'],
 'caves_karst': ['natural=cave_entrance','natural=sinkhole','natural=arch','natural=dune','natural=shaft',
                 'geological=palaeontological_site','geological=outcrop','geological=moraine',
                 'geological=glacial_boulder','geological=erratic','geological=fold','geological=fault'],
 'geological_wonders': ['natural=volcano','natural=saltpan','natural=stone','natural=rock',
                        'geological=monument','natural=crater','geological=hoodoo'],
 'forests_nature_reserves': ['leisure=nature_reserve','boundary=national_park','boundary=protected_area',
                             'natural=wood','landuse=forest','natural=scrub','natural=heath','natural=grassland'],
 'valleys_canyons': ['natural=valley','natural=gorge'],
 'museums_heritage': ['tourism=museum'],
 'castles_forts': ['historic=castle','historic=fort','historic=citadel','historic=castle_wall',
                   'historic=citywalls','historic=city_gate','historic=tower','man_made=watchtower'],
 'archaeological_ruins': ['historic=archaeological_site','historic=ruins','historic=monument','historic=memorial',
                          'historic=fountain','historic=aqueduct','historic=road','man_made=obelisk',
                          'historic=wreck','historic=milestone','historic=boundary_stone',
                          'historic=battlefield','military=bunker'],
 'tombs_necropolis': ['historic=tomb','building=mausoleum'],
 'religious_heritage': ['amenity=place_of_worship','building=mosque','historic=church','historic=chapel',
                        'historic=monastery','historic=wayside_shrine','man_made=cross'],
 'caravanserai_bridges': ['historic=caravanserai','historic=inn','historic=bridge','historic=ford','man_made=viaduct'],
 'traditional_architecture': ['historic=manor','historic=house','building=palace','man_made=windmill',
                              'man_made=watermill','historic=mine'],
 'rural_heritage': ['craft=pottery','craft=weaver','craft=blacksmith','craft=basket_maker',
                    'craft=carpenter','craft=shoemaker','craft=saddler'],
 'underground_rockcut': ['man_made=underground_city'],
 'trails_hiking': ['route=hiking','man_made=cairn'],
 'wildlife_heritage_trees': ['tourism=zoo','tourism=aquarium','natural=tree'],
 'landmarks_panorama': ['tourism=attraction','tourism=artwork','man_made=lighthouse',
                        'man_made=observatory','amenity=planetarium','tourism=camp_site'],
}
BADNAME=re.compile(r'^\d+ |unnamed|unknown')
def norm(n): return re.sub(r'[^\w ]','',n.casefold(),flags=re.UNICODE).replace('the ','',1).strip()

def overpass(q, qtimeout=170):
    data=urllib.parse.urlencode({'data':q}).encode()
    last_err=None
    for m in MIRRORS:
        for attempt in range(2):
            try:
                req=urllib.request.Request(m,data=data,headers=UA)
                with urllib.request.urlopen(req,timeout=qtimeout+30) as r:
                    return json.load(r)
            except Exception as e:
                last_err=e
                print(f"  ! {m} deneme {attempt+1} basarisiz: {e}",flush=True)
                time.sleep(5)
    raise RuntimeError(f"tum aynalar basarisiz, son hata: {last_err}")

def harvest(city):
    """Iki mod destekler:
       1) Merkez+yaricap: {"name","lat","lng","r"}  -> daire, mesafe filtresiyle
       2) Bbox (4 kose):  {"name","s","w","n","e"}   -> dikdortgen, mesafe filtresi yok (bbox zaten sinir)
    """
    is_bbox = all(k in city for k in ('s','w','n','e'))
    name = city['name']

    if is_bbox:
        s,w,n,e = city['s'], city['w'], city['n'], city['e']
        bbox=f"{s},{w},{n},{e}"
        clat=(s+n)/2; clng=(w+e)/2  # sadece bilgi amacli, filtre icin kullanilmiyor
        area_km2 = (n-s)*111.0 * (e-w)*111.320*math.cos(math.radians(abs(clat)))
        print(f"\n=== {name} baslatiliyor (BBOX modu: {bbox}  ~{area_km2:,.0f} km2) ===",flush=True)
        qtimeout = 170  # buyuk alan -> daha uzun sorgu zamani
        def in_area(lat,lng): return True  # bbox zaten kesin sinir, ek mesafe filtresi gereksiz
    else:
        clng,clat,R=city['lng'],city['lat'],city.get('r',25)
        print(f"\n=== {name} baslatiliyor (merkez {clat},{clng}  yaricap {R}km) ===",flush=True)
        dlat=R/111.0; dlng=R/(111.320*math.cos(math.radians(abs(clat))))
        bbox=f"{clat-dlat},{clng-dlng},{clat+dlat},{clng+dlng}"
        qtimeout = 100
        def in_area(lat,lng):
            d=(((lat-clat)*111.0)**2+((lng-clng)*111.320*math.cos(math.radians(abs(clat))))**2)**.5
            return d<=R

    seen_n=set(); seen_c=set(); rows=[]
    CHUNK=5  # buyuk bbox + cok alt-etiket kombinasyonu Overpass'ta sessizce bos donebiliyor; kucuk gruplar guvenli
    for cat,filters in QCAT.items():
        n_before_cat=len(rows)
        raw_total=0
        for ci in range(0, len(filters), CHUNK):
            chunk=filters[ci:ci+CHUNK]
            clauses="".join(f'nwr[{f.split("=")[0]}={json.dumps(f.split("=")[1])}]["name"]({bbox});' for f in chunk)
            q=f'[out:json][timeout:{qtimeout}];({clauses});out center tags;'
            print(f"[{name}] {cat} sorgulaniyor (parca {ci//CHUNK+1}/{(len(filters)-1)//CHUNK+1})...",flush=True)
            try:
                js=overpass(q, qtimeout)
            except Exception as e:
                print(f"  !! {cat} parca {ci//CHUNK+1} atlandi: {e}",flush=True)
                continue
            raw_total+=len(js.get('elements',[]))
            for el in js.get('elements',[]):
                t=el.get('tags',{}); nm=(t.get('name') or '').strip()
                if len(nm)<3 or BADNAME.search(nm): continue
                if cat=='religious_heritage' and t.get('amenity')=='place_of_worship' and t.get('religion')!='muslim': continue
                if cat=='forests_nature_reserves' and t.get('natural')=='wood' and 'name' not in t: continue
                lat=el.get('lat') or el.get('center',{}).get('lat'); lng=el.get('lon') or el.get('center',{}).get('lon')
                if lat is None or not in_area(lat,lng): continue
                kn=norm(nm); kc=(cat,round(lat,3),round(lng,3))
                if kn in seen_n or kc in seen_c: continue
                seen_n.add(kn); seen_c.add(kc)
                s_score=4.2+(0.3 if 'wikidata' in t else 0)+(0.1 if 'wikipedia' in t else 0)
                rows.append([nm,cat,round(lat,4),round(lng,4),round(min(s_score,4.7),1)])
            time.sleep(2)
        print(f"  -> {cat}: ham_eleman:{raw_total} +{len(rows)-n_before_cat} yer (toplam {len(rows)})",flush=True)
        time.sleep(1)
    rows.sort(key=lambda r:(r[1],-r[4]))
    os.makedirs('packs',exist_ok=True)
    out=f"packs/{name}.json"
    if is_bbox:
        payload={'bbox':[s,w,n,e],'count':len(rows),'d':rows}
    else:
        payload={'c':[clng,clat],'r':city.get('r',25),'count':len(rows),'d':rows}
    json.dump(payload,open(out,'w',encoding='utf-8'),ensure_ascii=False)
    print(f"=== {name} TAMAM: {len(rows)} yer -> {out} ===",flush=True)
    return len(rows)

if __name__=='__main__':
    os.makedirs('packs',exist_ok=True)  # klasor HER durumda var olsun (git adim asla patlamasin)
    print("Unveil hasat betigi basladi.",flush=True)
    try:
        with open('cities.json',encoding='utf-8') as f:
            raw=f.read()
        print(f"cities.json okundu ({len(raw)} karakter):",flush=True)
        print(raw,flush=True)
        cities=json.loads(raw)
    except FileNotFoundError:
        print("!! cities.json BULUNAMADI. Repo koklunde olmali. Ankara varsayilanla devam ediliyor.",flush=True)
        cities=[{"name":"Ankara","lat":39.925,"lng":32.854,"r":25}]
    except json.JSONDecodeError as e:
        print(f"!! cities.json BOZUK JSON: {e}. Ankara varsayilanla devam ediliyor.",flush=True)
        cities=[{"name":"Ankara","lat":39.925,"lng":32.854,"r":25}]

    if not cities:
        print("!! cities.json BOS LISTE. Ankara varsayilanla devam ediliyor.",flush=True)
        cities=[{"name":"Ankara","lat":39.925,"lng":32.854,"r":25}]

    print(f"Islenecek sehir sayisi: {len(cities)} -> {[c.get('name') for c in cities]}",flush=True)
    total=0
    for city in cities:
        try:
            total+=harvest(city)
        except Exception as e:
            print(f"!! {city.get('name','?')} icin genel hata, atlaniyor: {e}",flush=True)
    print(f"\nHASAT TAMAMLANDI. Toplam yer: {total}",flush=True)
    if total==0:
        # yine de packs/ klasorunde en az bir iz dosyasi birak ki git add asla basarisiz olmasin
        open('packs/.gitkeep','w').write('no results this run\n')
