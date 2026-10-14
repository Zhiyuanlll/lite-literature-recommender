"""Adaptive multi-source retrieval until the two requested groups are filled."""
from __future__ import annotations
import time
from . import sources
from .ranking import score,select_targets
from .homepage import enrich

def exposed(paper, previous):
    for old in previous:
        if paper.get('doi') and paper['doi']==old.get('doi'):return True
        if paper.get('arxiv_id') and paper['arxiv_id']==old.get('arxiv_id'):return True
        if paper.get('related_dois') and old.get('doi') in paper['related_dois']:return True
        if sources._same_title_version(paper,old):return True
    return False

def search(profile,counts,policy,run_date,active_sources,cache_dir):
    total=counts['core']+counts['related']; page_size=max(60,min(120,total*4))
    accumulated=[];warnings=[];queries=[];source_counts={};raw=0;waves=[];start=time.monotonic();flat=0;last=0;stop='page_boundary';home_stats={'attempted':0,'abstracts_added':0,'links_added':0,'unavailable':0}
    for wave in range(9):
        request={**profile,'_search_variant':wave%3,'_search_page':profile.get('_search_page',0)+wave//3,'_search_diagnostics':{}}
        found,errors=sources.search(request,limit=page_size,sources=active_sources)
        warnings.extend(errors);d=request['_search_diagnostics'];raw+=d.get('raw_count',len(found));queries.extend(d.get('query_summaries',[]))
        for name,num in d.get('source_counts',{}).items():source_counts[name]=source_counts.get(name,0)+num
        accumulated=sources.merge_candidates(accumulated+found)
        provisional=[score(profile,{**p,'paper_id':str(i)},policy,run_date) for i,p in enumerate(accumulated)]
        for item,p in zip(provisional,accumulated):item['seen']=exposed(p,profile.get('_exclude_papers',[]))
        chosen=select_targets(provisional,counts['core'],counts['related'])
        waves.append({'wave':wave+1,'query_variant':wave%3,'page':request['_search_page'],'new_candidates':len(accumulated)-last,'candidates':len(accumulated)})
        flat=flat+1 if len(accumulated)==last else 0;last=len(accumulated)
        if not found and errors and not any(q.get('status')=='ok' for q in d.get('query_summaries',[])):
            stop='sources_unavailable';break
        if sum(p['recommendation_group']=='core' for p in chosen)>=counts['core'] and sum(p['recommendation_group']=='related' for p in chosen)>=counts['related']:
            stop='groups_filled';break
        if flat>=3:
            stop='no_new_candidates';break
        if time.monotonic()-start>120:
            stop='time_boundary';break
    # Relevance first: do not spend public-homepage requests on unrelated titles.
    provisional=[score(profile,{**p,'paper_id':str(i)},policy,run_date) for i,p in enumerate(accumulated)]
    ordered=sorted(range(len(accumulated)),key=lambda i:(len(provisional[i].get('matched_concepts',[])),provisional[i]['score']),reverse=True)
    missing=[i for i in ordered if not accumulated[i].get('abstract')]
    if missing:
        enriched,home_stats=enrich([accumulated[i] for i in missing],cache_dir,max_attempts=24)
        for i,p in zip(missing,enriched):accumulated[i]=p
    diagnostics=profile.get('_search_diagnostics',{})
    diagnostics.update(raw_count=raw,deduped_count=len(accumulated),deduplicated_count=len(accumulated),duplicate_count=raw-len(accumulated),query_summaries=queries,source_counts=source_counts,errors=list(dict.fromkeys(warnings)),adaptive=True,waves=waves,homepage=home_stats,requested_groups=counts,stop_reason=stop,next_search_page=waves[-1]['page']+1,page=profile.get('_search_page',0),limit=None)
    return accumulated,list(dict.fromkeys(warnings))
