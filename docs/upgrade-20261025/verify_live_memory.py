"""Live extraction/embedding -> isolated Neo4j -> recall. Synthetic personal data."""
import asyncio,json,logging,os,sys,time,uuid
from pathlib import Path
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'api'));os.chdir(ROOT/'api')
logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
from neo4j import AsyncGraphDatabase
from app.config import settings
from app.core.llm.client import LLMClient,close_llm_client
from app.core.memory.extraction.orchestrator import run_extraction
from app.core.memory.retrieval.searcher import search_memory,format_memory_context
from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository

async def main():
    uri=os.environ['UPGRADE_TEST_NEO4J_URI']
    if not uri.startswith('bolt://127.0.0.1:') or uri.endswith(':7687'):raise ValueError('Disposable Neo4j required')
    driver=AsyncGraphDatabase.driver(uri,auth=('neo4j','upgrade-test-only'))
    chat=LLMClient(settings.eval_chat_base_url,settings.eval_chat_key,settings.eval_chat_model)
    embed=LLMClient(settings.eval_embed_base_url,settings.eval_embed_key,settings.eval_embed_model)
    user='live-test-'+uuid.uuid4().hex;ingestion='source-'+uuid.uuid4().hex
    start=time.monotonic()
    try:
        with patch('app.repositories.neo4j.memory_graph_repository.get_driver',return_value=driver),patch('app.core.memory.extraction.orchestrator._maybe_trigger_reflection',AsyncMock()),patch('app.core.memory.clustering.label_propagation.LabelPropagationEngine.run',AsyncMock(return_value={})):
            stats=await run_extraction(chat_client=chat,embed_client=embed,user_id=user,text='我在研究WiFi CSI。我计划下个月去深圳旅行。',ingestion_id=ingestion)
            repo=MemoryGraphRepository()
            status=await repo.memory_policy_status(user)
            snapshot=await repo.memory_policy_snapshot(user)
            assert stats.statement_count>=2
            assert status['counts'].get('active',0)>=2, status
            assert 'PLAN' in [r['memory_kind'] for r in snapshot['statements']]
            hits=await search_memory(embed_client=embed,user_id=user,query='用户研究什么？计划去哪里？')
            context=format_memory_context(hits)
            assert 'CSI' in context and '计划' in context, context
            # Re-deliver the same task through the full extraction pipeline.
            async with driver.session() as session:
                before=(await (await session.run('MATCH (s:Statement {user_id:$uid}) RETURN count(s)',uid=user)).single())[0]
            await run_extraction(chat_client=chat,embed_client=embed,user_id=user,text='我在研究WiFi CSI。我计划下个月去深圳旅行。',ingestion_id=ingestion)
            async with driver.session() as session:
                after=(await (await session.run('MATCH (s:Statement {user_id:$uid}) RETURN count(s)',uid=user)).single())[0]
            assert before==after
        result=dict(scope='real chat and embedding models + isolated Neo4j; synthetic user; background clustering/reflection mocked',passed=['multiple atomic statements extracted','source-validated active admission','plan type persisted','typed relevant recall','same task redelivery does not add statements'],stats=stats.to_dict(),counts=status['counts'],recalled=context,elapsed_seconds=round(time.monotonic()-start,3))
        Path(__file__).with_name('live-memory-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        await close_llm_client();await driver.close()
async def bounded():
    async with asyncio.timeout(240):await main()
asyncio.run(bounded())
