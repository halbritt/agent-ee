import pathlib, urllib.request, json, hashlib, datetime
base=pathlib.Path('/work/experiments/references'); base.mkdir(exist_ok=True)
urls={
'VNL5050.pdf':'https://atta.szlcsc.com/upload/public/pdf/source/20210702/812CDCDABBE6A13FE581F089AB8A7F27.pdf',
'VNL5090.pdf':'https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf',
'Polygonus_README.md':'https://raw.githubusercontent.com/FOME-Tech/Polygonus/b3176cb1de7d0f5b9380ecebc1d007e2f365a6de/README.md',
'Polygonus_mcu.kicad_sch':'https://raw.githubusercontent.com/FOME-Tech/Polygonus/b3176cb1de7d0f5b9380ecebc1d007e2f365a6de/mcu.kicad_sch',
'LCSC.html':'https://item.szlcsc.com/2772332.html'}
log=[]
for name,url in urls.items():
 try:
  with urllib.request.urlopen(url,timeout=15) as r: data=r.read()
  (base/name).write_bytes(data)
  log.append({'url':url,'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 except Exception as e: log.append({'url':url,'file':name,'error':str(e)})
 pathlib.Path('/work/experiments/retrieval.json').write_text(json.dumps(log,indent=2))
 print(json.dumps(log[-1]),flush=True)
