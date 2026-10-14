"""Bounded public landing-page metadata, never authenticated scraping."""
from __future__ import annotations
import concurrent.futures
import html
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from .storage import atomic_json, digest, normalized, doi
from .download import _open_response, _validate_url

MAX_HTML = 2 * 1024 * 1024

class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}; self.sections = []; self.stack = []; self.heading = ''
        self.in_heading = False; self.after_abstract = False; self.paragraphs = []
        self.in_p = False; self.current_p = []; self.links = []
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == 'meta':self.meta[(a.get('name') or a.get('property') or '').lower()]=a.get('content','')
        if tag == 'a' and a.get('href') and re.search(r'\.pdf(?:[?#]|$)|/pdfft|/content/pdf/',a['href'],re.I):self.links.append(a['href'])
        if tag in ('h1','h2','h3','h4'):self.in_heading=True;self.heading='';self.after_abstract=False
        if tag == 'p':self.in_p=True;self.current_p=[]
        # Void elements do not affect the section stack.
        if tag not in ('meta','link','img','br','hr','input','source','wbr'):
            active=bool(self.stack and self.stack[-1][1]) or bool(re.search(r'abstract|^abs1$',a.get('id',''),re.I))
            self.stack.append((tag,active))
    def handle_endtag(self,tag):
        if tag in ('h1','h2','h3','h4'):
            self.in_heading=False;self.after_abstract=normalized(self.heading) in ('abstract','摘要')
        if tag == 'p' and self.in_p:
            text=' '.join(self.current_p).strip()
            if self.after_abstract and len(text)>60:self.paragraphs.append(text)
            self.in_p=False
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,text):
        if self.in_heading:self.heading+=text
        if self.in_p:self.current_p.append(text)
        if self.stack and self.stack[-1][1] and not self.in_heading:self.sections.append(text)
    def extract(self,paper,url):
        expected=doi(paper.get('doi',''))
        actual=doi(self.meta.get('citation_doi','') or self.meta.get('dc.identifier',''))
        title=self.meta.get('citation_title','') or self.meta.get('og:title','')
        wanted=set(normalized(paper.get('title','')).split());got=set(normalized(title).split())
        if expected and actual:
            if expected != actual:return {}
        elif not wanted or len(wanted & got)/max(1,len(wanted)) < .85:return {}
        text=' '.join(' '.join(self.sections).split()).strip()
        if text.lower().startswith('abstract'):text=text[8:].strip()
        if len(text)<80:text=' '.join(self.paragraphs)
        if len(text)<80:
            text=self.meta.get('dc.description','') or self.meta.get('description','') or self.meta.get('og:description','')
            words=set(normalized(text).split())
            if len(words & wanted)<min(2,len(wanted)):text=''
        text=' '.join(html.unescape(text).split())
        result={}
        if 80<=len(text)<=12000 and not re.search(r'please enable javascript|access denied|sign in to your account',text,re.I):
            result.update(abstract=text,abstract_source='publisher_homepage',abstract_url=url)
        pdf=self.meta.get('citation_pdf_url','')
        for link in ([pdf] if pdf else [])+self.links:
            try:_validate_url(urljoin(url,link));result['pdf_url']=urljoin(url,link);break
            except ValueError:pass
        return result

def fetch(paper):
    current=paper.get('url') or ('https://doi.org/'+paper['doi'] if paper.get('doi') else '')
    if current.startswith('http://'):current='https://'+current[7:]
    for _ in range(6):
        connection,response=_open_response(current)
        try:
            if response.status in (301,302,303,307,308):
                target=response.getheader('Location')
                if not target:raise ValueError('主页重定向无目标')
                current=urljoin(current,target);continue
            if response.status!=200:raise ValueError('主页HTTP '+str(response.status))
            if 'pdf' in (response.getheader('Content-Type') or '').lower():return {'pdf_url':current}
            raw=response.read(MAX_HTML+1)
            if len(raw)>MAX_HTML:raise ValueError('主页过大')
            parser=PageMetadata();parser.feed(raw.decode('utf-8',errors='replace'))
            return parser.extract(paper,current)
        finally:response.close();connection.close()
    raise ValueError('主页重定向过多')

def enrich(papers,cache_dir,max_attempts=24):
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    attempted=[];stats={'attempted':0,'abstracts_added':0,'links_added':0,'unavailable':0}
    values=[dict(p) for p in papers]
    for i,p in enumerate(values):
        if p.get('abstract') or not (p.get('doi') or p.get('url')):continue
        path=cache_dir/(digest([p.get('doi'),p.get('url'),p.get('title')])+'.json')
        try:
            cached=json.loads(path.read_text())
            if time.time()-cached['at'] < (7*86400 if cached['metadata'] else 3600):
                p.update(cached['metadata']);stats['abstracts_added']+=bool(cached['metadata'].get('abstract'));stats['links_added']+=bool(cached['metadata'].get('pdf_url'));continue
        except (OSError,ValueError,KeyError):pass
        attempted.append((i,path))
        if len(attempted)>=max_attempts:break
    def one(entry):
        i,path=entry
        try:metadata=fetch(values[i])
        except Exception:metadata={}
        atomic_json(path,{'at':time.time(),'metadata':metadata})
        return i,metadata
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for i,metadata in pool.map(one,attempted):
            stats['attempted']+=1;stats['abstracts_added']+=bool(metadata.get('abstract'));stats['links_added']+=bool(metadata.get('pdf_url'));stats['unavailable']+=not bool(metadata);values[i].update(metadata)
    return values,stats
