from typing import Annotated, cast

from fastapi import Depends, Request

from app.api.runs import RunManager


def get_manager(request: Request) -> RunManager:
    return cast(RunManager, request.app.state.run_manager)


Manager = Annotated[RunManager, Depends(get_manager)]
