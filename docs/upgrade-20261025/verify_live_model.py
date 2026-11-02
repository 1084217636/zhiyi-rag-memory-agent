"""Live synthetic model acceptance; no application data access. API charges possible."""
import asyncio,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'api'));os.chdir(ROOT/'api')
from app.config import settings
from app.core.llm.client import LLMClient,close_llm_client
from app.core.memory.governance.policy import decide,classify_kind
from app.core.memory.preprocessing.statement_extractor import extract_statements
from app.core.agent.verification import verify_answer

async def main():
    if not (settings.eval_chat_key and settings.eval_chat_base_url and settings.eval_chat_model):raise RuntimeError('Local model configuration missing')
    client=LLMClient(settings.eval_chat_base_url,settings.eval_chat_key,settings.eval_chat_model)
    rows=[];start=time.monotonic()
    try:
        statements=await extract_statements(client,'我在研究WiFi CSI。我现在住在北京，计划下个月去深圳旅行。')
        kinds=[classify_kind(s.statement,s.statement_type) for s in statements]
        rows.append(dict(case='one input, multiple typed memories',passed=len(statements)>=2 and 'PLAN' in kinds,
                         statements=[dict(text=s.statement,kind=k) for s,k in zip(statements,kinds)]))
        d=await decide(client,dict(statement='用户现在搬到深圳居住',memory_kind='FACT'),[dict(id='old-home',statement='用户住在北京',memory_kind='FACT')])
        rows.append(dict(case='explicit residence update',passed=d.action=='UPDATE',decision=d.model_dump()))
        d=await decide(client,dict(statement='用户喜欢咖啡',memory_kind='FACT'),[dict(id='old-drink',statement='用户喜欢喝茶',memory_kind='FACT')])
        rows.append(dict(case='coexisting preferences not overwritten',passed=d.action in {'ADD','CONFLICT'},decision=d.model_dump()))
        evidence=[dict(evidence_id='K1',source_id='synthetic-doc',content='实验使用30个采样点，不涉及部署设备数量。')]
        r=await verify_answer(client,'实验用了多少采样点？','实验使用30个采样点。',evidence,required=True)
        rows.append(dict(case='supported numeric answer',passed=r.status=='verified',result=r.__dict__))
        evidence=[dict(evidence_id='M1',source_id='synthetic-memory',memory_kind='PLAN',content='用户计划下个月去深圳旅行。')]
        r=await verify_answer(client,'我是不是已经去过深圳？','你已经去过深圳。',evidence,required=True)
        rows.append(dict(case='plan not converted to completed event',passed=r.status!='verified' or ('计划' in r.text and any(word in r.text for word in ['无法确认','不能确认','不能确定','无法确定','没有证据','不代表','不是已经'])),result=r.__dict__))
        from app.core.llm.chat_model import build_chat_model, LocalChatConfig
        from app.core.agent.orchestrator import run_function_calling
        from langchain_core.messages import SystemMessage,HumanMessage
        from langchain_core.tools import StructuredTool
        calls=[]
        async def knowledge(query: str) -> str:
            calls.append('knowledge_search')
            return json.dumps({'evidence_id':'K1','content':'文档：实验采用30个采样点。'},ensure_ascii=False)
        async def memory(query: str) -> str:
            calls.append('memory_search')
            return json.dumps({'evidence_id':'M1','memory_kind':'FACT','content':'用户研究WiFi CSI。'},ensure_ascii=False)
        tools=[StructuredTool.from_function(coroutine=knowledge,name='knowledge_search',description='查实验文档中的采样点数量'),
               StructuredTool.from_function(coroutine=memory,name='memory_search',description='查用户记忆中的研究方向')]
        model=build_chat_model(LocalChatConfig(settings.eval_chat_model,settings.eval_chat_base_url,settings.eval_chat_key,('function_call',)),temperature=0,streaming=True)
        events=[e async for e in run_function_calling(model,tools,[SystemMessage(content='涉及文档与个人记忆的两部分都必须调用对应工具核对。只根据工具结果回答。'),HumanMessage(content='文档里实验用了多少采样点？我的研究方向是什么？请分别回答两部分。')])]
        answer=events[-1].get('text','')
        rows.append(dict(case='live Agent selects both tools',passed=set(calls)=={'knowledge_search','memory_search'} and '30' in answer and 'CSI' in answer,calls=calls,answer=answer))
        report=dict(scope='live configured model, synthetic text; not full browser/ingestion E2E',elapsed_seconds=round(time.monotonic()-start,3),passed=sum(r['passed'] for r in rows),total=len(rows),cases=rows)
        Path(__file__).with_name('live-model-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not all(r['passed'] for r in rows):raise RuntimeError('Live model acceptance failed')
    finally:await close_llm_client()
async def bounded():
    async with asyncio.timeout(240):await main()
asyncio.run(bounded())
