"""业务工具（全部基于模拟数据）。

硬约束：只用模拟数据，不接任何真实企业系统。

这两个工具特意选成"一个有参数可选、一个必须给参数"，因为参数的**必填/可选**
直接影响模型犯不犯错 —— 这是设计工具时最实际的考虑之一。

另外注意 QueryDepartmentExpense 里那段计算：超支金额和百分比是**代码算的**，
不是让模型算的。数值计算交给代码（确定性、可测），模型只负责把结果讲成人话。
"哪些活交给 LLM、哪些交给传统程序"，这个分工是 JD 明确考察的。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolError, ToolResult

# ── 模拟数据 ────────────────────────────────────────────
# 阶段 06 会用 SQL 查询工具替换掉这两块硬编码的数据。
_DEPARTMENT_EXPENSES: dict[str, dict[str, dict[str, float]]] = {
    "2026-09": {
        "研发部": {"spent": 482000, "budget": 450000},
        "市场部": {"spent": 268000, "budget": 300000},
        "销售部": {"spent": 512000, "budget": 480000},
        "行政部": {"spent": 96000, "budget": 120000},
    },
    "2026-08": {
        "研发部": {"spent": 431000, "budget": 450000},
        "市场部": {"spent": 322000, "budget": 300000},
        "销售部": {"spent": 465000, "budget": 480000},
        "行政部": {"spent": 88000, "budget": 120000},
    },
}

_LEAVE_BALANCE: dict[str, dict[str, float]] = {
    "E1001": {"annual_total": 10.0, "annual_used": 2.5},
    "E1002": {"annual_total": 15.0, "annual_used": 11.0},
    "E1003": {"annual_total": 8.0, "annual_used": 0.0},
}


# ── 工具一：部门费用 ────────────────────────────────────
class QueryDepartmentExpenseArgs(BaseModel):
    month: str = Field(
        description="月份，格式必须为 YYYY-MM，例如 2026-09",
        pattern=r"^\d{4}-\d{2}$",
    )
    department: str | None = Field(
        default=None,
        description="部门名称，例如 研发部。不填则返回该月全部门数据",
    )


class QueryDepartmentExpense(Tool):
    name = "query_department_expense"
    # description 的写法值得注意：先说"能干什么"，再说"什么时候用它"，
    # 最后点明"只读"（让模型知道它没有副作用，可以放心调用）
    description = (
        "查询指定月份各部门的费用与预算，并计算是否超支。"
        "用于回答费用、预算、超支相关的数据问题。只读操作，不修改任何数据。"
    )
    args_model = QueryDepartmentExpenseArgs

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, QueryDepartmentExpenseArgs)  # 由执行器保证
        month_data = _DEPARTMENT_EXPENSES.get(args.month)
        if month_data is None:
            # 注意这是 ToolResult(ok=False) 还是 ToolError？
            # 答："查无此月"是**正常的查询结果**，不是故障 —— 模型应该看到
            # "该月没有数据"，然后决定是纠正月份还是如实告诉用户。
            available = ", ".join(sorted(_DEPARTMENT_EXPENSES))
            return ToolResult(
                ok=False,
                content=f"没有 {args.month} 的费用数据。可用月份：{available}",
                error_code="no_data",
            )

        if args.department is not None and args.department not in month_data:
            available = ", ".join(sorted(month_data))
            return ToolResult(
                ok=False,
                content=f"部门「{args.department}」不存在。该月有数据的部门：{available}",
                error_code="unknown_department",
            )

        selected = {args.department: month_data[args.department]} if args.department else month_data

        rows = []
        over_budget: list[str] = []
        for name, figures in selected.items():
            spent, budget = figures["spent"], figures["budget"]
            over = round(spent - budget, 2)
            rows.append(
                {
                    "department": name,
                    "spent": spent,
                    "budget": budget,
                    "over_budget": over,
                    "over_budget_pct": round(over / budget * 100, 1) if budget else 0.0,
                }
            )
            if over > 0:
                over_budget.append(name)

        rows.sort(key=lambda r: r["over_budget"], reverse=True)
        payload = {
            "month": args.month,
            "departments": rows,
            "over_budget_departments": over_budget,
            "total_spent": round(sum(r["spent"] for r in rows), 2),
        }
        # 给模型的是一段**紧凑 JSON**：既是机器可读的，模型也容易复述。
        # 不用自然语言句子（模型要从句子里抠数字，容易出错），
        # 也不甩原始表结构（字段多、占 token、还有很多和问题无关的列）。
        return ToolResult(
            ok=True,
            content=json.dumps(payload, ensure_ascii=False),
            meta={"row_count": len(rows)},
        )


# ── 工具二：年假余额 ────────────────────────────────────
class GetLeaveBalanceArgs(BaseModel):
    employee_id: str = Field(description="员工工号，例如 E1001")


class GetLeaveBalance(Tool):
    name = "get_leave_balance"
    description = "查询某位员工的年假总额、已用天数和剩余天数。只读操作。"
    args_model = GetLeaveBalanceArgs

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, GetLeaveBalanceArgs)
        record = _LEAVE_BALANCE.get(args.employee_id.upper())
        if record is None:
            known = ", ".join(sorted(_LEAVE_BALANCE))
            # 这里抛 ToolError 而不是返回 ok=False：因为"工号不存在"在业务上
            # 更需要被当成"这次调用无效"来处理，且我希望能演示两条路径的差别。
            # 真实项目里这条线怎么画，取决于你希望模型看到什么 —— 没有标准答案，
            # 但要**有意识地**选，不能随手写。
            raise ToolError(
                f"工号 {args.employee_id} 不存在。可用工号：{known}", code="unknown_employee"
            )

        remaining = round(record["annual_total"] - record["annual_used"], 2)
        payload = {
            "employee_id": args.employee_id.upper(),
            "annual_total": record["annual_total"],
            "annual_used": record["annual_used"],
            "annual_remaining": remaining,
        }
        return ToolResult(ok=True, content=json.dumps(payload, ensure_ascii=False))


def build_default_tools() -> list[Tool]:
    """本阶段内置的工具清单。

    留一个工厂函数而不是一个全局列表：阶段 07 要按用户权限装配不同的工具集，
    那时这里会变成 build_tools_for(user) —— 调用方不用改。
    """
    return [QueryDepartmentExpense(), GetLeaveBalance()]
