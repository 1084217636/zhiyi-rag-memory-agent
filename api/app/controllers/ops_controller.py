from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["operations"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    collector = request.app.state.request_metrics
    return Response(collector.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
