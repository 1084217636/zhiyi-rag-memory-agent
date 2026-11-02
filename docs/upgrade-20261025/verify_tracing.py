"""Trace persistence acceptance using a disposable PostgreSQL container."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'api'))
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db.postgres import Base
from app.models.user_model import User
from app.models.agent_trace_model import AgentTrace,AgentSpan
from app.core.agent.tracing import tracer as tracer_module
from app.core.agent.tracing.span_recorder import SpanRecorder

async def main():
    address=os.environ['UPGRADE_TEST_POSTGRES_ADDRESS']
    if not address.startswith('127.0.0.1:') or address.endswith(':5432'):
        raise ValueError('Explicit disposable test port required')
    engine=create_async_engine(f'postgresql+asyncpg://postgres:upgrade-test-only@{address}/upgrade_test')
    maker=async_sessionmaker(engine,expire_on_commit=False)
    async def session_source():
        async with maker() as session:yield session
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda c:Base.metadata.create_all(c,tables=[User.__table__,AgentTrace.__table__,AgentSpan.__table__]))
        uid=uuid.uuid4()
        async with maker() as session:
            session.add(User(id=uid,username='trace-test-'+uid.hex,password_hash='not-a-login'))
            await session.commit()
        recorder=SpanRecorder()
        with patch.object(tracer_module,'get_recorder',return_value=recorder),patch('app.core.agent.tracing.span_recorder.get_session',session_source):
            await recorder.start()
            async with tracer_module.get_tracer().trace(uid,task_type='chat') as trace:
                async with tracer_module.get_tracer().llm_span('fake-model',model_name='test') as span:
                    span.set_tokens(input=100,output=20,model_name='test')
                async with tracer_module.get_tracer().span('retrieval',span_type='tool_call'):
                    pass
                trace_id=trace.trace_id
            await recorder.stop()
        async with maker() as session:
            trace=(await session.execute(select(AgentTrace).where(AgentTrace.trace_id==trace_id))).scalar_one()
            spans=(await session.execute(select(AgentSpan).where(AgentSpan.trace_id==trace_id))).scalars().all()
            assert trace.status=='ok' and trace.total_input_tokens==100 and trace.total_output_tokens==20
            assert len(spans)==2 and all(s.status=='ok' for s in spans)
        result={'scope':'isolated PostgreSQL; fake token usage, not a paid usage measurement',
                'trace_persisted':True,'spans':len(spans),'input_tokens':100,'output_tokens':20,'status':trace.status}
        Path(__file__).with_name('tracing-results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:await engine.dispose()

asyncio.run(main())
