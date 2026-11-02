"""Isolated Neo4j acceptance. URI must explicitly target a disposable test container.
Never runs against configured application databases.
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import logging
logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'api'))
from neo4j import AsyncGraphDatabase
from app.core.memory.graph_models import DialogueNode,ChunkNode,StatementNode
from app.core.memory.governance.service import MemoryRevisionConflict
from app.repositories.neo4j.memory_graph_repository import MemoryGraphRepository

async def main():
    uri=os.environ['UPGRADE_TEST_NEO4J_URI']
    if not uri.startswith('bolt://127.0.0.1:') or uri.endswith(':7687'):
        raise ValueError('Only explicit random-port localhost test URI is allowed')
    driver=AsyncGraphDatabase.driver(uri,auth=('neo4j','upgrade-test-only'))
    results=[]
    try:
        for _ in range(40):
            try:await driver.verify_connectivity();break
            except Exception:await asyncio.sleep(.5)
        async with driver.session() as session:
            for query in [
                'CREATE CONSTRAINT ledger_unique IF NOT EXISTS FOR (n:MemoryLedger) REQUIRE n.user_id IS UNIQUE',
                'CREATE CONSTRAINT ingestion_unique IF NOT EXISTS FOR (n:MemoryIngestion) REQUIRE n.id IS UNIQUE',
                'CREATE CONSTRAINT statement_unique IF NOT EXISTS FOR (n:Statement) REQUIRE n.id IS UNIQUE',
            ]:await (await session.run(query)).consume()
        with patch('app.repositories.neo4j.memory_graph_repository.get_driver',return_value=driver):
            repo=MemoryGraphRepository()
        async def ingest(id,text,action='ADD',targets=(),revision=None,status='active',limit=2,user='test-u'):
            snapshot=await repo.memory_policy_snapshot(user)
            d=DialogueNode(id='d-'+id,user_id=user,content=text)
            c=ChunkNode(id='c-'+id,user_id=user,dialog_id=d.id,content=text)
            s=StatementNode(id=id,user_id=user,chunk_id=c.id,statement=text,embedding=[1.,0.],importance=.7,confidence=.9)
            plan=dict(user_id=user,revision=snapshot['revision'] if revision is None else revision,
                      ingestion_id='ing-'+id,active_limit=limit,plans=[dict(id=id,action=action,targets=list(targets),
                      status=status,memory_kind='FACT',reason='test',confidence=.98,expires_at=None)])
            await repo.save_graph(dialogues=[d],chunks=[c],statements=[s],entities=[],events=[],mentions=[],relations=[],involves=[],policy=plan)
            return plan
        async def scalar(query,**params):
            async with driver.session() as session:return (await (await session.run(query,**params)).single())[0]
        await ingest('s1','用户住北京');results.append('ADD persisted')
        await ingest('s2','用户住北京',action='NOOP',targets=['s1'],status='duplicate')
        assert await scalar("MATCH (:Statement {id:'s2'})-[:SUPPORTS]->(:Statement {id:'s1'}) RETURN count(*)")==1
        results.append('NOOP retains source support')
        await ingest('s3','用户现在住深圳',action='UPDATE',targets=['s1'])
        assert await scalar("MATCH (s:Statement {id:'s1'}) RETURN s.status")=='superseded'
        assert await scalar("MATCH (:Statement {id:'s3'})-[:SUPERSEDES]->(:Statement {id:'s1'}) RETURN count(*)")==1
        results.append('UPDATE retains superseded history')
        await ingest('s4','不明确的新说法',action='CONFLICT',targets=['s3'],status='pending_confirmation')
        found=await repo.search_active_statements('test-u',[1.,0.],10)
        assert 's4' not in [r['id'] for r in found];results.append('pending conflicts excluded from recall')
        await ingest('s5','用户研究CSI');await ingest('s6','用户喜欢茶')
        assert await scalar("MATCH (s:Statement {user_id:'test-u',status:'active'}) RETURN count(s)")==2
        assert await scalar("MATCH (s:Statement {user_id:'test-u',status:'archived'}) RETURN count(s)")==1
        results.append('active quota=2, overflow archived')
        before=await scalar("MATCH (s:Statement) RETURN count(s)")
        await ingest('s6','用户喜欢茶',revision=0)
        assert await scalar("MATCH (s:Statement) RETURN count(s)")==before
        results.append('successful ingestion replay is idempotent')
        try:await ingest('rollback','不能提交',revision=0)
        except MemoryRevisionConflict:pass
        else:raise AssertionError('stale revision accepted')
        assert await scalar("MATCH (s:Statement {id:'rollback'}) RETURN count(s)")==0
        results.append('stale revision rolls back graph write')
        assert await repo.search_active_statements('another-user',[1.,0.],10)==[]
        results.append('cross-user recall isolated')
        # Actual concurrent transactions: one wins, the stale snapshot rolls back.
        outcomes=await asyncio.gather(ingest('race1','a',revision=0,user='race-u'),ingest('race2','b',revision=0,user='race-u'),return_exceptions=True)
        assert sum(isinstance(r,MemoryRevisionConflict) for r in outcomes)==1
        assert await scalar("MATCH (s:Statement {user_id:'race-u'}) RETURN count(s)")==1
        results.append('concurrent same-user writers: one commit, one conflict')
        status=await repo.memory_policy_status('test-u')
        assert any(p['id']=='s4' for p in status['pending'])
        assert not await repo.resolve_memory_statement('another-user','s4','reject')
        assert await repo.resolve_memory_statement('test-u','s4','reject')
        assert await scalar("MATCH (s:Statement {id:'s4'}) RETURN s.status")=='rejected'
        results.append('pending review is user-scoped and can reject')
        async with driver.session() as session:
            await (await session.run("""
MERGE (e:Entity {id:'entity-test',user_id:'test-u',name:'用户'})
WITH e MATCH (s:Statement {id:'s1'}) MERGE (s)-[:MENTIONS]->(e)
MERGE (o:Entity {id:'old-city',user_id:'test-u',name:'北京'})
MERGE (e)-[:RELATION {statement_id:'s1',predicate:'位于'}]->(o)
""")).consume()
        assert await repo.get_entity_supporting_statements('test-u',['entity-test'])==[]
        assert not any(r.get('predicate') for r in await repo.get_entity_neighbors('test-u',['entity-test']))
        results.append('legacy graph reads exclude superseded source statement')
        from app.config import settings
        from app.core.memory.governance.service import MemoryCapacityExceeded
        with patch.object(settings,'memory_graph_node_limit',1):
            try:await ingest('over-cap','too much',user='capacity-u')
            except MemoryCapacityExceeded:pass
            else:raise AssertionError('graph hard cap not enforced')
        assert await scalar("MATCH (s:Statement {id:'over-cap'}) RETURN count(s)")==0
        results.append('graph hard cap rejects transaction without partial write')
        await ingest('replace-old','用户住北京',user='replace-u')
        await ingest('replace-new','用户现住深圳',action='CONFLICT',targets=['replace-old'],status='pending_confirmation',user='replace-u')
        assert await repo.resolve_memory_statement('replace-u','replace-new','replace')
        assert await scalar("MATCH (s:Statement {id:'replace-old'}) RETURN s.status")=='superseded'
        assert await scalar("MATCH (s:Statement {id:'replace-new'}) RETURN s.status")=='active'
        results.append('explicit replace supersedes old fact and activates new fact')
        await ingest('both-old','用户喜欢茶',user='both-u')
        await ingest('both-new','用户喜欢咖啡',action='CONFLICT',targets=['both-old'],status='pending_confirmation',user='both-u')
        assert await repo.resolve_memory_statement('both-u','both-new','keep_both')
        assert await scalar("MATCH (s:Statement {user_id:'both-u',status:'active'}) RETURN count(s)")==2
        results.append('explicit keep_both preserves both facts')
        result={'scope':'isolated disposable Neo4j; no application DB access','passed':results}
        Path(__file__).with_name('memory-graph-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:await driver.close()

asyncio.run(main())
