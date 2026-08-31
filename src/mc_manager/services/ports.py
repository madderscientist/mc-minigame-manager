from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from mc_manager.enums import PortState
from mc_manager.errors import ConflictError, ValidationError
from mc_manager.models import PortLease


class PortService:
    def __init__(self, port_min: int, port_max: int) -> None:
        self.port_min = port_min
        self.port_max = port_max

    def reserve(self, session: Session, run_id: str, requested_port: int | None) -> int:
        if requested_port is not None:
            if not self.port_min <= requested_port <= self.port_max:
                raise ValidationError("port_out_of_pool", "指定端口不在预设端口池中")
            candidates = [requested_port]
        else:
            candidates = list(
                session.scalars(
                    select(PortLease.port)
                    .where(
                        PortLease.state == PortState.FREE,
                        PortLease.port >= self.port_min,
                        PortLease.port <= self.port_max,
                    )
                    .order_by(PortLease.port)
                ).all()
            )
        for port in candidates:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(PortLease)
                    .where(PortLease.port == port, PortLease.state == PortState.FREE)
                    .values(
                        state=PortState.RESERVED,
                        run_id=run_id,
                        generation=PortLease.generation + 1,
                        reserved_at=datetime.now(UTC),
                    )
                )
            )
            if result.rowcount == 1:
                return port
        if requested_port is not None:
            raise ConflictError("port_in_use", "指定端口已被占用")
        raise ConflictError("port_pool_exhausted", "没有可用的游戏端口")

    @staticmethod
    def activate(session: Session, run_id: str, generation: int) -> None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(PortLease)
                .where(
                    PortLease.run_id == run_id,
                    PortLease.generation == generation,
                    PortLease.state == PortState.RESERVED,
                )
                .values(state=PortState.ACTIVE)
            )
        )
        if result.rowcount != 1:
            raise ConflictError("port_lease_stale", "端口租约已经失效")

    @staticmethod
    def release(session: Session, run_id: str, generation: int) -> bool:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(PortLease)
                .where(
                    PortLease.run_id == run_id,
                    PortLease.generation == generation,
                    PortLease.state != PortState.FREE,
                )
                .values(
                    state=PortState.FREE,
                    run_id=None,
                    reserved_at=None,
                )
            )
        )
        return result.rowcount == 1
