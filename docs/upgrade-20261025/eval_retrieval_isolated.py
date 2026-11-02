"""Rerun the fixed 10-doc/22-query retrieval set in a disposable ES instance.
Real configured embeddings; no production index or user data read/write.
"""
import asyncio,json,os,sys,time,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'api'));os.chdir(ROOT/'api')
from elasticsearch import AsyncElasticsearch,helpers
from app.config import settings
from app.core.llm.client import LLMClient,close_llm_client
from app.core.rag.chunker import chunk_parent_child
from app.core.rag.es_index import _MAPPING
from eval import clients,metrics

async def main():
    address=os.environ['UPGRADE_TEST_ES_ADDRESS']
    if not address.startswith('127.0.0.1:') or address.endswith(':9200'):raise ValueError('Disposable ES required')
    es=AsyncElasticsearch('http://'+address);index='retrieval-'+uuid.uuid4().hex
    embed=LLMClient(settings.eval_embed_base_url,settings.eval_embed_key,settings.eval_embed_model)
    start=time.monotonic()
    try:
        await es.indices.create(index=index,body=_MAPPING)
        corpus=sorted((ROOT/'api/eval/fixtures/corpus').glob('*.md'))
        for file in corpus:
            docs=[]
            for i,parent in enumerate(chunk_parent_child(file.read_text())):
                parent_id=f'{file.stem}-p-{i}'
                docs.append(dict(_index=index,_id=parent_id,_source=dict(user_id='audit',source_id=file.stem,chunk_id=parent_id,chunk_type='parent',content=parent.content)))
                vectors=await embed.embed(parent.children)
                for j,(content,vector) in enumerate(zip(parent.children,vectors)):
                    id=f'{file.stem}-c-{i}-{j}'
                    docs.append(dict(_index=index,_id=id,_source=dict(user_id='audit',source_id=file.stem,chunk_id=id,chunk_type='child',parent_id=parent_id,content=content,vector=vector)))
            await helpers.async_bulk(es,docs)
        await es.indices.refresh(index=index)
        gold=json.loads((ROOT/'api/eval/fixtures/gold/retrieval.json').read_text())
        details=[]
        with patch.object(clients,'get_es',return_value=es),patch.object(clients,'CHUNKS_INDEX',index):
            for item in gold:
                q=item['question']
                ranked={'vector':await clients.retrieve_vector(embed,'audit',q),
                        'bm25':await clients.retrieve_bm25('audit',q),
                        'hybrid':await clients.retrieve_hybrid(embed,'audit',q)}
                details.append(dict(question=q,gold=item['relevant_doc_ids'],ranked=ranked))
        summary={}
        for route in ['vector','bm25','hybrid']:
            summary[route]={'recall@5':metrics.avg([metrics.recall_at_k(r['ranked'][route],r['gold'],5) for r in details]),
                            'mrr':metrics.avg([metrics.mrr(r['ranked'][route],r['gold']) for r in details]),
                            'ndcg@5':metrics.avg([metrics.ndcg_at_k(r['ranked'][route],r['gold'],5) for r in details])}
        report=dict(scope='fixed 10-doc/22-query set, real configured embeddings, isolated ES',corpus=len(corpus),queries=len(gold),elapsed_seconds=round(time.monotonic()-start,3),summary=summary,details=details,
                    comparison_note='Historical baseline preserved separately; identical corpus/questions, model version/host/time not controlled, no causal improvement claim.')
        Path(__file__).with_name('retrieval-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='details'},ensure_ascii=False,indent=2))
    finally:
        await es.indices.delete(index=index,ignore_unavailable=True);await es.close();await close_llm_client()
async def bounded():
    async with asyncio.timeout(240):await main()
asyncio.run(bounded())
