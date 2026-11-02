"""Actual Elasticsearch, synthetic vectors/text, isolated random-port test instance."""
import asyncio,json,os,sys,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'api'))
from elasticsearch import AsyncElasticsearch
from app.core.rag import search
from app.core.agent.tools.base import ToolBuildContext
from app.core.agent.tools.builtin.knowledge import _build

async def main():
    address=os.environ['UPGRADE_TEST_ES_ADDRESS']
    if not address.startswith('127.0.0.1:') or address.endswith(':9200'):raise ValueError('Disposable ES port required')
    es=AsyncElasticsearch('http://'+address)
    index='upgrade-test-'+uuid.uuid4().hex
    async def embed(q):return [0.,1.] if q=='astronomy' else [1.,0.]
    client=SimpleNamespace(embed_one=embed)
    try:
        for _ in range(60):
            try:
                if await es.ping():break
            except Exception:pass
            await asyncio.sleep(.5)
        await es.indices.create(index=index,body={'mappings':{'properties':{
            'user_id':{'type':'keyword'},'chunk_type':{'type':'keyword'},'source_id':{'type':'keyword'},
            'chunk_id':{'type':'keyword'},'source_type':{'type':'keyword'},'parent_id':{'type':'keyword'},
            'content':{'type':'text'},'vector':{'type':'dense_vector','dims':2,'index':True,'similarity':'cosine'},
        }}})
        records=[('a','u','sampling points benchmark 30',[1.,0.]),('b','u','bananas fruit',[-1.,0.]),('private','v','sampling points private',[1.,0.])]
        for id,user,content,vector in records:
            await es.index(index=index,id=id,document=dict(user_id=user,chunk_type='child',chunk_id=id,source_id='doc-'+id,source_type='document',doc_name=id,content=content,vector=vector))
        await es.indices.refresh(index=index)
        evidence=[]
        with patch.object(search,'get_es',return_value=es),patch.object(search,'CHUNKS_INDEX',index),patch.object(search,'get_client_for_type',AsyncMock(return_value=client)),patch.object(search,'get_optional_client_for_type',AsyncMock(return_value=None)):
            tool=await _build(ToolBuildContext(session=None,user_id='u',citations=evidence,embed_holder={},stats_holder={}))
            good=json.loads(await tool.ainvoke({'query':'sampling points'}))
            empty=json.loads(await tool.ainvoke({'query':'astronomy'}))
            assert [r['source_id'] for r in good['evidence']]==['doc-a']
            assert empty['status']=='empty' and empty['evidence']==[]
            with patch.object(search,'get_client_for_type',AsyncMock(side_effect=RuntimeError('embedding down'))):
                fallback=await search.hybrid_search(None,'u','sampling points')
                assert fallback[0]['source_id']=='doc-a'
        result=dict(scope='isolated Elasticsearch with synthetic 2D vectors; not real embedding quality',passed=['tenant isolation','low relevance excluded from tool evidence','empty retrieval structured','embedding outage falls back to BM25'],evidence_ids=[e['evidence_id'] for e in good['evidence']])
        Path(__file__).with_name('rag-results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    finally:
        # Only the unique test index in the disposable ES is removed.
        await es.indices.delete(index=index,ignore_unavailable=True)
        await es.close()
asyncio.run(main())
