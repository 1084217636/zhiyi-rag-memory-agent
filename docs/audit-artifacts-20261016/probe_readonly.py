"""Read-only audit probes: fake models/tools, local fixture reads, no service calls.
Run from api/: .venv/bin/python ../docs/audit-artifacts-20261016/probe_readonly.py
"""
import asyncio
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'api'), str(ROOT / 'api/tests')]
from app.core.agent import orchestrator
from app.core.memory.retrieval.searcher import format_memory_context
from app.core.memory.preprocessing.statement_extractor import extract_statements
from test_agent_orchestrator import FakeModel, FakeTool, FakeChunk, FakeTracer, tool_call
from eval import metrics

async def collect(model, tools):
    with patch.object(orchestrator, 'get_tracer', return_value=FakeTracer()):
        return [e async for e in orchestrator.run_function_calling(model, tools, [])]

async def main():
    out = {'scope': 'fake-model/code-path probes; no real LLM, DB, Redis, ES or Neo4j', 'probes': {}}
    knowledge, memory = FakeTool(), FakeTool(result='memory evidence')
    memory.name = 'memory_search'
    second = {'name':'memory_search','args':{'query':'my topic'},'id':'call-2'}
    model = FakeModel(stream_rounds=[
        [FakeChunk(tool_calls=[tool_call()])],
        [FakeChunk(tool_calls=[second])],
        [FakeChunk('combined answer')],
    ])
    events = await collect(model,[knowledge,memory])
    out['probes']['both_tools_in_successive_rounds'] = {
        'tool_names':[e['tool'] for e in events if e['type']=='tool_result'],
        'final':events[-1]['text'],
    }
    empty = FakeTool(result='知识库中没有检索到相关内容。')
    model = FakeModel(stream_rounds=[
        [FakeChunk(tool_calls=[tool_call()])], [FakeChunk('UNSUPPORTED_CANARY')],
    ])
    events = await collect(model,[empty])
    out['probes']['no_evidence_answer_gate'] = {
        'unsupported_canary_passed': events[-1].get('text') == 'UNSUPPORTED_CANARY',
        'tool_status':next(e['status'] for e in events if e['type']=='tool_result'),
        'note':'Canary supplied by fake model; demonstrates missing server gate, not measured real-model hallucination rate',
    }
    many = FakeTool()
    calls=[tool_call(f'call-{i}',f'query-{i}') for i in range(6)]
    model=FakeModel(stream_rounds=[[FakeChunk(tool_calls=calls)],[FakeChunk('final')]])
    with patch.object(orchestrator,'MAX_TOOL_ITERATIONS',1):
        await collect(model,[many])
    out['probes']['model_round_is_not_tool_count']={'round_limit':1,'executed_tools':many.calls}
    text=format_memory_context([{
        'name':'用户','type':'生命体','confidence':0.9,'relations':[],
        'statements':[{'statement':'用户计划去北京','stmt_type':'PREDICTION','confidence':0.9}],
    }])
    out['probes']['memory_statement_type_lost']={'formatted':text,'has_fact_prefix':'事实：用户计划去北京' in text}
    class BrokenClient:
        async def chat(self,*args,**kwargs): raise RuntimeError('synthetic extraction outage')
    statements=await extract_statements(BrokenClient(),'用户在研究 WiFi CSI')
    out['probes']['extraction_error_indistinguishable_from_empty']={'result':statements}
    from app.core.agent.tracing import get_tracer
    async with get_tracer().span('audit-no-active-root') as span:
        out['probes']['span_without_root']={'is_noop':span.is_noop}
    path=ROOT/'api/eval/results/details-20260903-143301.json'
    if path.exists():
        payload=json.loads(path.read_text())
        out['historical_details_snapshot']=payload
        out['historical_recomputed']={}
        for label,field in [('vector','vector_topk'),('bm25','bm25_topk'),('hybrid','hybrid_topk')]:
            rows=payload['RAG 检索']
            out['historical_recomputed'][label]={
                'n':len(rows),
                'recall@5':metrics.avg([metrics.recall_at_k(r[field],r['gold'],5) for r in rows]),
                'mrr_top5':metrics.avg([metrics.mrr(r[field],r['gold']) for r in rows]),
                'ndcg@5':metrics.avg([metrics.ndcg_at_k(r[field],r['gold'],5) for r in rows]),
            }
        mem=payload.get('记忆检索',[])
        out['historical_memory_bad_cases']=[r for r in mem if r.get('missed')][:3]
    paths=[*sorted((ROOT/'api/eval/fixtures').rglob('*')),
           *sorted((ROOT/'api/eval/results').glob('*.json')),
           *sorted((ROOT/'api/tests').glob('test_*.py')),
           ROOT/'api/app/core/agent/orchestrator.py',ROOT/'api/app/core/rag/search.py']
    out['sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
    out['dependencies']={n:importlib.metadata.version(n) for n in ['langchain','langchain-core','langchain-openai','pydantic','elasticsearch','pytest']}
    target=Path(__file__).with_name('baseline.json')
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'probes':out['probes'],'historical_recomputed':out.get('historical_recomputed'),'baseline_path':str(target)},ensure_ascii=False,indent=2))

if __name__ == '__main__': asyncio.run(main())
