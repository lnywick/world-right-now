"""Daily YouTube Data API selector. Requires YOUTUBE_API_KEY secret."""
import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = 'https://www.googleapis.com/youtube/v3/'
API_KEY = os.environ.get('YOUTUBE_API_KEY')
OUT = Path(__file__).parent / 'videos.json'
# Broad public-interest search terms; editorial / source verification is NOT automated.
TOPICS = {
 'war': 'war conflict civilians aftermath shorts news',
 'animals': 'animal welfare factory farming investigation shorts',
 'protests': 'demonstration protest march police news shorts',
 'climate': 'flood wildfire extreme weather climate shorts news',
 'migration': 'refugees migration humanitarian border shorts news',
 'wealth': 'billionaires wealth inequality corporate power shorts news',
}
# Avoid obviously sensational or misleading text, without asserting safe content.
BLOCK = re.compile(r'\b(gore|graphic|uncensored|beheading|execution|dead bodies|fake|ai generated|parody|gameplay|reaction|compilation)\b', re.I)

def api(endpoint, params):
    params = dict(params, key=API_KEY)
    with urlopen(BASE + endpoint + '?' + urlencode(params), timeout=25) as res:
        return json.load(res)

def seconds(iso):
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso or '')
    return (int(m.group(1) or 0)*3600+int(m.group(2) or 0)*60+int(m.group(3) or 0)) if m else 0

def pick(topic, query, used, old):
    since = (dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    results=api('search', {'part':'snippet','q':query,'type':'video','videoEmbeddable':'true','videoDuration':'short','safeSearch':'moderate','order':'date','publishedAfter':since,'maxResults':25})['items']
    ids=[it['id']['videoId'] for it in results if it.get('id',{}).get('videoId')]
    if ids:
        detail=api('videos', {'part':'contentDetails,status,statistics','id':','.join(ids)})['items']
        detail={v['id']:v for v in detail}
        choices=[]
        for rank,it in enumerate(results):
            vid=it.get('id',{}).get('videoId'); val=detail.get(vid)
            if not val or vid in used: continue
            title=it['snippet'].get('title','')
            if BLOCK.search(title+' '+it['snippet'].get('description','')): continue
            if not val.get('status',{}).get('embeddable',False): continue
            length=seconds(val.get('contentDetails',{}).get('duration'))
            if not 15<=length<=90: continue
            if val.get('contentDetails',{}).get('regionRestriction',{}).get('blocked') and 'CH' in val['contentDetails']['regionRestriction']['blocked']: continue
            views=int(val.get('statistics',{}).get('viewCount',0))
            choices.append((rank, -min(views,1000000), {'id':vid,'title':title,'channel':it['snippet'].get('channelTitle',''),'publishedAt':it['snippet'].get('publishedAt','')}))
        # Newest matching clip first; among equally ranked clips views break tie.
        if choices:
            choices.sort(key=lambda p:(p[0],p[1])); chosen=choices[0][2];used.add(chosen['id']);return chosen
    if old and re.fullmatch(r'[A-Za-z0-9_-]{11}',old.get('id','')) and old['id'] not in used:
        used.add(old['id']);return old
    return None

def main():
    if not API_KEY: raise SystemExit('Set YOUTUBE_API_KEY in GitHub Actions Secrets')
    try: previous=json.loads(OUT.read_text())
    except (FileNotFoundError,json.JSONDecodeError): previous={}
    used=set(); videos={}
    for topic,query in TOPICS.items():
        try: videos[topic]=pick(topic,query,used,previous.get('videos',{}).get(topic))
        except Exception as exc:
            print(f'{topic}: API failed: {exc}')
            old=previous.get('videos',{}).get(topic)
            videos[topic]=old if old and old.get('id') not in used else None
            if videos[topic]: used.add(old['id'])
    if not any(videos.values()): raise SystemExit('No feed entries; preserving previously deployed site')
    payload={'updatedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'videos':videos}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print('Selected',sum(bool(x) for x in videos.values()),'clips')

if __name__=='__main__': main()
