import urllib.request, pathlib, hashlib, json, subprocess, datetime
refs={
'TJA1051':'https://cache.nxp.com/docs/en/data-sheet/TJA1051.pdf',
'VNL5090':'https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf',
'MAX9924':'https://www.analog.com/media/en/technical-documentation/data-sheets/MAX9924-MAX9927.pdf',
'MAX9142':'https://www.analog.com/media/en/technical-documentation/data-sheets/MAX9140-MAX9144.pdf',
'TLE9201':'https://www.infineon.com/dgdl/Infineon-TLE9201SG-DS-v01_05-en.pdf?fileId=db3a30431b3e89eb011ba415a2e24797'
}
log=[]
for name,url in refs.items():
 p=pathlib.Path('/work/experiments/references')/(name+'.pdf')
 try:
  b=urllib.request.urlopen(url,timeout=35).read(); p.write_bytes(b)
  r=subprocess.run(['pdftotext','-layout',str(p),str(p.with_suffix('.txt'))],capture_output=True,text=True)
  log.append(dict(name=name,url=url,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),pdftotext_returncode=r.returncode,stderr=r.stderr))
 except Exception as e:log.append(dict(name=name,url=url,error=str(e)))
pathlib.Path('/work/experiments/references/fetch_log.json').write_text(json.dumps(log,indent=2))
print(json.dumps(log,indent=2))
