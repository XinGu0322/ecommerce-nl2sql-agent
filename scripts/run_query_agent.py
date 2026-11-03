
import argparse
import asyncio

from app.runtime import create_query_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="电商 NL2SQL 智能问数 Agent"
    )

    parser.add_argument(
        "question",
        nargs="?",
        help="需要查询的自然语言问题",
    )

    return parser.parse_args()


def display_grouped_rows(
    execution: dict,
    schema_plan: dict,
) -> None:
    rows = execution.get("rows")

    if rows is None:
        return

    metric_id = execution["metric_id"]

    group_fields = (
        schema_plan.get("group_by") or []
    )

    dimension_names = [
        field_id.split(".")[1]
        for field_id in group_fields
    ]

    print("\n分组查询结果：")

    if not rows:
        print("没有符合条件的数据")
        return

    for row in rows:
        if group_fields == [
            "dim_date.year",
            "dim_date.month",
        ]:
            group_label = (
                f"{row['year']}-{row['month']:02d}"
            )
        else:
            group_label = " | ".join(
                f"{name}={row[name]}"
                for name in dimension_names
            )

        print(
            f"{group_label} | "
            f"{metric_id} = {row[metric_id]}"
        )


async def main(question: str) -> int:
    # CLI 与 FastAPI 复用同一套运行时
    async with create_query_runtime() as query_graph:

        result = await query_graph.ainvoke({
            "question": question,
        })

        status = result["status"]

        print("\n========== 智能问数结果 ==========")
        print("用户问题：", question)
        print("执行状态：", status)

        if status == "success":
            execution = result["execution"]
            intent = result["intent"]
            schema_plan = result["schema_plan"]

            print(
                "\n识别指标：",
                intent.get("metric"),
            )

            print(
                "确定指标：",
                schema_plan["metric"]["id"],
            )

            if execution.get("rows") is not None:
                display_grouped_rows(
                    execution,
                    schema_plan,
                )
            else:
                print(
                    "查询结果：",
                    execution["value"],
                )

            print("\n执行 SQL：")
            print(execution["sql"])

            print("\n绑定参数：")
            print(execution["params"])

            return 0

        print("\n未完成查询：")
        print(result.get("error") or "未知原因")

        if status in {"needs_review", "rejected"}:
            return 2

        return 1


if __name__ == "__main__":
    args = parse_args()

    question = (
        args.question
        if args.question is not None
        else input("请输入自然语言查询问题：")
    )

    raise SystemExit(
        asyncio.run(main(question))
    )
