import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import struct
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from agent_framework import (
    BaseChatClient, ChatResponse, ChatResponseUpdate, Content, FunctionInvocationLayer, Message, ResponseStream,
)
from openpyxl import Workbook
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from agent import CALLER, Caller, chart_text, create_agent
from artifact_storage import ArtifactStore
from chart_service import ChartService
from contracts import ChartBundle, FileChartRequest, FileInput, FileQuery
from file_inputs import FILE_NOTICE, download_file, responses_file
from main import CallerMiddleware
from tabular import FileInputError, MAX_FILE_BYTES, parse_table, decode_file

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "synthetic-sales-2026.csv"
CSV = b"Department,Sales,Expense\nDesign,100,60\nEngineering,200,120\nDesign,50,30\n"


def upload(data=CSV, filename="metrics.csv"):
    return FileInput(filename=filename, file_data="data:text/csv;base64," + base64.b64encode(data).decode())


def payload(data=CSV, filename="metrics.csv"):
    return {
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Chart sales by department"},
            {"type": "input_file", **upload(data, filename).model_dump()},
        ]}],
    }


def excel(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_sample_totals_and_anomaly():
    table = parse_table(SAMPLE.name, SAMPLE.read_bytes())
    rows, summary = table.query(FileQuery(metric="revenue", group_by="region"))
    assert {row.x: row.y for row in rows} == {
        "Americas": 1_816_000, "Asia Pacific": 1_425_000, "Europe": 1_535_000,
    }
    assert sum(row.y for row in rows) == 4_776_000
    assert "Total: 4,776,000.00" in summary
    assert "Used 36 of 36 rows" in summary
    rows, _ = table.query(FileQuery(metric="revenue", group_by="month", filters={"region": ["Europe"]}))
    assert min(rows, key=lambda row: row.y).x == "2026-08"
    assert dict((row.x, row.y) for row in rows)["2026-07"] == 130_000
    assert all(int(row[2]) - int(row[3]) == int(row[4]) for row in table.rows)


@pytest.mark.parametrize("data", [
    CSV, b"\xef\xbb\xbf" + CSV, CSV.replace(b",", b";"), CSV.replace(b",", b"\t"),
])
def test_csv_encodings_and_delimiters(data):
    table = parse_table("test.CSV", data)
    assert table.columns == ("Department", "Sales", "Expense")
    assert table.numeric == ("Sales", "Expense")
    assert table.schema()["rows"] == 3


@pytest.mark.parametrize("aggregation,expected", [
    ("sum", 150), ("mean", 75), ("min", 50), ("max", 100), ("count", 2),
])
def test_aggregation_filters_and_missing_values(aggregation, expected):
    table = parse_table("data.csv", CSV + b"Design,,40\n,999,1\n")
    rows, summary = table.query(FileQuery(
        metric="Sales", group_by="Department", aggregation=aggregation, filters={"Department": ["Design"]},
    ))
    assert [(row.x, row.y) for row in rows] == [("Design", expected)]
    assert "Used 2 of 5 rows; 2 filtered out, 1 omitted" in summary
    assert ("Total:" in summary) == (aggregation in {"sum", "count"})


def test_text_counts_and_generic_series():
    table = parse_table("data.csv", b"Team,Shift,Value\nA,Day,0.1\nA,Day,0.2\nA,Night,-1\nB,Night,4\n")
    rows, _ = table.query(FileQuery(metric="Value", group_by="Team", series="Shift"))
    assert [(row.x, row.series, row.y) for row in rows] == [
        ("A", "Day", 0.3), ("A", "Night", -1), ("B", "Night", 4),
    ]
    rows, _ = table.query(FileQuery(metric="Shift", group_by="Team", aggregation="count"))
    assert [(row.x, row.y) for row in rows] == [("A", 3), ("B", 1)]


@pytest.mark.parametrize("query,match", [
    ({"metric": "Invented", "group_by": "Department"}, "Unknown file column"),
    ({"metric": "Department", "group_by": "Department"}, "numeric"),
    ({"metric": "Sales", "group_by": "Department", "series": "Department"}, "different"),
    ({"metric": "Sales", "group_by": "Department", "filters": {"Department": []}}, "filter"),
    ({"metric": "Sales", "group_by": "Department", "filters": {"Department": ["Unknown"]}}, "No complete"),
])
def test_invalid_queries_are_explicit(query, match):
    with pytest.raises(FileInputError, match=match):
        parse_table("data.csv", CSV).query(FileQuery(**query))


@pytest.mark.parametrize("data,match", [
    (b"A,A\n1,2\n", "unique"),
    (b"A,\n1,2\n", "non-empty"),
    (b"A,B\n1,2,3\n", "same number"),
    (b"A,B\n", "non-empty data"),
    (b"\xff\xfe", "UTF-8"),
    (b"A,B\n\"unterminated,2", "malformed"),
    (b"A,B\n" + b"x" * 257 + b",2\n", "256-character"),
    (b"A,B\n" + b"x,2\n" * 2001, "row"),
    (b"A,B\n" + b"\n" * 2001, "row"),
])
def test_bad_csv_is_rejected_without_echoing_cells(data, match):
    with pytest.raises(FileInputError, match=match):
        parse_table("data.csv", data)


def test_cell_column_group_and_file_budgets():
    with pytest.raises(FileInputError, match="30 columns"):
        parse_table("wide.csv", (",".join(f"c{i}" for i in range(31)) + "\n").encode())
    with pytest.raises(FileInputError, match="20,000-cell"):
        parse_table("cells.csv", (",".join(f"c{i}" for i in range(20)) + "\n" + ("1," * 19 + "1\n") * 1001).encode())
    table = parse_table("groups.csv", ("K,V\n" + "".join(f"{i},1\n" for i in range(257))).encode())
    with pytest.raises(FileInputError, match="256 chart groups"):
        table.query(FileQuery(metric="V", group_by="K"))
    assert decode_file(upload(b"x" * MAX_FILE_BYTES)) == b"x" * MAX_FILE_BYTES
    with pytest.raises(FileInputError, match="1,000,000"):
        decode_file(upload(b"x" * (MAX_FILE_BYTES + 1)))


def test_exact_row_cell_column_and_group_limits_are_accepted():
    assert len(parse_table("rows.csv", b"K,V\n" + b"A,1\n" * 2000).rows) == 2000
    wide = ",".join(f"c{i}" for i in range(30)) + "\n" + ",".join("1" for _ in range(30)) + "\n"
    assert len(parse_table("wide.csv", wide.encode()).columns) == 30
    cells = ",".join(f"c{i}" for i in range(20)) + "\n" + ("1," * 19 + "1\n") * 1000
    assert len(parse_table("cells.csv", cells.encode()).rows) == 1000
    table = parse_table("groups.csv", ("K,V\n" + "".join(f"{i},1\n" for i in range(256))).encode())
    assert len(table.query(FileQuery(metric="V", group_by="K"))[0]) == 256


def test_aggregate_overflow_is_explicit():
    table = parse_table("large.csv", b"K,V\nA,1e308\nB,1e308\n")
    with pytest.raises(FileInputError, match="overall aggregate"):
        table.query(FileQuery(metric="V", group_by="K"))


@pytest.mark.parametrize("value", ["not-a-data-uri", "data:text/csv;base64,%%", "data:text/csv;base64,"])
def test_bad_encoded_file(value):
    with pytest.raises(FileInputError):
        decode_file(FileInput(filename="data.csv", file_data=value))


def test_excel_dates_missing_cells_and_first_worksheet():
    from datetime import date
    data = excel([["Date", "Team", "Value"], [date(2026, 1, 1), "A", 5], [date(2026, 2, 1), "B", None]])
    table = parse_table("data.xlsx", data)
    assert table.worksheet == "Sheet"
    assert table.numeric == ("Value",)
    assert table.rows[0][0].startswith("2026-01-01")
    assert table.rows[1][-1] == ""
    rows, summary = table.query(FileQuery(metric="Value", group_by="Team"))
    assert rows[0].y == 5
    assert "1 omitted" in summary


@pytest.mark.parametrize("value", ["=SUM(1,2)", "#DIV/0!"])
def test_excel_never_evaluates_formulas_or_error_cells(value):
    with pytest.raises(FileInputError, match="formulas and error cells"):
        parse_table("data.xlsx", excel([["Team", "Value"], ["A", value]]))


def test_excel_zip_bomb_and_legacy_files():
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("large.xml", b"0" * 10_000_001)
    with pytest.raises(FileInputError, match="expanded-data"):
        parse_table("data.xlsx", stream.getvalue())
    with pytest.raises(FileInputError, match="readable Excel"):
        parse_table("bad.xlsx", b"not an Excel file")
    for name in ("data.xls", "data.xlsm", "../data.csv"):
        with pytest.raises(FileInputError):
            parse_table(name, CSV)


@pytest.mark.parametrize("url", [
    "http://tenant.sharepoint.com/test.csv", "https://127.0.0.1/test.csv",
    "https://169.254.169.254/latest", "https://tenant.sharepoint.com.evil.test/file.csv",
    "https://user:secret@tenant.sharepoint.com/file.csv", "https://tenant.sharepoint.com:444/file.csv",
    "https://tenant.sharepoint.com:bad/file.csv",
])
async def test_file_download_rejects_untrusted_urls(url):
    with pytest.raises(FileInputError):
        await download_file(url)


async def test_downloads_are_bounded_anonymous_and_do_not_follow_redirects():
    real_client = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=CSV)

    with patch("file_inputs.httpx.AsyncClient", side_effect=lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw,
    )):
        assert await download_file("https://tenant.sharepoint.com/file.csv?sig=private") == CSV
    assert "authorization" not in seen[0].headers
    for response in [
        httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
        httpx.Response(200, content=b"x" * (MAX_FILE_BYTES + 1)),
        httpx.Response(403),
    ]:
        with patch("file_inputs.httpx.AsyncClient", side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(lambda request: response), **kw,
        )):
            with pytest.raises(FileInputError) as error:
                await download_file("https://tenant.sharepoint.com/file.csv?sig=private")
            assert "sig=private" not in str(error.value)


async def test_responses_file_parts_are_replaced_before_the_sdk_or_model_sees_them():
    body = payload()
    table = await responses_file(body)
    assert table.columns == ("Department", "Sales", "Expense")
    assert body["input"][0]["content"][1] == {"type": "input_text", "text": FILE_NOTICE}
    assert base64.b64encode(CSV).decode() not in json.dumps(body)


@pytest.mark.parametrize("part", [
    {"type": "input_file", "file_id": "file-secret"},
    {"type": "input_file", "filename": "file.xls", "file_data": "data:text/csv;base64,QQ=="},
    {"type": "input_file", "filename": "../file.csv", "file_data": "data:text/csv;base64,QQ=="},
    {"type": "input_file", "filename": "file.csv"},
    {"type": "input_file", "filename": "file.csv", "file_url": {"url": "https://invalid"}},
])
async def test_unsupported_response_file_parts_fail_visibly(part):
    with pytest.raises(FileInputError):
        await responses_file({"input": [{"role": "user", "content": [part]}]})


async def test_responses_reject_multiple_or_non_user_attachments():
    body = payload()
    body["input"][0]["content"].append(body["input"][0]["content"][1].copy())
    with pytest.raises(FileInputError, match="exactly one"):
        await responses_file(body)
    body = payload()
    body["input"][0]["role"] = "assistant"
    with pytest.raises(FileInputError, match="user message"):
        await responses_file(body)


async def inspect_caller(scope, receive, send):
    request = Request(scope, receive)
    sanitized = await request.json()
    await asyncio.sleep(0)
    caller = CALLER.get()
    await JSONResponse({
        "filename": caller.table.filename if caller.table else None,
        "schema": caller.table.schema() if caller.table else None,
        "payload": sanitized,
    })(scope, receive, send)


async def test_responses_ingress_isolates_files_and_preserves_no_file_budget():
    app = CallerMiddleware(inspect_caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        results = await asyncio.gather(
            client.post("/responses", json=payload()),
            client.post("/responses", json={"input": "normal"}),
            client.post("/responses", json=payload(b"Other,N\nB,2\n", "other.csv")),
        )
        assert [response.json()["filename"] for response in results] == ["metrics.csv", None, "other.csv"]
        large_csv = b"K,V\n" + b"x" * 180 + b",1\n"
        large_csv = large_csv[:4] + large_csv[4:] * 800
        assert len(large_csv) > 128_000
        response = await client.post("/responses", json=payload(large_csv))
        assert response.status_code == 200
        response = await client.post("/responses", json={"input": "x" * 128_001})
        assert response.status_code == 413
    assert CALLER.get() is None


async def test_bad_ingress_uses_safe_actionable_errors():
    app = CallerMiddleware(inspect_caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = payload(b"Secret,Secret\nprivate-cell,2\n")
        response = await client.post("/responses", json=body)
        assert response.status_code == 422
        assert "unique" in response.json()["error"]
        assert "private-cell" not in response.text


async def test_file_tools_and_real_shared_rendering(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("ARTIFACT_BASE_URL", "http://localhost:8088/artifacts")
    service = ChartService(ArtifactStore())
    agent = create_agent(service, "https://example.services.ai.azure.com/api/projects/test", "test")
    tools = {tool.name: tool for tool in agent.default_options["tools"]}
    table = parse_table("metrics.csv", CSV)
    caller = Caller(table=table)
    token = CALLER.set(caller)
    try:
        with pytest.raises(FileInputError, match="fictional database"):
            await tools["get_data_schema"].invoke(arguments={})
        with pytest.raises(FileInputError, match="fictional database"):
            await tools["create_chart"].invoke(arguments={"request": {"title": "Wrong source"}})
        schema = json.loads((await tools["get_file_schema"].invoke(arguments={}))[0].text)
        assert schema["rows"] == 3
        query = {"metric": "Sales", "group_by": "Department"}
        analysis = json.loads((await tools["analyze_file"].invoke(arguments={"query": query}))[0].text)
        assert "Total: 350.00" in analysis["summary"]
        await tools["create_file_chart"].invoke(arguments={"request": {"title": "Sales by department", "query": query}})
        bundle = caller.bundles[0]
        assert [(row.x, row.y) for row in bundle.rows] == [("Design", 150), ("Engineering", 200)]
        assert ChartBundle.model_validate_json(bundle.model_dump_json()).request.source == "file"
        assert "Synthetic" not in bundle.model_dump_json()
        assert not bundle.adaptive_card["actions"]
        assert "units:" not in json.dumps(bundle.adaptive_card)
        assert "![Chart]" in chart_text(caller.bundles, "static")
        assert "```chart" in chart_text(caller.bundles, "interactive")
        png = next(tmp_path.glob("*.png")).read_bytes()
        assert struct.unpack(">II", png[16:24]) == (960, 540)
        svg = next(tmp_path.glob("*.svg")).read_text()
        assert "Sales by department" in svg and "Engineering" in svg and "sans-serif" in svg
        line = await service.create_file(FileChartRequest(kind="line", query=FileQuery(**query)), table)
        assert not line.adaptive_supported
        assert "category labels" in line.fallback_reason
    finally:
        CALLER.reset(token)
        await service.close()


async def test_file_tools_never_fall_back_to_mock_data(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    service = ChartService(ArtifactStore())
    agent = create_agent(service, "https://example.services.ai.azure.com/api/projects/test", "test")
    tool = next(tool for tool in agent.default_options["tools"] if tool.name == "get_file_schema")
    token = CALLER.set(Caller())
    try:
        with pytest.raises(FileInputError, match="No uploaded table"):
            await tool.invoke(arguments={})
    finally:
        CALLER.reset(token)
        await service.close()


class FileToolClient(FunctionInvocationLayer, BaseChatClient):
    """Run the real Agent Framework tool loop without calling a model."""

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        assert options["reasoning"] == {"summary": "auto"}
        query = {"metric": "Sales", "group_by": "Department"}
        calls = [
            ("get_file_schema", {}),
            ("analyze_file", {"query": query}),
            ("create_file_chart", {"request": {"title": "Uploaded sales", "query": query}}),
        ]
        completed = sum(content.type == "function_result" for message in messages for content in message.contents)
        if completed < len(calls):
            name, arguments = calls[completed]
            contents = [Content("function_call", name=name, arguments=arguments, call_id=f"call-{completed}")]
            reason = "tool_calls"
        else:
            contents = [
                Content.from_text_reasoning(id="rs-file", text="I grouped the uploaded sales by department."),
                Content.from_text("Model prose must not replace the deterministic chart and analysis."),
            ]
            reason = "stop"
        for message in messages:
            assert all(content.type != "uri" for content in message.contents)
            if message.role == "user":
                assert "base64" not in message.text

        if stream:
            async def updates():
                yield ChatResponseUpdate(role="assistant", contents=contents, finish_reason=reason)
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def response():
            return ChatResponse(messages=[Message("assistant", contents)], finish_reason=reason)
        return response()


@pytest.fixture
def file_host(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTS_DEV_MODE", "true")
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("ARTIFACT_BASE_URL", "http://localhost:8088/artifacts")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/test")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "test")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setattr("agent.FoundryChatClient", lambda **kwargs: FileToolClient())
    from main import create_app
    return create_app()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
def test_real_responses_host_file_tool_loop_and_formatting(file_host, stream, extension):
    data = CSV if extension == ".csv" else excel([
        ["Department", "Sales", "Expense"], ["Design", 100, 60], ["Engineering", 200, 120], ["Design", 50, 30],
    ])
    body = payload(data, "metrics" + extension)
    body.update(stream=stream, metadata={"chart_output": "interactive"})
    with TestClient(file_host) as client:
        response = client.post("/responses", json=body)
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data: {")]
        completed = next(event["response"] for event in events if event["type"] == "response.completed")
    else:
        completed = response.json()
    text = "\n".join(
        content["text"] for item in completed["output"] for content in item.get("content", [])
        if content.get("type") == "output_text"
    )
    assert "Total: 350.00" in text
    assert "Model prose must not replace" not in text
    summaries = [
        summary["text"] for item in completed["output"] if item["type"] == "reasoning"
        for summary in item["summary"]
    ]
    assert summaries == ["I grouped the uploaded sales by department."]
    assert "I grouped the uploaded sales" not in text
    if stream:
        assert any(
            event["type"] == "response.reasoning_summary_text.delta"
            and event["delta"] == summaries[0] for event in events
        )
    chart = ChartBundle.model_validate_json(text.split("```chart\n")[1].split("\n```")[0])
    assert chart.request.source == "file"
    assert {row.x: row.y for row in chart.rows} == {"Design": 150, "Engineering": 200}


def test_real_activity_host_file_tool_loop_returns_native_card(file_host):
    with TestClient(file_host) as client:
        response = client.post("/api/messages", json={
            "type": "message", "id": "test-file-message", "channelId": "emulator",
            "serviceUrl": "http://localhost:56150/_connector",
            "from": {"id": "user"}, "recipient": {"id": "bot"},
            "conversation": {"id": "test-file-conversation"}, "deliveryMode": "expectReplies",
            "text": "Chart my sales by department",
            "attachments": [{
                "name": "metrics.csv", "contentType": "text/csv", "contentUrl": upload().file_data,
            }],
        })
    assert response.status_code == 200, response.text
    final = next(activity for activity in reversed(response.json()["activities"]) if activity["type"] == "message")
    assert "Total: 350.00" in final["text"]
    assert "I grouped the uploaded sales" not in final["text"]
    card = final["attachments"][0]["content"]
    assert "Synthetic" not in json.dumps(card)
    assert card["body"][2]["type"] == "Chart.VerticalBar"
