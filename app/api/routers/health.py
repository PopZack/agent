"""健康检查。

阶段 09 部署时会用到它：Docker healthcheck、K8s livenessProbe 都靠这个接口。
现在只报"进程活着"，阶段 03 接入数据库/Redis 后，这里要升级成**依赖就绪检查**
（区分 liveness「进程没死」和 readiness「依赖都通」——这个区别后面细讲）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["system"])


@router.get("/health", summary="存活探针")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
